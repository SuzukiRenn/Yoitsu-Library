"""Read-only local candidates. Similarity is never an identity assertion."""
from difflib import SequenceMatcher
import heapq
import re
import unicodedata
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef
from .models import Work, WorkAlias, WorkTag, TagSuppression, PublicRecommendation, PublicLike, UserWorkMetadata
from .services import DomainError
from .text import normalize, WHITESPACE

MAX_TERMS = 20000
FUZZY_POOL = 200


def visible_works(include_retired=False):
    rows = Work.objects.filter(hidden_at__isnull=True, review_status='clear', merged_into__isnull=True)
    return rows if include_retired else rows.exclude(source_metadata__has_key='manual_retired_at')


def public_works():
    # Enrollment time is historical; only active recommendations keep a work on the shelf.
    recommendations = PublicRecommendation.objects.filter(work_id=OuterRef('pk'), withdrawn_at__isnull=True,
        invalidated_at__isnull=True, archived_at__isnull=True)
    return visible_works().filter(public_added_at__isnull=False).filter(Exists(recommendations))


def search_key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold()
                   if c not in WHITESPACE and not unicodedata.category(c).startswith(('P', 'Z')))


def cover_url(value, *, cached=True):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return None
    try:
        url = urlsplit(value)
        from .cover_cache import local_url
        local = local_url(value) if cached else None
        if local:
            return local
        # The source emits HTTP, but this specific image host also serves HTTPS.
        # Keep source snapshots unchanged; normalize only the displayed URL.
        if url.scheme == 'http' and url.netloc == 'img.wenku8.com' and not url.query and not url.fragment:
            match = re.fullmatch(r'/image/([0-9]+)/([1-9][0-9]*)/\2s\.(?:jpg|jpeg|png)', url.path)
            if match and int(match[1]) == int(match[2]) // 1000:
                return 'https://img.wenku8.com'+url.path
        return value if url.scheme == 'https' and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


def versioned_cover(work):
    from . import cover_cache
    if work.cover_url_overridden:
        return None
    pointer = work.source_metadata.get('cover_cache_version')
    if not isinstance(pointer, dict) or pointer.get('source_url') != cover_cache.source_url(work.wenku8_cover_url):
        return None
    name = pointer.get('name')
    if not isinstance(name, str) or not cover_cache.NAME.fullmatch(name): return None
    info = cover_cache.cache_info(name)
    if not info or info.get('versioned') is not True or info['source_url'] != pointer['source_url']:
        return None
    return '/media/covers/'+name


def snapshot_cover(work):
    return versioned_cover(work) or cover_url(
        work.cover_url_override if work.cover_url_overridden else work.wenku8_cover_url, cached=False) or ''


def serialize_works(works, detail=False):
    ids = [work.pk for work in works]
    public_ids = set(public_works().filter(pk__in=ids).values_list('pk', flat=True))
    suppressed = list(TagSuppression.objects.filter(work_id__in=ids, is_active=True).values_list('work_id', 'tag_id', 'scope'))
    all_bans = {(work, tag) for work, tag, scope in suppressed if scope == 'all'}
    source_bans = {(work, tag) for work, tag, scope in suppressed if scope == 'source'}
    tags = {pk: {} for pk in ids}
    rows = WorkTag.objects.filter(work_id__in=ids, state='active', tag__is_active=True,
                                  tag__merged_into__isnull=True).values_list('work_id', 'tag_id', 'tag__name', 'source_type')
    for work, tag, name, origin in rows:
        if (work, tag) not in all_bans and not (origin == 'source' and (work, tag) in source_bans):
            tags[work][tag] = {'id': tag, 'name': name}
    manual_ids = [work.pk for work in works if work.wenku8_book_id is None or work.source_metadata.get('preserve_user_description')]
    manuals = {row.work_id: row for row in UserWorkMetadata.objects.filter(work_id__in=manual_ids, archived_at__isnull=True)} if manual_ids else {}
    results = []
    for work in works:
        manual = manuals.get(work.pk)
        author = work.author_override if work.author_overridden else work.wenku8_author or ''
        if manual and work.wenku8_book_id is None and not work.author_overridden:
            author = manual.author or author
        row = {'id': work.pk, 'title': work.display_title,
               'author': author,
               'cover_url': versioned_cover(work) or cover_url(work.cover_url_override if work.cover_url_overridden else work.wenku8_cover_url),
               'is_public': work.pk in public_ids,
               'source_book_id': work.wenku8_book_id,
               'tags': sorted(tags[work.pk].values(), key=lambda tag: (tag['name'], tag['id']))}
        if detail:
            row['description'] = work.description_override if work.description_overridden else (manual.description if manual and manual.description else work.wenku8_description or '')
            row['metadata'] = {key: work.source_metadata.get(key) for key in ('publisher_category', 'status', 'last_updated', 'word_count')}
        results.append(row)
    return results


