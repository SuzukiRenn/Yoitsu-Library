"""Dedicated, bounded Wenku8 session. Credentials/cookies never enter reports."""
import argparse
from http.cookiejar import MozillaCookieJar
import json
import os
from pathlib import Path
import re
import time
import warnings
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from lxml import html
from scripts import probe_wenku8 as transport
from integrations.wenku8.retry_policy import transient

BASE = transport.BASE
LOGIN = BASE+'/login.php'
LISTING = BASE+'/modules/article/toplist.php?sort=lastupdate'


class SourceStop(ValueError):
    pass


class TransientNetworkError(SourceStop):
    pass


def document(raw):
    match = re.search(br'charset\s*=\s*["\x27]?\s*([A-Za-z0-9_-]+)', raw, re.I)
    encoding = match[1].decode('ascii').lower() if match else 'gb18030'
    if encoding not in {'gbk', 'gb2312', 'gb18030', 'utf-8', 'utf8'}:
        raise SourceStop('source_charset_changed')
    text = raw.decode('gb18030' if encoding.startswith('gb') else 'utf-8', errors='strict')
    return html.fromstring(text, parser=html.HTMLParser(no_network=True, remove_comments=True))


def allowed_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise SourceStop('source_url_outside_scope')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.netloc != 'www.wenku8.net' or parsed.fragment:
        raise SourceStop('source_url_outside_scope')
    if parsed.path == '/robots.txt' and not parsed.query:
        return value
    if parsed.path == '/login.php' and parsed.query in {'', 'do=submit'}:
        return value
    if re.fullmatch(r'/book/[1-9][0-9]{0,7}\.htm', parsed.path) and not parsed.query:
        return value
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.path == '/modules/article/toplist.php' and query.get('sort') == ['lastupdate']
            and set(query) <= {'sort', 'page'} and len(query.get('page', ['1'])) == 1
            and re.fullmatch(r'[1-9][0-9]{0,3}', query.get('page', ['1'])[0])):
        return value
    raise SourceStop('source_url_outside_scope')


def login_form(raw):
    root = document(raw)
    if root.xpath('//input[contains(translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"checkcode") or contains(@name,"captcha")] | //img[contains(@src,"checkcode") or contains(@src,"captcha")]'):
        raise SourceStop('source_captcha_requires_attention')
    forms = root.xpath('//form[.//input[@name="username"] and .//input[@name="password"]]')
    if len(forms) != 1:
        raise SourceStop('source_login_form_changed')
    form = forms[0]
    action = urljoin(LOGIN, form.get('action', ''))
    if form.get('method', '').lower() != 'post' or action != LOGIN+'?do=submit':
        raise SourceStop('source_login_action_changed')
    fields = {}
    for node in form.xpath('.//input[@name]'):
        name = node.get('name')
        if name in fields or not re.fullmatch('[A-Za-z0-9_]{1,50}', name):
            raise SourceStop('source_login_fields_changed')
        if name not in {'username', 'password'} and node.get('type', 'text') not in {'hidden', 'submit'}:
            raise SourceStop('source_login_fields_changed')
        fields[name] = node.get('value', '')
    choices = form.xpath('.//select[@name="usecookie"]/option[contains(normalize-space(.),"一年")]')
    if len(choices) != 1 or not re.fullmatch('[0-9]{1,10}', choices[0].get('value', '')):
        raise SourceStop('source_session_duration_changed')
    fields['usecookie'] = choices[0].get('value')
    return action, fields


