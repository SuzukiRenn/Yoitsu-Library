"""Public recommendations and likes have separate identities and lifecycles."""
from django.core.exceptions import ValidationError
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from . import catalog, services
from .models import PublicRecommendation, PublicLike, Recommendation, Work, WorkAlias, WorkTag, TagSuppression
from .protocol import pagination
from .text import normalize


def recommendations():
    return PublicRecommendation.objects.filter(withdrawn_at__isnull=True, invalidated_at__isnull=True, archived_at__isnull=True)


def likes():
    return PublicLike.objects.filter(cancelled_at__isnull=True, invalidated_at__isnull=True, archived_at__isnull=True)


def reasons():
    return Recommendation.objects.filter(scope='public', archived_at__isnull=True, invalidated_at__isnull=True)


def public_works():
    return catalog.visible_works().filter(public_added_at__isnull=False)


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
    visible_reasons = reasons().filter(text_hidden_at__isnull=True)
    reason_counts = counts(visible_reasons.filter(work_id__in=ids))
    result = catalog.serialize_works(works)
    for row in result:
        pk = row['id']
        row.update(recommendation_count=rec_counts.get(pk, 0), like_count=like_counts.get(pk, 0),
                   recommended=pk in own_recs, liked=pk in own_likes, own_reason=own_reasons.get(pk, ''),
                   reason_count=reason_counts.get(pk, 0),
                   reasons=list(visible_reasons.filter(work_id=pk).order_by('created_at', 'id').values('reason')[:2]))
    return result


def listing(client=None, page=1, page_size=20, tags=()):
    offset, size = pagination(page, page_size)
    if len(tags) > 20 or any(not str(tag).isascii() or not str(tag).isdecimal() or str(int(tag)) != str(tag) or int(tag) <= 0 for tag in tags):
        raise ValidationError('标签无效')
    tags = sorted(set(map(int, tags)))
    query = public_works()
    for tag in tags:
        query = query.filter(pk__in=effective_tags().filter(tag_id=tag).values('work_id'))
    active = Q(publicrecommendation__withdrawn_at__isnull=True, publicrecommendation__invalidated_at__isnull=True, publicrecommendation__archived_at__isnull=True)
    query = query.annotate(rank_count=Count('publicrecommendation', filter=active)).order_by('-rank_count', '-public_added_at', 'id')
    tag_rows = effective_tags().filter(work_id__in=public_works().values('id')).values('tag_id', 'tag__name').annotate(count=Count('work_id', distinct=True)).order_by('-count', 'tag__name', 'tag_id')
    return {'results': serialize(query[offset:offset+size], client), 'total': query.count(), 'page': int(page),
            'page_size': size, 'selected_tags': tags,
            'tags': [{'id': row['tag_id'], 'name': row['tag__name'], 'count': row['count']} for row in tag_rows]}


def require_work(work_id, public=True):
    query = public_works() if public else catalog.visible_works()
    work = query.filter(pk=work_id).first()
    if work is None:
        raise services.DomainError('not_found', '作品不存在或暂不可见', 404)
    return work


def set_reason(work, client, value):
    value = normalize(value, 'reason')
    row = reasons().filter(work=work, anonymous_client=client).first()
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
            Recommendation.objects.create(work=work, anonymous_client=client, scope='public', reason=value, text_hidden_at=row.text_hidden_at)
        else:
            row.reason = value
            row.save(update_fields=['reason', 'updated_at'])
    else:
        if Recommendation.objects.filter(work=work, anonymous_client=client, scope='public', invalidated_at__isnull=False).exists():
            raise services.DomainError('moderated', '该记录已失效，需管理员处理', 409)
        # A moderated history must not become visible simply through a new text row.
        hidden = Recommendation.objects.filter(work=work, anonymous_client=client, scope='public', text_hidden_at__isnull=False).order_by('-id').first()
        Recommendation.objects.create(work=work, anonymous_client=client, scope='public', reason=value,
                                      text_hidden_at=hidden.text_hidden_at if hidden else None)


def set_relation(work, client, kind, enabled, origin='plus_one'):
    query = recommendations() if kind == 'recommend' else likes()
    row = query.filter(work=work, anonymous_client=client).first()
    if enabled and row is None:
        model = PublicRecommendation if kind == 'recommend' else PublicLike
        if model.objects.filter(work=work, anonymous_client=client, invalidated_at__isnull=False).exists():
            raise services.DomainError('moderated', '该记录已失效，需管理员处理', 409)
        model.objects.create(work=work, anonymous_client=client, **({'origin': origin} if kind == 'recommend' else {}))
    elif not enabled and row:
        field = 'withdrawn_at' if kind == 'recommend' else 'cancelled_at'
        setattr(row, field, timezone.now())
        row.save(update_fields=[field, 'updated_at'])


def resolve_submission(data):
    title = normalize(data['title'], 'title', required=True)
    if data['work_id'] is not None:
        work = require_work(data['work_id'], public=False)
    else:
        # Recheck within the idempotent write transaction, including current overrides and aliases.
        title_match = Q(title_overridden=True, title_override=title) | Q(title_overridden=False, wenku8_title=title) | (
            Q(title_overridden=False) & (Q(wenku8_title__isnull=True) | Q(wenku8_title='')) & Q(original_submitted_title=title))
        aliases = WorkAlias.objects.filter(alias=title, archived_at__isnull=True).values('work_id')
        matches = Work.objects.filter(title_match | Q(pk__in=aliases)).filter(merged_into__isnull=True)
        # The legacy confirmed_unmatched field is retained for old clients, never as an override.
        if matches.filter(hidden_at__isnull=True, review_status='clear').exists() or catalog.search(title)['results']:
            raise services.DomainError('existing_candidate', '请先选择已有候选作品；无法确认时需管理员核查', 409)
        pending = matches.filter(Q(hidden_at__isnull=False) | Q(review_status='pending')).exists()
        work = Work.objects.create(original_submitted_title=title, review_status='pending' if pending else 'clear')
    return work


def enroll(work):
    pending = work.review_status == 'pending'
    if not pending and work.public_added_at is None:
        work.public_added_at = timezone.now()
        work.save(update_fields=['public_added_at', 'updated_at'])


def submit(client, data):
    reason = normalize(data['reason'], 'reason')
    work = resolve_submission(data)
    pending = work.review_status == 'pending'
    enroll(work)
    set_relation(work, client, 'recommend', True, origin='submit')
    # Empty optional input must not erase an existing reason during a repeat recommendation.
    if reason:
        set_reason(work, client, reason)
    return {'status': 'pending'} if pending else {'status': 'saved', 'work': serialize([work], client)[0]}
