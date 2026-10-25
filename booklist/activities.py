"""Activity participation follows its period's fixed recommendation destination."""
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from zoneinfo import ZoneInfo
from . import services, public_booklist as books, catalog
from .models import Activity, ActivityTransition, ActivityWork, Recommendation, Vote, PendingActivitySubmission
from .protocol import pagination, utc_milliseconds
from .text import normalize
from .shelf_sort import validate as validate_sort
from .shelf_filters import search_text,tag_ids,tag_match_mode

DESTINATIONS = {'public': '同时推荐到公共书架', 'activity_only': '仅推荐到本期活动'}


def destination_locked(row):
    # History includes withdrawn, invalidated and archived participation. Never unlock on withdrawal.
    return row.first_recommended_at is not None or Recommendation.objects.filter(activity=row, scope='activity').exists()


def lock_destination(row, at=None):
    if row.first_recommended_at is None:
        row.first_recommended_at = at or timezone.now()
        row.save(update_fields=['first_recommended_at', 'updated_at'])


LABELS = {'draft': '草稿', 'scheduled': '待开始', 'active': '进行中', 'paused': '暂停', 'ended': '已结束', 'cancelled': '已取消'}


def get(activity_id):
    row = Activity.objects.exclude(status='draft').filter(pk=activity_id).first()
    if row is None:
        raise services.DomainError('not_found', '活动不存在', 404)
    return row


def writable(row, now=None):
    now = now or timezone.now()
    return row.status == 'active' and row.started_at <= now < row.scheduled_end_at


def require_open(activity_id):
    row = Activity.objects.select_for_update().exclude(status='draft').filter(pk=activity_id).first()
    if row is None:
        raise services.DomainError('not_found', '活动不存在', 404)
    if not writable(row):
        raise services.DomainError('activity_readonly', '该活动不可提交', 409)
    return row


def recs(activity_id):
    return Recommendation.objects.filter(scope='activity', activity_id=activity_id, withdrawn_at__isnull=True,
        archived_at__isnull=True, invalidated_at__isnull=True, activity_work__archived_at__isnull=True, activity_work__merged_into__isnull=True)


def votes(activity_id):
    return Vote.objects.filter(scope='activity', activity_id=activity_id, cancelled_at__isnull=True,
        archived_at__isnull=True, activity_work__archived_at__isnull=True, activity_work__merged_into__isnull=True)


