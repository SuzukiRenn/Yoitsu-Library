"""Public recommendations and likes have separate identities and lifecycles."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from . import catalog, services
from .models import PublicRecommendation, PublicLike, Recommendation, Work, WorkAlias, WorkTag, TagSuppression
from .protocol import pagination
from .text import normalize
from .shelf_sort import validate as validate_sort, validate_period
from .shelf_filters import search_text, tag_ids, search_public


def recommendations():
    return PublicRecommendation.objects.filter(withdrawn_at__isnull=True, invalidated_at__isnull=True, archived_at__isnull=True)


def likes():
    return PublicLike.objects.filter(cancelled_at__isnull=True, invalidated_at__isnull=True, archived_at__isnull=True)


def reasons():
    return Recommendation.objects.filter(scope='public', archived_at__isnull=True, invalidated_at__isnull=True)


def public_works():
    return catalog.public_works()


def effective_tags():
    bans = TagSuppression.objects.filter(work_id=OuterRef('work_id'), tag_id=OuterRef('tag_id'), is_active=True)
    return WorkTag.objects.filter(state='active', tag__is_active=True, tag__merged_into__isnull=True).annotate(
        all_banned=Exists(bans.filter(scope='all')), source_banned=Exists(bans.filter(scope='source'))
    ).filter(all_banned=False).exclude(source_type='source', source_banned=True)


def serialize(works, client=None):
    # Page-bounded work set; aggregate relations independently to avoid multiplying counts.
    works = list(works)
    ids = [work.pk for work in works]
    recs, votes = recommendations().filter(work_id__in=ids), likes().filter(work_id__in=ids)
    counts = lambda query: dict(query.values('work_id').annotate(total=Count('id')).values_list('work_id', 'total'))
    rec_counts, like_counts = counts(recs), counts(votes)
    own_recs = set(recs.filter(anonymous_client=client).values_list('work_id', flat=True)) if client else set()
    own_likes = set(votes.filter(anonymous_client=client).values_list('work_id', flat=True)) if client else set()
    own_reasons = dict(reasons().filter(work_id__in=ids, anonymous_client=client).values_list('work_id', 'reason')) if client else {}
    own_spoilers = dict(reasons().filter(work_id__in=ids, anonymous_client=client).values_list('work_id', 'contains_spoilers')) if client else {}
    visible_reasons = reasons().filter(text_hidden_at__isnull=True)
    reason_counts = counts(visible_reasons.filter(work_id__in=ids))
    from .reason_likes import entries
    result = catalog.serialize_works(works)
    for row in result:
        pk = row['id']
        row.update(recommendation_count=rec_counts.get(pk, 0), like_count=like_counts.get(pk, 0),
                   recommended=pk in own_recs, liked=pk in own_likes, own_reason=own_reasons.get(pk, ''), own_contains_spoilers=own_spoilers.get(pk, False),
                   reason_count=reason_counts.get(pk, 0),
                   reasons=entries(visible_reasons.filter(work_id=pk), client, limit=2),
                   top_reason=next(iter(entries(visible_reasons.filter(work_id=pk), client, limit=1, popular=True)), None))
    return result


def listing(client=None, page=1, page_size=20, tags=(), sort="recommendations", period="all", q=""):
    sort = validate_sort(sort)
    period = validate_period(period)
    offset, size = pagination(page, page_size)
    tags = tag_ids(tags)
    q = search_text(q)
    query = search_public(public_works(), q)
    for tag in tags:
        query = query.filter(pk__in=effective_tags().filter(tag_id=tag).values('work_id'))
    active = Q(publicrecommendation__withdrawn_at__isnull=True, publicrecommendation__invalidated_at__isnull=True, publicrecommendation__archived_at__isnull=True)
    active_likes = Q(publiclike__cancelled_at__isnull=True, publiclike__invalidated_at__isnull=True, publiclike__archived_at__isnull=True)
    if period != 'all':
        now = timezone.now()
        cutoff = now - timedelta(days=7 if period == 'week' else 30)
        active &= Q(publicrecommendation__created_at__gte=cutoff, publicrecommendation__created_at__lte=now)
        active_likes &= Q(publiclike__created_at__gte=cutoff, publiclike__created_at__lte=now)
    query = query.annotate(rank_count=Count('publicrecommendation', filter=active, distinct=True),
                           rank_likes=Count('publiclike', filter=active_likes, distinct=True))
    if period != 'all':
        if sort == 'newest':
            query = query.filter(public_added_at__gte=cutoff, public_added_at__lte=now)
        else:
            query = query.filter(**{'rank_count__gt' if sort == 'recommendations' else 'rank_likes__gt': 0})
    ordering = {'recommendations': ('-rank_count', '-rank_likes'),
                'likes': ('-rank_likes', '-rank_count'), 'newest': ()}
    query = query.order_by(*ordering[sort], '-public_added_at', 'id')
    tag_rows = effective_tags().filter(work_id__in=public_works().values('id')).values('tag_id', 'tag__name').annotate(count=Count('work_id', distinct=True)).order_by('-count', 'tag__name', 'tag_id')
    return {'results': serialize(query[offset:offset+size], client), 'total': query.count(), 'page': int(page),
            'page_size': size, 'selected_tags': tags, 'sort': sort, 'period': period, 'query': q,
            'tags': [{'id': row['tag_id'], 'name': row['tag__name'], 'count': row['count']} for row in tag_rows]}


def require_work(work_id, public=True):
    query = catalog.visible_works()
    # Keep write/reason eligibility for previously enrolled works, including monthly-only works.
    # Leaving the list is not deletion or moderation; a new recommendation reuses this record.
    if public:
        query = query.filter(public_added_at__isnull=False)
    work = query.filter(pk=work_id).first()
    if work is None:
        raise services.DomainError('not_found', '作品不存在或暂不可见', 404)
    return work


def set_reason(work, client, value, contains_spoilers=None):
    value = normalize(value, 'reason')
    row = reasons().filter(work=work, anonymous_client=client).first()
    spoilers = bool(value) and (row.contains_spoilers if contains_spoilers is None and row else bool(contains_spoilers))
    if not value:
        if row:
            if row.text_hidden_at:
                row.archived_at = timezone.now()
                row.save(update_fields=['archived_at', 'updated_at'])
            else:
                row.delete()
    elif row:
        if row.text_hidden_at and row.reason != value:
            row.archived_at = timezone.now()
            row.save(update_fields=['archived_at', 'updated_at'])
            Recommendation.objects.create(work=work, anonymous_client=client, scope='public', reason=value, contains_spoilers=spoilers, text_hidden_at=row.text_hidden_at)
        else:
            row.reason = value
            row.contains_spoilers = spoilers
            row.save(update_fields=['reason', 'contains_spoilers', 'updated_at'])
    else:
        if Recommendation.objects.filter(work=work, anonymous_client=client, scope='public', invalidated_at__isnull=False).exists():
            raise services.DomainError('moderated', '该记录已失效，需管理员处理', 409)
        # A moderated history must not become visible simply through a new text row.
        hidden = Recommendation.objects.filter(work=work, anonymous_client=client, scope='public', text_hidden_at__isnull=False).order_by('-id').first()
        Recommendation.objects.create(work=work, anonymous_client=client, scope='public', reason=value, contains_spoilers=spoilers,
                                      text_hidden_at=hidden.text_hidden_at if hidden else None)


@transaction.atomic
def set_relation(work, client, kind, enabled, origin='plus_one'):
    if kind == 'like' and enabled and not recommendations().filter(work=work).exists():
        raise services.DomainError('not_in_public', '请先推荐这本书，再进行点赞', 409)
    query = recommendations() if kind == 'recommend' else likes()
    row = query.filter(work=work, anonymous_client=client).first()
    if enabled and row is None:
        model = PublicRecommendation if kind == 'recommend' else PublicLike
        if model.objects.filter(work=work, anonymous_client=client, invalidated_at__isnull=False).exists():
            raise services.DomainError('moderated', '该记录已失效，需管理员处理', 409)
        if kind == 'recommend' and work.review_status == 'clear' and not query.filter(work=work).exists():
            work.public_added_at = timezone.now(); work.save(update_fields=['public_added_at', 'updated_at'])
        model.objects.create(work=work, anonymous_client=client, **({'origin': origin} if kind == 'recommend' else {}))
    elif not enabled and row:
        field = 'withdrawn_at' if kind == 'recommend' else 'cancelled_at'
        setattr(row, field, timezone.now())
        row.save(update_fields=[field, 'updated_at'])
        if kind == 'recommend' and not recommendations().filter(work=work).exists():
            from .lifecycle import finish_public_cycle
            finish_public_cycle(work)


def resolve_submission(data, client=None):
    title = normalize(data['title'], 'title', required=True)
    if data['work_id'] is not None:
        if 'metadata' in data:
            raise services.DomainError('metadata_separate', '已有作品请从详情页编辑资料', 409)
        retired = Work.objects.filter(pk=data['work_id'], hidden_at__isnull=True, review_status='clear',
            merged_into__isnull=True, source_metadata__has_key='manual_retired_at').exists()
        if retired:
            raise services.DomainError('resubmission_required', '这本未收录作品已退出书单，请重新填写作者、标签和简介', 409)
        work = require_work(data['work_id'], public=False)
    else:
        # Recheck within the idempotent write transaction, including current overrides and aliases.
        title_match = Q(title_overridden=True, title_override=title) | Q(title_overridden=False, wenku8_title=title) | (
            Q(title_overridden=False) & (Q(wenku8_title__isnull=True) | Q(wenku8_title='')) & Q(original_submitted_title=title))
        aliases = WorkAlias.objects.filter(alias=title, archived_at__isnull=True).values('work_id')
        matches = Work.objects.filter(title_match | Q(pk__in=aliases)).filter(merged_into__isnull=True).exclude(
            Q(source_metadata__has_key='manual_retired_at') & Q(hidden_at__isnull=True, review_status='clear'))
        # A similarity suggestion is not an identity match. Explicit manual entry
        # may dismiss partial/fuzzy suggestions, never exact titles or aliases.
        candidates = catalog.search(title)
        if matches.filter(hidden_at__isnull=True, review_status='clear').exists() or (
                candidates['results'] and candidates['strategy'] in ('exact', 'normalized')):
            raise services.DomainError('existing_candidate', '已有同名或同别名作品，请选择已有作品', 409)
        if candidates['results'] and not data.get('confirmed_unmatched', False):
            raise services.DomainError('existing_candidate', '请选择候选作品，或确认手动填写新作品', 409)
        pending = matches.filter(Q(hidden_at__isnull=False) | Q(review_status='pending')).exists()
        work = Work.objects.create(original_submitted_title=title, review_status='pending' if pending else 'clear')
        if client:
            from . import user_metadata
            user_metadata.initialize(work, client, data.get('metadata'))
    return work


def enroll(work):
    pending = work.review_status == 'pending'
    if not pending and work.public_added_at is None:
        work.public_added_at = timezone.now()
        work.save(update_fields=['public_added_at', 'updated_at'])


def submit(client, data):
    reason = normalize(data['reason'], 'reason')
    work = resolve_submission(data, client)
    pending = work.review_status == 'pending'
    enroll(work)
    set_relation(work, client, 'recommend', True, origin='submit')
    # Empty optional input must not erase an existing reason during a repeat recommendation.
    if reason:
        set_reason(work, client, reason, data.get('contains_spoilers'))
    return {'status': 'pending'} if pending else {'status': 'saved', 'work': serialize([work], client)[0]}