class Client:
    def __init__(self, state_dir, credentials, *, sleeper=time.sleep, fetch=transport.fetch,
                 request_limit=500, seconds_limit=3600):
        self.directory = Path(state_dir)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.directory.is_symlink(): raise SourceStop('linked_source_state')
        self.cookies_path = self.directory/'cookies.txt'
        if self.cookies_path.is_symlink(): raise SourceStop('linked_source_cookies')
        self.jar = MozillaCookieJar(str(self.cookies_path))
        if self.cookies_path.is_file():
            try:
                # CookieJar may include a malformed line in its warning/error.
                # Never let a damaged private cookie file reach diagnostics.
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    self.jar.load(ignore_discard=False, ignore_expires=False)
            except (OSError, ValueError):
                raise SourceStop('source_cookie_file_requires_review') from None
        self.credentials = Path(credentials)
        self.opener = build_opener(transport.NoRedirect(), HTTPCookieProcessor(self.jar))
        self.sleeper, self.fetch = sleeper, fetch
        self.request_limit, self.seconds_limit = request_limit, seconds_limit
        self.started = time.monotonic()
        self.requests = []
        self.interval, self.rules, self.redirect = 10, None, ''

    def open(self, request, timeout):
        try:
            return self.opener.open(request, timeout=timeout)
        except HTTPError as error:
            self.redirect = error.headers.get('Location', '')
            raise

    def request(self, url, *, post=None, retry=True):
        allowed_url(url)
        if self.rules is not None and not self.rules.can_fetch(transport.AGENT, url):
            raise SourceStop('source_robots_disallows_request')
        for attempt in range(3 if retry else 1):
            if len(self.requests) >= self.request_limit or time.monotonic()-self.started >= self.seconds_limit:
                raise SourceStop('source_run_budget_reached')
            self.sleeper(self.interval if attempt == 0 else max(self.interval, 30*attempt))
            self.redirect = ''
            client = self
            class PostOpener:
                def open(self, original, timeout):
                    if post is not None:
                        original = Request(url, data=urlencode(post, encoding='gb18030').encode('ascii'),
                            headers={'User-Agent': transport.AGENT, 'Accept-Encoding': 'identity',
                                     'Referer': LOGIN, 'Content-Type': 'application/x-www-form-urlencoded'})
                    return client.open(original, min(30, transport.REQUEST_BUDGET_SECONDS) if post is not None else timeout)
            record, raw = self.fetch(url, transport.ROBOTS_LIMIT if url.endswith('/robots.txt') else transport.DETAIL_LIMIT, PostOpener())
            self.requests.append(record)
            if record.get('status') in {401, 403, 429}:
                raise SourceStop('source_access_restricted')
            if raw is not None or record.get('result') != 'transport_failed':
                return record, raw
            if not transient(record):
                raise SourceStop('source_transport_requires_attention')
        raise TransientNetworkError('source_transport_failed_after_bounded_retries')

    def robots(self):
        record, raw = self.request(BASE+'/robots.txt')
        if raw is None: raise SourceStop('source_robots_unavailable')
        policy, self.rules = transport.policy(raw, record.get('media_type'))
        if self.rules is None or policy['minimum_interval_seconds'] > 60:
            raise SourceStop('source_robots_requires_attention')
        self.interval = max(10, policy['minimum_interval_seconds'])

    def save_session(self):
        cap = int(time.time())+365*86400
        for cookie in self.jar:
            if cookie.expires is not None:
                cookie.expires = min(cookie.expires, cap)
        temporary = self.cookies_path.with_suffix('.pending')
        self.jar.save(str(temporary), ignore_discard=False, ignore_expires=False)
        temporary.chmod(0o600)
        os.replace(temporary, self.cookies_path)

    def listing(self, page=1, *, allow_login=True):
        url = LISTING if page == 1 else LISTING+'&page='+str(page)
        record, raw = self.request(url)
        if raw is None:
            target = urlsplit(urljoin(BASE, self.redirect))
            if (record.get('status') == 302 and target.netloc == 'www.wenku8.net'
                    and target.path == '/login.php' and allow_login):
                self.login()
                return self.listing(page, allow_login=False)
            raise SourceStop('source_listing_requires_attention')
        if record.get('media_type') != 'text/html': raise SourceStop('source_listing_type_changed')
        if not re.search(br'</body\s*>\s*</html\s*>\s*$', raw, re.I):
            raise SourceStop('source_listing_incomplete')
        root = document(raw)
        if root.xpath('//form[.//input[@name="username"] and .//input[@name="password"]]'):
            raise SourceStop('source_session_not_authenticated')
        if not root.xpath('//a[contains(@href,"/book/")]'):
            raise SourceStop('source_listing_unrecognized')
        self.save_session()
        return root

    def login(self):
        record, raw = self.request(LOGIN)
        if raw is None or record.get('media_type') != 'text/html':
            raise SourceStop('source_login_unavailable')
        action, fields = login_form(raw)
        if self.credentials.is_symlink() or self.credentials.stat().st_size > 4096:
            raise SourceStop('source_credentials_invalid')
        credentials = json.loads(self.credentials.read_text(encoding='utf-8'))
        if set(credentials) != {'username', 'password'} or any(not isinstance(v, str) or not v or len(v)>300 for v in credentials.values()):
            raise SourceStop('source_credentials_invalid')
        fields.update(credentials)
        try:
            record, raw = self.request(action, post=fields, retry=False)
        except SourceStop as error:
            if str(error) != 'source_transport_failed_after_bounded_retries':
                raise
            # A timed-out POST may have changed the server session. Verify by GET;
            # never automatically submit the password a second time in this run.
            self.save_session()
            return
        finally:
            fields.clear(); credentials.clear()
        if record.get('status') not in {200, 302}:
            raise SourceStop('source_login_failed')


def probe(state_dir, credentials):
    client = Client(state_dir, credentials, request_limit=10, seconds_limit=600)
    result = {'status': 'needs_attention', 'scope': 'read-only authenticated listing; no database or cover writes'}
    try:
        client.robots()
        root = client.listing()
        links = root.xpath('//a[@href]')
        result.update(status='verified', book_links=sum('/book/' in node.get('href') for node in links),
            pagination=[{'href':node.get('href'), 'text':node.text_content().strip()[:25]} for node in links
                        if 'lastupdate' in node.get('href') or 'page=' in node.get('href')][:25],
            examples=[{'href':node.get('href'), 'tag':node.getparent().tag,
                       'ancestors':[{'tag':parent.tag,'class':parent.get('class',''),
                           'dates':re.findall(r'20\d{2}-\d{2}-\d{2}', parent.text_content())[:8],
                           'book_links':len(parent.xpath('.//a[contains(@href,"/book/")]'))}
                           for parent in list(node.iterancestors())[:4]]}
                      for node in links if '/book/' in node.get('href')][:4],
            persisted_cookie_count=len(client.jar))
    except (SourceStop, OSError, ValueError) as error:
        result['reason'] = str(error) if isinstance(error, SourceStop) else type(error).__name__
    result['request_count'] = len(client.requests)
    result['transports'] = [{key: record.get(key) for key in
        ['url', 'status', 'result', 'phase', 'error_class', 'cause_class', 'elapsed_ms']}
        for record in client.requests]
    Path(state_dir, 'preflight.json').write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result['status']=='verified' else 1


if __name__ == '__main__':
    command = argparse.ArgumentParser()
    command.add_argument('--state-dir', type=Path, required=True)
    command.add_argument('--credentials', type=Path, required=True)
    args = command.parse_args()
    raise SystemExit(probe(args.state_dir, args.credentials))