def summary(row):
    return {'id': row.pk, 'title': row.title, 'theme': row.theme, 'status': row.status,
        'recommendation_destination': row.recommendation_destination,
        'recommendation_destination_label': DESTINATIONS[row.recommendation_destination],
        'status_label': LABELS[row.status], 'writable': writable(row), 'corrected': row.corrected_at is not None,
        'start_at': utc_milliseconds(row.started_at or row.start_at),
        'end_at': utc_milliseconds(row.ended_at or row.scheduled_end_at),
        'start_label': timezone.localtime(row.started_at or row.start_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M'),
        'end_label': timezone.localtime(row.ended_at or row.scheduled_end_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')}


def listing(activity_id, client=None, page=1, page_size=20, *, work_id=None, sort="recommendations", q="", tags=(), ids_only=False, recommended_only=False, liked_only=False, tag_mode="all"):
    sort = validate_sort(sort)
    q = search_text(q)
    tags = tag_ids(tags)
    tag_mode = tag_match_mode(tag_mode)
    activity = get(activity_id)
    offset, size = pagination(page, page_size)
    snaps = ActivityWork.objects.filter(activity=activity, archived_at__isnull=True, merged_into__isnull=True,
                                       work_id__in=catalog.visible_works(include_retired=activity.status == 'ended').values('id'))
    if work_id is not None:
        snaps = snaps.filter(work_id=work_id)
    recommendations, likes = recs(activity_id), votes(activity_id)
    counts = lambda query: dict(query.values('activity_work_id').annotate(total=Count('id')).values_list('activity_work_id', 'total'))
    rc, lc = counts(recommendations), counts(likes)
    def rank(snap):
        counts = {'recommendations': (-rc.get(snap.pk, 0), -lc.get(snap.pk, 0)),
                  'likes': (-lc.get(snap.pk, 0), -rc.get(snap.pk, 0)), 'newest': ()}
        return (*counts[sort], -snap.created_at.timestamp(), snap.pk)
    ordered = sorted(snaps, key=rank)
    ordered = [snap for snap in ordered if rc.get(snap.pk, 0)]
    # Use frozen snapshot tags, never current catalog tags for historical activities.
    tag_counts={}
    def snapshot_tags(snap):
        return {tag['id']:tag['name'] for tag in (snap.snapshot_tags or []) if isinstance(tag,dict) and type(tag.get('id')) is int and isinstance(tag.get('name'),str)}
    for snap in ordered:
        for tag_id,name in snapshot_tags(snap).items():
            row=tag_counts.setdefault(tag_id,{'id':tag_id,'name':name,'count':0});row['count']+=1
    if q:
        # Python casefold preserves non-ASCII case matching on snapshot strings.
        key=q.casefold()
        ordered=[snap for snap in ordered if key in snap.snapshot_title.casefold() or key in (snap.snapshot_author or '').casefold()]
    if tags:
        wanted=set(tags)
        ordered=[snap for snap in ordered if (bool(wanted & snapshot_tags(snap).keys()) if tag_mode == 'any' else wanted <= snapshot_tags(snap).keys())]

    if recommended_only:
        own_ids=set(recommendations.filter(anonymous_client=client).values_list('work_id',flat=True)) if client else set()
        ordered=[snap for snap in ordered if snap.work_id in own_ids]
    if liked_only:
        own_ids=set(likes.filter(anonymous_client=client).values_list('work_id',flat=True)) if client else set()
        ordered=[snap for snap in ordered if snap.work_id in own_ids]
    if ids_only:
        return [snap.work_id for snap in ordered]
    own = {rec.work_id: rec for rec in recommendations.filter(anonymous_client=client)} if client else {}
    own_likes = set(likes.filter(anonymous_client=client).values_list('work_id', flat=True)) if client else set()
    from .reason_likes import entries
    from .editions import edition_info
    from .models import Work
    versions={pk:edition_info(metadata) for pk,metadata in Work.objects.filter(pk__in=[snap.work_id for snap in ordered[offset:offset+size]]).values_list('pk','source_metadata')}
    result = []
    for snap in ordered[offset:offset+size]:
        visible = recommendations.filter(activity_work=snap, text_hidden_at__isnull=True).exclude(reason='').order_by('created_at', 'id')
        result.append({**versions[snap.work_id], 'id': snap.work_id, 'title': snap.snapshot_title, 'author': snap.snapshot_author,
            'tags': [{'id': tag_id, 'name': name} for tag_id, name in snapshot_tags(snap).items()],
            'cover_url': catalog.cover_url(snap.snapshot_cover_url), 'recommendation_count': rc.get(snap.pk, 0),
            'like_count': lc.get(snap.pk, 0), 'recommended': snap.work_id in own, 'liked': snap.work_id in own_likes,
            'own_reason': own[snap.work_id].reason if snap.work_id in own else '',
            'own_reason_references': own[snap.work_id].reason_references if snap.work_id in own else [],
            'own_contains_spoilers': own[snap.work_id].contains_spoilers if snap.work_id in own else False,
            'reason_count': visible.count(), 'reasons': entries(visible, client, limit=2, writable=writable(activity)),
            'top_reason': next(iter(entries(visible, client, limit=1, popular=True, writable=writable(activity))), None)})
    return {'activity': summary(activity), 'results': result, 'total': len(ordered), 'page': int(page), 'page_size': size, 'sort': sort, 'query':q,'selected_tags':tags,'tag_mode':tag_mode,'recommended_only':recommended_only,'liked_only':liked_only,'filter_count':len(tags)+int(recommended_only)+int(liked_only),
            'tags':sorted(tag_counts.values(),key=lambda tag:(-tag['count'],tag['name'],tag['id']))}


def ensure_snapshot(activity, work):
    """Create a complete participation snapshot once; never rewrite an existing snapshot."""
    snap = ActivityWork.objects.filter(activity=activity, work=work, archived_at__isnull=True, merged_into__isnull=True).first()
    if snap is None:
        metadata = catalog.serialize_works([work], detail=True)[0]
        snap = ActivityWork.objects.create(activity=activity, work=work, snapshot_title=normalize(work.display_title, 'title', required=True),
            snapshot_author=normalize(metadata['author'], 'title'),
            snapshot_description=metadata['description'], snapshot_tags=metadata['tags'],
            snapshot_cover_url=catalog.snapshot_cover(work))
    return snap


@transaction.atomic
def submit(activity_id, client, data):
    activity = require_open(activity_id)
    if data.get('expected_recommendation_destination', activity.recommendation_destination) != activity.recommendation_destination:
        raise services.DomainError('activity_destination_changed', '本期推荐归属已调整，请刷新页面后再提交', 409)
    reason = normalize(data['reason'], 'reason')
    work = books.resolve_submission(data, client)
    from .reason_references import validate
    previous=recs(activity_id).filter(work=work,anonymous_client=client).first()
    refs=validate(reason,data.get('references'),previous,work.pk)
    if work.review_status == 'pending':
        PendingActivitySubmission.objects.create(activity=activity, work=work, anonymous_client=client,
            recommendation_destination=activity.recommendation_destination,
            reason=reason, reason_references=refs, contains_spoilers=bool(reason) and data.get('contains_spoilers', False), copy_public_reason=data['copy_public_reason'])
        return {'status': 'pending'}
    if activity.recommendation_destination == 'public':
        books.enroll(work)
    snap = ensure_snapshot(activity, work)
    row = recs(activity_id).filter(work=work, anonymous_client=client).first()
    if row:
        edit_reason(row, reason, data.get('contains_spoilers'), refs)
    else:
        history = Recommendation.objects.filter(scope='activity', activity=activity, work=work, anonymous_client=client)
        if history.filter(invalidated_at__isnull=False).exists():
            raise services.DomainError('moderated', '该记录已失效，需管理员处理', 409)
        hidden = history.filter(text_hidden_at__isnull=False).order_by('-id').first()
        row=Recommendation.objects.create(scope='activity', activity=activity, activity_work=snap, work=work,
            anonymous_client=client, reason=reason, reason_references=refs, contains_spoilers=bool(reason) and data.get('contains_spoilers', False), text_hidden_at=hidden.text_hidden_at if hidden else None)
    lock_destination(activity)
    if activity.recommendation_destination == 'public':
        books.set_relation(work, client, 'recommend', True, origin='activity')
        if data['copy_public_reason'] and reason and not books.reasons().filter(work=work, anonymous_client=client).exists():
            books.set_reason(work, client, reason, data.get('contains_spoilers'), refs, reference_source=row)
    return {'status': 'saved', 'activity_id': activity.pk, 'work_id': work.pk}


def edit_reason(row, text, contains_spoilers=None, references=None):
    from .reason_references import validate
    refs=validate(text,references,row,row.work_id)
    # Preserve moderated original text through append-only audit before a user edit.
    if row.text_hidden_at and (row.reason != text or row.reason_references != refs):
        services.audit(None, 'activity.reason.user_edit_hidden', row, before={'reason': row.reason,'references':row.reason_references}, after={'reason': text,'references':refs})
    row.reason = text
    row.reason_references = refs
    row.contains_spoilers = bool(text) and (row.contains_spoilers if contains_spoilers is None else contains_spoilers)
    row.save(update_fields=['reason', 'contains_spoilers', 'reason_references', 'updated_at'])


@transaction.atomic
def withdraw(activity_id, work_id, client):
    require_open(activity_id); work = books.require_work(work_id, public=False)
    changed = recs(activity_id).filter(work_id=work_id, anonymous_client=client).update(withdrawn_at=timezone.now(), updated_at=timezone.now())
    if changed and not recs(activity_id).filter(work_id=work_id).exists():
        from .lifecycle import finish_activity_cycle
        finish_activity_cycle(activity_id, work)
    return {'status': 'saved'}


@transaction.atomic
def recommend(activity_id, work_id, client, enabled):
    if not enabled:
        return withdraw(activity_id, work_id, client)
    require_open(activity_id)
    work = books.require_work(work_id, public=False)
    # A list button can join an existing entry, never revive a stale or missing entry.
    snap = ActivityWork.objects.filter(activity_id=activity_id, work=work, archived_at__isnull=True,
                                      merged_into__isnull=True).first()
    if snap is None and work.public_added_at is None and not ActivityWork.objects.filter(activity_id=activity_id, work=work).exists():
        raise services.DomainError('not_found', '本期作品不存在', 404)
    if snap is None or not recs(activity_id).filter(activity_work=snap).exists():
        raise services.DomainError('not_in_activity', '请先推荐到该活动', 409)
    if recs(activity_id).filter(work=work, anonymous_client=client).exists():
        # A repeated +1 is not a reason edit, including retries with a fresh idempotency key.
        return {'status': 'saved', 'activity_id': activity_id, 'work_id': work_id}
    return submit(activity_id, client, {'work_id': work_id, 'title': snap.snapshot_title, 'reason': '',
                                      'confirmed_unmatched': False, 'copy_public_reason': False})


def reason(activity_id, work_id, client, value, contains_spoilers=None, references=None):
    require_open(activity_id); books.require_work(work_id, public=False)
    row = recs(activity_id).filter(work_id=work_id, anonymous_client=client).first()
    if row is None:
        raise services.DomainError('not_found', '活动推荐不存在', 404)
    edit_reason(row, normalize(value, 'reason'), contains_spoilers, references)
    return {'status': 'saved'}


def like(activity_id, work_id, client, enabled):
    require_open(activity_id); books.require_work(work_id, public=False)
    row = votes(activity_id).filter(work_id=work_id, anonymous_client=client).first()
    if not enabled:
        if row:
            row.cancelled_at = timezone.now(); row.save(update_fields=['cancelled_at', 'updated_at'])
    elif row is None:
        snap = ActivityWork.objects.filter(activity_id=activity_id, work_id=work_id, archived_at__isnull=True, merged_into__isnull=True).first()
        if snap is None or not recs(activity_id).filter(activity_work=snap).exists():
            raise services.DomainError('not_in_activity', '请先推荐到该活动', 409)
        Vote.objects.create(scope='activity', activity_id=activity_id, activity_work=snap, work_id=work_id, anonymous_client=client)
    return {'status': 'saved'}


def conflict(start, end, exclude=None):
    return Activity.objects.filter(status__in=['scheduled', 'active', 'paused'], start_at__lt=end,
        scheduled_end_at__gt=start).exclude(pk=exclude).exists()


def transition(row, target, effective, processed, reason, admin=None):
    if target == 'ended':
        from .lifecycle import freeze_activity_metadata
        freeze_activity_metadata(row.pk)
    previous = row.status; row.status = target; row.save()
    ActivityTransition.objects.create(activity=row, from_status=previous, to_status=target,
        effective_at=effective, processed_at=processed, admin=admin, reason=reason)


def advance_due():
    now = timezone.now()
    due = Q(status__in=['scheduled', 'active', 'paused'], scheduled_end_at__lte=now) | Q(status='scheduled', start_at__lte=now)
    if not Activity.objects.filter(due).exists():
        return
    _advance_due()


@transaction.atomic
def _advance_due():
    now = timezone.now()
    # End earlier periods before starting adjacent ones. One transaction serializes schedulers and writers.
    for row in Activity.objects.filter(status__in=['scheduled', 'active', 'paused'], scheduled_end_at__lte=now).order_by('scheduled_end_at', 'id'):
        offline = row.status == 'scheduled'
        if offline:
            row.started_at = row.start_at
        row.ended_at = row.scheduled_end_at; row.end_reason = 'offline_window' if offline else 'deadline'
        transition(row, 'ended', row.scheduled_end_at, now, row.end_reason)
    for row in Activity.objects.filter(status='scheduled', start_at__lte=now, scheduled_end_at__gt=now).order_by('start_at', 'id'):
        if conflict(row.start_at, row.scheduled_end_at, row.pk):
            raise services.DomainError('activity_conflict', '活动时段冲突', 409)
        row.started_at = row.start_at
        transition(row, 'active', row.start_at, now, 'scheduled_start')
