"""Bounded intent extraction and local matching over the visible catalog."""
from collections import defaultdict
import json
import logging
import re
from secrets import token_hex
from threading import BoundedSemaphore
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.db import OperationalError
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from . import catalog, public_booklist, services
from .catalog_views import page, read_limit
from .course_common import clean, page_error
from .models import WorkAlias
from .views import database_unavailable

logger = logging.getLogger(__name__)
model_slot = BoundedSemaphore(1)
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'
PROMPT = (
    '将读者的找书请求解析为 json 对象，只允许 keywords、tags、exclude 三个数组。'
    '每个数组最多4项，每项是中文或英文短词，最多24字。keywords 是可识别的书名、作者、题材或情节短词，'
    'tags 是题材标签，exclude 是不希望出现的词。无法确定的数组留空。不要生成书名或作品推荐，不要解释。'
    '例如：{"keywords":["魔法","学院"],"tags":["奇幻"],"exclude":["恐怖"]}'
)
PRIVATE_INPUT = re.compile(r'\S+@\S+|\d{11,}|(?:sk-|Bearer +)[A-Za-z0-9_-]{8,}', re.I)
FIND_PATH = '/find-books/'
SEARCH_COOKIE = 'course_booklist_find'
SEARCH_TTL = 3600
PER_PAGE = 5
SEARCH_ID = re.compile('[0-9a-f]{32}')
BROWSER_ID = re.compile('[0-9a-f]{48}')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def extract(query):
    payload = {'model': settings.COURSE_DEEPSEEK_MODEL, 'max_tokens': 384,
               'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'},
               'messages': [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': query}]}
    request = Request(DEEPSEEK_URL, data=json.dumps(payload).encode('utf-8'), headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + settings.COURSE_DEEPSEEK_KEY}, method='POST')
    if not model_slot.acquire(blocking=False):
        raise ValueError('Model request already in flight')
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            raw = response.read(32769)
    finally:
        model_slot.release()
    if len(raw)>32768:
        raise ValueError('Oversize response')
    result = json.loads(raw)
    choice = result['choices'][0]
    content = choice['message']['content']
    if choice.get('finish_reason') != 'stop' or not isinstance(content, str) or not content.strip():
        raise ValueError('Incomplete or blank JSON')
    intent = json.loads(content)
    if not isinstance(intent, dict) or set(intent) != {'keywords', 'tags', 'exclude'}:
        raise ValueError('Unknown intent shape')
    normalized = {}
    for field, values in intent.items():
        if not isinstance(values, list) or len(values)>4:
            raise ValueError('Invalid field size')
        terms = []
        for value in values:
            if not isinstance(value, str) or not 1<=len(value.strip())<=24 or any(ord(char)<32 for char in value):
                raise ValueError('Invalid term')
            terms.append(value.strip())
        normalized[field] = list(dict.fromkeys(terms))
    if not normalized['keywords'] and not normalized['tags']:
        raise ValueError('No positive terms')
    return normalized


def excerpt(description, key):
    # Preserve spelling and punctuation from the actual displayed introduction.
    for sentence in re.split(r'(?<=[。！？!?])|\n', description):
        if key not in catalog.search_key(sentence):
            continue
        if len(sentence) <= 140:
            return sentence.strip()
        normalized = [catalog.search_key(character) for character in sentence]
        start = ''.join(normalized).find(key)
        if start < 0:
            return sentence.strip()
        consumed = 0
        for index, character in enumerate(normalized):
            consumed += len(character)
            if consumed > start:
                return sentence[max(0, index-35):index+len(key)+65].strip()
    # A match crossing two adjacent sentences still has original-source evidence.
    return description.strip()


def entered_intent(post):
    intent = {}
    for field in ('keywords', 'tags', 'exclude'):
        values = [value.strip() for value in re.split('[、,，]', post.get(field, '')) if value.strip()]
        if len(values)>4 or any(len(value)>24 for value in values):
            raise ValidationError('每组最多 4 个词，每词最多 24 字。')
        intent[field] = list(dict.fromkeys(values))
    if not intent['keywords'] and not intent['tags']:
        raise ValidationError('请填写至少一个检索词或标签。')
    return intent


def ranked_matches(intent):
    terms = {}
    for value in intent['keywords'] + intent['tags']:
        if key := catalog.search_key(value):
            terms.setdefault(key, value)
    if not terms:
        return []
    eligible = catalog.visible_works()
    tags = defaultdict(set)
    links = public_booklist.effective_tags().filter(work_id__in=eligible.values('pk'))
    for work_id, name in links.values_list('work_id', 'tag__name'):
        tags[work_id].add(name)
    aliases = defaultdict(list)
    rows = WorkAlias.objects.filter(work_id__in=eligible.values('pk'), archived_at__isnull=True).order_by('pk')
    for work_id, value in rows.values_list('work_id', 'alias'):
        aliases[work_id].append((value, catalog.search_key(value)))
    exclusions = set(filter(None, map(catalog.search_key, intent['exclude'])))
    ranked = []
    works = list(eligible)
    manuals = catalog.manual_display_metadata(works)
    for work in works:
        title, author, description = catalog.display_text(work, manuals.get(work.pk))
        description = description or ''
        tag_names = sorted(tags.get(work.pk, ()))
        tag_keys = {catalog.search_key(value): value for value in tag_names}
        title_key = catalog.search_key(title)
        author_key = catalog.search_key(author or '')
        description_key = catalog.search_key(description)
        work_aliases = aliases.get(work.pk, ())
        if exclusions:
            haystack = (title_key, author_key, description_key, *tag_keys, *(key for _, key in work_aliases))
            if any(value in field for value in exclusions for field in haystack):
                continue
        evidence = []
        score = 0
        missing = []
        for key, term in terms.items():
            if key in tag_keys:
                score += 4
                evidence.append(('标签', tag_keys[key], ''))
            elif key in title_key:
                score += 5
                evidence.append(('书名', title, ''))
            elif alias := next((value for value, alias_key in work_aliases if key in alias_key), None):
                score += 4
                evidence.append(('别名', alias, ''))
            elif key in author_key:
                score += 4
                evidence.append(('作者', author, ''))
            elif tag := next((name for tag_key, name in tag_keys.items() if key in tag_key), None):
                score += 3
                evidence.append(('标签', tag, ''))
            elif key in description_key:
                score += 1
                evidence.append(('简介片段', description, key))
            else:
                missing.append(term)
        if score:
            ranked.append((score, work, title, author, evidence, missing))
    ranked.sort(key=lambda row: (-row[0], row[1].pk))
    return ranked