def work_detail(work_id, include_retired=False):
    work = visible_works(include_retired).filter(pk=work_id).first()
    if work is None:
        raise DomainError('not_found', '作品不存在或暂不可见', 404)
    return serialize_works([work], detail=True)[0]


def recent_works():
    return serialize_works(list(visible_works().order_by('-id')[:6]))


def terms():
    result = []
    def append(work_id, value, kind):
        if len(result) >= MAX_TERMS:
            raise DomainError('search_capacity', '书库检索暂不可用，请稍后重试', 503)
        result.append((work_id, value, kind, search_key(value)))
    for work in visible_works().only('id', 'title_override', 'title_overridden', 'wenku8_title', 'original_submitted_title').order_by('id').iterator(chunk_size=500):
        append(work.pk, work.display_title, 'title')
    aliases = WorkAlias.objects.filter(work_id__in=visible_works().values('id'), archived_at__isnull=True).order_by('id')
    for work_id, alias in aliases.values_list('work_id', 'alias').iterator(chunk_size=500):
        append(work_id, alias, 'alias')
    return result


def search(query, limit=10, offset=0):
    query = normalize(query, 'title', required=True)
    key = search_key(query)
    if not key or type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or not 0 <= offset <= MAX_TERMS:
        raise ValidationError('查询或候选数量无效')
    candidates = terms()
    exact = [(work, (0 if kind == 'title' else 1, -1.0, work), 'exact_'+kind)
             for work, value, kind, normalized in candidates if value == query]
    normalized = [(work, (2 if kind == 'title' else 3, -1.0, work), 'normalized_'+kind)
                  for work, value, kind, normalized in candidates if normalized == key]
    matches = exact or normalized
    strategy = 'exact' if exact else 'normalized' if normalized else 'local'
    if not matches:
        fuzzy = []
        key_chars = set(key)
        for work, value, kind, normalized in candidates:
            if not normalized:
                continue
            if normalized.startswith(key):
                matches.append((work, (4, -len(key)/len(normalized), work), 'prefix'))
            elif key in normalized:
                matches.append((work, (5, -len(key)/len(normalized), work), 'substring'))
            elif 3 <= len(key) <= 600 and len(normalized) <= 600:
                length_ratio = min(len(key), len(normalized))/max(len(key), len(normalized))
                chars = set(normalized)
                overlap = 2*len(key_chars & chars)/(len(key_chars)+len(chars))
                if length_ratio >= 0.5 and overlap >= 0.4:
                    fuzzy.append((-overlap, work, normalized))
        for overlap, work, value in heapq.nsmallest(FUZZY_POOL, fuzzy):
            ratio = SequenceMatcher(None, key, value, autojunk=False).ratio()
            if ratio >= 0.65:
                matches.append((work, (6, -ratio, work), 'fuzzy'))
    # Deduplicate only a work's multiple terms; keep different same-name works.
    chosen = {}
    for work, rank, kind in matches:
        if work not in chosen or rank < chosen[work][0]:
            chosen[work] = (rank, kind)
    visible_ids = set(visible_works().filter(pk__in=chosen).values_list('pk', flat=True))
    ranked = [pk for pk in sorted(chosen, key=lambda work: chosen[work][0]) if pk in visible_ids]
    total = len(ranked)
    ordered = ranked[offset:offset+limit]
    current = {work.pk: work for work in visible_works().filter(pk__in=ordered)}
    rows = serialize_works([current[pk] for pk in ordered if pk in current])
    for row in rows:
        row['match_kind'] = chosen[row['id']][1]
    next_offset = offset + limit if offset + limit < total else None
    return {'query': query, 'results': rows, 'strategy': strategy if total else 'none',
            'total': total, 'offset': offset, 'next_offset': next_offset, 'has_more': next_offset is not None,
            'requires_confirmation': True, 'ai_used': False}
