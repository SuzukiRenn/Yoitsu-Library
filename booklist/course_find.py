"""Bounded intent extraction. All matching and evidence stay on this server."""
import json
import logging
import re
from threading import BoundedSemaphore
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponseNotAllowed
from django.db import OperationalError
from django.views.decorators.csrf import csrf_protect

from . import catalog, public_booklist, services
from .catalog_views import page, read_limit
from .course_common import clean, page_error
from .shelf_filters import search_public
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


def match(intent):

    eligible = public_booklist.public_works()
    tags = {}
    links = public_booklist.effective_tags().filter(work_id__in=eligible.values('id'))
    for work_id, name in links.values_list('work_id', 'tag__name'):
        tags.setdefault(work_id, set()).add(name)
    exclusions = [catalog.search_key(value) for value in intent['exclude']]
    terms = {value: catalog.search_key(value) for value in intent['keywords'] + intent['tags']}
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
        haystack = (title_key, author_key, description_key, *tag_keys)
        if any(value and any(value in field for field in haystack) for value in exclusions):
            continue
        evidence = []
        score = 0
        missing = []
        for key in dict.fromkeys(terms.values()):
            if not key:
                continue
            if key in tag_keys:
                score += 4
                evidence.append(f'标签「{tag_keys[key]}」')
            elif key in title_key:
                score += 5
                evidence.append(f'书名「{title}」')
            elif key in author_key:
                score += 4
                evidence.append(f'作者「{author}」')
            elif key in description_key:
                score += 1
                evidence.append(f'简介片段「{excerpt(description, key)}」')
            else:
                missing.append(next(term for term, term_key in terms.items() if term_key == key))
        if score:
            ranked.append((score, work, title, author, evidence, missing))
    ranked.sort(key=lambda row: (-row[0], row[1].pk))
    return [{'id': work.pk, 'title': title, 'author': author, 'cover_url': catalog.display_cover(work),
             'evidence': evidence, 'missing': missing}
            for _, work, title, author, evidence, missing in ranked[:15]]


def ordinary(query):
    works = list(search_public(public_booklist.public_works(), query[:100]).order_by('id')[:15])
    return catalog.serialize_works(works)


@csrf_protect
def find(request):
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    try:
        read_limit(request, search=True)
        return search_page(request)
    except services.DomainError as exc:
        return page_error(request, exc.message, exc.status)
    except OperationalError as exc:
        return database_unavailable(request, exc)


def reserve_model_budget(request):
    services.rate_limit('course_ai_ip', request.META.get('REMOTE_ADDR', ''), 6, 3600)
    services.rate_limit('course_ai_daily', 'all', settings.COURSE_DEEPSEEK_DAILY_CALLS, 86400)


def search_page(request):
    if request.method == 'GET':
        return page(request, 'booklist/course/find.html', {'ai_available': bool(settings.COURSE_DEEPSEEK_KEY)})
    try:
        query = clean(request.POST.get('query', ''), 180)
    except ValidationError as exc:
        return page_error(request, exc.messages[0], 400)
    result = {'query': query, 'searched': True, 'ai_available': bool(settings.COURSE_DEEPSEEK_KEY),
              'ordinary_url': '/?' + urlencode({'q': query[:100]})}
    if request.POST.get('mode') == 'conditions':
        try:
            intent = entered_intent(request.POST)
        except ValidationError as exc:
            return page_error(request, exc.messages[0], 400)
        result.update(results=match(intent), intent=intent, status='已按调整后的条件在公共书架查找。')
    elif request.POST.get('mode') != 'ai':
        result.update(results=ordinary(query), status='已使用普通书名、作者和别名检索。')
    elif PRIVATE_INPUT.search(query):
        result.update(results=ordinary(query), status='输入可能包含联系方式或凭据，已改用站内检索。')
    else:
        try:
            if not settings.COURSE_DEEPSEEK_KEY:
                raise ValueError('Unavailable')
            reserve_model_budget(request)
            intent = extract(query)
        except (services.DomainError, HTTPError, URLError, OSError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.info('course_intent_fallback type=%s', type(exc).__name__)
            result.update(results=ordinary(query), status='智能解析暂不可用，已执行普通检索。也可缩短关键词后重试。')
        else:
            matches = match(intent)
            result.update(results=matches, intent=intent, status='以下结果来自当前公共书架的可见资料。' if matches else
                          '公共书架中没有足够依据的匹配。可调整题材或改用普通检索。')
    return page(request, 'booklist/course/find.html', result)