def result_rows(ranked):
    return [{'id': work.pk, 'title': title, 'author': author, 'cover_url': catalog.display_cover(work),
             'evidence': [f'{label}「{excerpt(value, key) if key else value}」' for label, value, key in evidence],
             'missing': missing}
            for _, work, title, author, evidence, missing in ranked]


def search_url(search_id, number=1):
    return f'{FIND_PATH}?search={search_id}' + (f'&page={number}' if number > 1 else '')


def redirect_to(location, status=303):
    return HttpResponseRedirect(location, status=status)


def cached_search(request, cache, key):
    if key:
        saved = cache.get(key)
        if saved and saved['account_id'] in (None, getattr(request.account, 'pk', None)):
            return saved
    return None


def display_search(request, search_id, saved):
    ai_available = bool(settings.COURSE_DEEPSEEK_KEY)
    if not saved:
        if request.GET:
            return redirect_to(FIND_PATH, status=302)
        return page(request, 'booklist/course/find.html', {'ai_available': ai_available})
    query, intent = saved['query'], saved['intent']
    terms = intent or {'keywords': [query], 'tags': [], 'exclude': []}
    raw_page = request.GET.get('page', '1')
    number = int(raw_page) if re.fullmatch('[1-9][0-9]{0,5}', raw_page) else 1
    current = Paginator(ranked_matches(terms), PER_PAGE).get_page(number)
    url = search_url(search_id, current.number)
    if request.get_full_path() != url:
        return redirect_to(url, status=302)
    return page(request, 'booklist/course/find.html', {
        'ai_available': ai_available, 'query': query, 'intent': intent, 'status': saved['status'],
        'searched': True, 'results': result_rows(current.object_list),
        'search_id': search_id, 'search_url': url, 'paging': current,
        'previous_url': search_url(search_id, current.previous_page_number()) if current.has_previous() else '',
        'next_url': search_url(search_id, current.next_page_number()) if current.has_next() else '',
    })


@csrf_protect
@never_cache
def find(request):
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    try:
        if request.method != 'POST' or request.POST.get('mode') != 'clear':
            read_limit(request, search=True)
        response = search_page(request)
        response['Referrer-Policy'] = 'same-origin'
        return response
    except services.DomainError as exc:
        return page_error(request, exc.message, exc.status)
    except OperationalError as exc:
        return database_unavailable(request, exc)


def reserve_model_budget(request):
    services.rate_limit('course_ai_ip', request.META.get('REMOTE_ADDR', ''), 6, 3600)
    services.rate_limit('course_ai_daily', 'all', settings.COURSE_DEEPSEEK_DAILY_CALLS, 86400)


def search_page(request):
    cache = caches['course_search']
    browser = request.COOKIES.get(SEARCH_COOKIE, '')
    new_browser = not BROWSER_ID.fullmatch(browser)
    if new_browser:
        browser = token_hex(24)
    search_id = request.GET.get('search', '')
    key = f'{browser}:{search_id}' if SEARCH_ID.fullmatch(search_id) else ''
    if request.method == 'GET':
        response = display_search(request, search_id, cached_search(request, cache, key))
    elif request.POST.get('mode') == 'clear':
        if cached_search(request, cache, key):
            cache.delete(key)
        response = redirect_to(FIND_PATH)
    else:
        response = start_search(request, cache, browser)
    if new_browser:
        response.set_cookie(SEARCH_COOKIE, browser, path=FIND_PATH, secure=settings.COOKIE_SECURE,
                            httponly=True, samesite='Lax')
    return response


def start_search(request, cache, browser):
    try:
        query = clean(request.POST.get('query', ''), 180)
    except ValidationError as exc:
        return page_error(request, exc.messages[0], 400)
    intent, status = None, ''
    mode = request.POST.get('mode')
    if mode == 'conditions':
        try:
            intent = entered_intent(request.POST)
        except ValidationError as exc:
            return page_error(request, exc.messages[0], 400)
    elif mode == 'ai':
        if PRIVATE_INPUT.search(query):
            status = '输入可能包含联系方式或凭据，已改用站内检索。'
        else:
            try:
                if not settings.COURSE_DEEPSEEK_KEY:
                    raise ValueError('Unavailable')
                reserve_model_budget(request)
                intent = extract(query)
            except (services.DomainError, HTTPError, URLError, OSError, KeyError, IndexError, TypeError, ValueError) as exc:
                logger.info('course_intent_fallback type=%s', type(exc).__name__)
                status = '智能解析暂不可用，已执行普通检索。'
    search_id = token_hex(16)
    cache.set(f'{browser}:{search_id}', {
        'query': query, 'intent': intent, 'status': status, 'account_id': getattr(request.account, 'pk', None),
    }, timeout=SEARCH_TTL)
    return redirect_to(search_url(search_id))
