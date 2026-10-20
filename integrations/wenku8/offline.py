"""Adapter for the unchanged saved-page parser. No network/browser API."""
import hashlib
import codecs
import importlib.util
from html.parser import HTMLParser
import re
from pathlib import Path
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError
from booklist.text import normalize

ROOT = Path(__file__).absolute().parents[2]
PARSER_PATH = ROOT / 'tests/wenku8_pages/parse_wenku8_pages.py'
LEGACY_PATH = ROOT / 'tests/wenku8_html/parse_wenku8_html.py'
_parser = None
MAX_HTML_BYTES = 512 * 1024


class SnapshotError(ValueError):
    pass


def parser():
    global _parser
    if _parser is None:
        spec = importlib.util.spec_from_file_location('reused_wenku8_parser', PARSER_PATH)
        _parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_parser)
    return _parser


def fingerprint():
    digest = hashlib.sha256()
    for path in (PARSER_PATH, LEGACY_PATH, Path(__file__).absolute()):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def canonical_detail_url(book_id, value):
    if type(book_id) is not int or book_id <= 0 or not isinstance(value, str):
        raise SnapshotError('invalid_source_identity')
    url = urlsplit(value)
    if url.scheme != 'https' or url.netloc != 'www.wenku8.net' or url.path != f'/book/{book_id}.htm' or url.query or url.fragment:
        raise SnapshotError('invalid_source_identity')
    return value


def safe_cover(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValidationError('Invalid cover URL')
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
        raise ValidationError('Invalid cover URL')
    return value


def field(state, value=None, raw=None, complete=None, code=''):
    return {'state': state, 'value': value, 'raw': raw, 'complete': complete, 'code': code}


def document_diagnostics(text, errors):
    """Ignore only observed URL-attribute entity noise, never text/structure.

    Use lexical positions from the original input, rather than a recovered DOM.
    Newer libxml2 HTML tokenizers omit legacy missing-semicolon diagnostics, so
    enforce that collection policy independently and deduplicate native errors.
    """
    offsets = [0, *(match.end() for match in re.finditer('\n', text))]

    class Scan(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.hrefs = []
            self.ends = []
            self.entity_errors = []
            self.rawtext_tag = None
            self.unverified_attribute = False

        def check_entities(self, fragment, start):
            for match in re.finditer(r'&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]*);?', fragment):
                if not match[0].endswith(';'):
                    kind = 'ERR_CHARREF_SEMICOL_MISSING' if match[0].startswith('&#') else 'ERR_ENTITYREF_SEMICOL_MISSING'
                    self.entity_errors.append((kind, start+match.start(), start+match.end()))

        def handle_data(self, data):
            if self.rawtext_tag is None:
                line, column = self.getpos()
                self.check_entities(data, offsets[line-1]+column)

        def handle_entityref(self, name):
            line, column = self.getpos()
            start = offsets[line-1]+column
            self.check_entities(text[start:start+len(name)+2], start)

        def handle_charref(self, name):
            line, column = self.getpos()
            start = offsets[line-1]+column
            self.check_entities(text[start:start+len(name)+3], start)

        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style'):
                self.rawtext_tag = tag
            line, column = self.getpos()
            start = offsets[line-1] + column
            original = self.get_starttag_text()
            opening = re.match(r'<[^\s/>]+', original)
            attribute = re.compile(r'''\s+([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+)))?''', re.S)
            cursor = opening.end() if opening else len(original)
            hrefs = []
            while original[cursor:].strip() not in ('>', '/>'):
                match = attribute.match(original, cursor)
                if not match:
                    self.unverified_attribute = True
                    self.check_entities(original[cursor:], start+cursor)
                    return
                cursor = match.end()
                group = 2 if match[2] is not None else 3 if match[3] is not None else None
                value_group = group or (4 if match[4] is not None else None)
                if value_group is not None:
                    self.check_entities(match[value_group], start+match.start(value_group))
                if match[1].lower() == 'href' and group is not None:
                    value = match[group]
                    if '<' not in value and '>' not in value:
                        hrefs.append((start+match.start(group), start+match.end(group)))
            self.hrefs.extend(hrefs)

        def handle_endtag(self, tag):
            if tag == self.rawtext_tag:
                self.rawtext_tag = None
            if tag in ('body', 'html'):
                self.ends.append(tag)

    scan = Scan()
    lexical_ok = True
    try:
        scan.feed(text)
        scan.close()
    except (ValueError, AssertionError):
        lexical_ok = False
    explicit = (lexical_ok and scan.ends == ['body', 'html']
                and bool(re.search(r'</body>\s*</html>\s*$', text, re.I)))
    ignored = 0
    blocking = set()
    if scan.unverified_attribute:
        blocking.add('unverified_attribute_syntax')
    diagnostic_count = len(scan.entity_errors)
    for kind, start, end in scan.entity_errors:
        if lexical_ok and kind == 'ERR_ENTITYREF_SEMICOL_MISSING' and any(left <= start and end <= right for left, right in scan.hrefs):
            ignored += 1
        else:
            blocking.add(kind)
    for error in errors:
        position = offsets[error.line-1]+error.column-1 if 1 <= error.line <= len(offsets) else -1
        if any(kind == error.type_name and start <= position <= end for kind, start, end in scan.entity_errors):
            continue
        diagnostic_count += 1
        if (lexical_ok and error.type_name == 'ERR_ENTITYREF_SEMICOL_MISSING'
                and any(start <= position < end for start, end in scan.hrefs)):
            ignored += 1
        else:
            blocking.add(error.type_name)
    # Do not certify a page with a flood of diagnostics; logs may be bounded.
    if diagnostic_count > 50:
        blocking.add('diagnostic_limit_exceeded')
    complete = explicit and not blocking
    return {'complete': complete, 'explicit_end_tags': explicit,
            'parser_error_count': len(errors), 'ignored_href_entity_errors': ignored,
            'lexical_entity_error_count': len(scan.entity_errors),
            'blocking_error_types': sorted(blocking),
            'reason': '' if complete else ('missing_explicit_end' if not explicit else 'blocking_parser_diagnostic')}


def parser_bytes(raw):
    """Handle observed GB18030 extension characters in GBK-declared source HTML.

    Keep legacy parsers unchanged. Only the charset token in the parser's copy
    changes; decoding must be strict and round-trip the entire original body.
    """
    declaration = re.search(br'<meta\b[^>]*charset\s*=\s*["\x27]?\s*([A-Za-z0-9_-]+)', raw, re.I)
    if not declaration or codecs.lookup(declaration[1].decode('ascii')).name != 'gbk':
        return raw, None
    try:
        raw.decode('gbk', errors='strict')
        return raw, None
    except UnicodeDecodeError:
        text = raw.decode('gb18030', errors='strict')
        if text.encode('gb18030', errors='strict') != raw:
            raise ValueError('non_roundtrip_encoding')
        adjusted = raw[:declaration.start(1)] + b'gb18030' + raw[declaration.end(1):]
        return adjusted, {'declared': 'gbk', 'decoded': 'gb18030', 'strict_roundtrip': True,
                          'original_body_sha256': hashlib.sha256(raw).hexdigest()}


def extract(raw, book_id, source_url):
    canonical_detail_url(book_id, source_url)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_HTML_BYTES:
        raise SnapshotError('invalid_file_size')
    p = parser()
    try:
        parse_raw, encoding = parser_bytes(raw)
        result = p.parse_page(parse_raw, source_url=source_url)
        text, _ = p.LEGACY.decode_html(parse_raw)
        html_parser=p.html.HTMLParser(no_network=True, remove_comments=True)
        root = p.html.fromstring(text, parser=html_parser)
        document = document_diagnostics(text, html_parser.error_log)
        if encoding:
            document['encoding'] = encoding
    except (ValueError, LookupError, UnicodeError):
        raise SnapshotError('unrecognized_or_mismatched_detail') from None
    if result.get('page_type') != 'detail' or result.get('book_id') != book_id or result['diagnostics']['fields']['title']['state'] != 'ok':
        raise SnapshotError('invalid_detail_identity')
    try:
        title = normalize(result['title'], 'title', required=True)
        title_field = field('value',title,result['title'])
    except ValidationError:
        title_field = field('invalid',raw=result['title'],code='invalid_standard_title')
    tables = p.metadata_tables(root)
    if len(tables) != 1:
        raise SnapshotError('invalid_detail_identity')
    meta = tables[0]
    details = meta.xpath('following-sibling::table[1]')
    detail = details[0] if len(details) == 1 else None
    fields = {'title':title_field}
    for name in ('author','publisher_category','status','last_updated','word_count'):
        label = p.LEGACY.LABELS[name]
        values = [p.LEGACY.value_after_label(td,label) for td in meta.xpath('.//td[not(.//td)]')]
        values = [v for v in values if v is not None]
        if not values:
            fields[name] = field('not_fetched', code='node_missing')
        elif len(values) != 1:
            fields[name] = field('parse_error', raw=values, code='ambiguous_field')
        elif values[0] == '':
            fields[name] = field('empty', raw='')
        elif result['diagnostics']['fields'][name]['state'] == 'ok':
            fields[name] = field('value', result[name], values[0])
        else:
            fields[name] = field('parse_error', raw=values[0], code='unsupported_value')
    # A unique explicit label and its body are evidence for an empty description.
    labels = [] if detail is None else [n for n in detail.xpath('.//td/span') if p.LEGACY.compact(n.text_content()) in ('内容简介：','内容简介:')]
    bodies = labels[0].xpath('following-sibling::span[1]') if len(labels) == 1 else []
    if len(labels) == 1 and len(bodies) == 1:
        intro = p.LEGACY.plain_paragraphs(bodies[0])
        fields['introduction'] = field('value' if intro else 'empty', intro or None, intro)
    else:
        fields['introduction'] = field('not_fetched' if not labels else 'parse_error', code='description_unavailable')
    images = [] if detail is None else detail.xpath('.//td[span/b[starts-with(normalize-space(.),"作品Tags")]]/preceding-sibling::td[1]/img')
    if result.get('cover_url'):
        fields['cover_url'] = field('value', result['cover_url'], result['cover_url'])
    elif len(images) == 1 and not (images[0].get('src') or '').strip():
        fields['cover_url'] = field('empty', raw='')
    else:
        fields['cover_url'] = field('not_fetched' if not images else 'parse_error', raw=[n.get('src') for n in images] or None, code='cover_unavailable')
    tag_nodes = [] if detail is None else [n for n in detail.xpath('.//span/b') if p.LEGACY.value_after_label(n,'作品Tags') is not None]
    if len(tag_nodes) == 1:
        tag_text = p.LEGACY.value_after_label(tag_nodes[0],'作品Tags')
        # Existing samples are plain text; do not invent IDs from unsupported link syntax.
        if tag_nodes[0].xpath('.//a'):
            fields['tags'] = field('parse_error', raw=tag_text, complete=False, code='tag_link_identity_unverified')
        else:
            tags = [{'id':None,'name':name} for name in dict.fromkeys(tag_text.split())]
            fields['tags'] = field('value' if tags else 'empty', tags, tag_text, complete=True)
    else:
        fields['tags'] = field('not_fetched' if not tag_nodes else 'parse_error', complete=False, code='tags_incomplete')
    if not document['complete']:
        # lxml recovery can manufacture closing nodes. It must not certify an
        # empty/complete collection or overwrite previous optional metadata.
        for name,diagnostic in fields.items():
            if name!='title':
                diagnostic.update(state='parse_error',value=None,code='incomplete_document')
                if name=='tags':
                    diagnostic['complete']=False
    return {'version':1, 'book_id':book_id, 'source_url':source_url, 'fields':fields,
            'document_diagnostics':document}
