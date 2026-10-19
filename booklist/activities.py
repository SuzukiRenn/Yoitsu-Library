"""Monthly participation is separate from public recommendations and likes."""
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from zoneinfo import ZoneInfo
from . import services, public_booklist as books, catalog
from .models import Activity, ActivityTransition, ActivityWork, Recommendation, Vote, PendingActivitySubmission
from .protocol import pagination, utc_milliseconds
from .text import normalize

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
    row = get(activity_id)
    if not writable(row):
        raise services.DomainError('activity_readonly', '本期活动不可提交', 409)
    return row


def recs(activity_id):
    return Recommendation.objects.filter(scope='activity', activity_id=activity_id, withdrawn_at__isnull=True,
        archived_at__isnull=True, invalidated_at__isnull=True, activity_work__archived_at__isnull=True, activity_work__merged_into__isnull=True)


def votes(activity_id):
    return Vote.objects.filter(scope='activity', activity_id=activity_id, cancelled_at__isnull=True,
        archived_at__isnull=True, activity_work__archived_at__isnull=True, activity_work__merged_into__isnull=True)


def summary(row):
    return {'id': row.pk, 'title': row.title, 'theme': row.theme, 'status': row.status,
        'status_label': LABELS[row.status], 'writable': writable(row), 'corrected': row.corrected_at is not None,
        'start_at': utc_milliseconds(row.started_at or row.start_at),
        'end_at': utc_milliseconds(row.ended_at or row.scheduled_end_at),
        'start_label': timezone.localtime(row.started_at or row.start_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M'),
        'end_label': timezone.localtime(row.ended_at or row.scheduled_end_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')}


def listing(activity_id, client=None, page=1, page_size=20):
    activity = get(activity_id)
    offset, size = pagination(page, page_size)
    snaps = ActivityWork.objects.filter(activity=activity, archived_at__isnull=True, merged_into__isnull=True,
                                       work_id__in=catalog.visible_works().values('id'))
    recommendations, likes = recs(activity_id), votes(activity_id)
    counts = lambda query: dict(query.values('activity_work_id').annotate(total=Count('id')).values_list('activity_work_id', 'total'))
    rc, lc = counts(recommendations), counts(likes)
    ordered = sorted(snaps, key=lambda snap: (-lc.get(snap.pk, 0), -rc.get(snap.pk, 0), snap.created_at, snap.pk))
    ordered = [snap for snap in ordered if rc.get(snap.pk, 0) or lc.get(snap.pk, 0)]
    own = {rec.work_id: rec for rec in recommendations.filter(anonymous_client=client)} if client else {}
    own_likes = set(likes.filter(anonymous_client=client).values_list('work_id', flat=True)) if client else set()
    result = []
    for snap in ordered[offset:offset+size]:
        visible = recommendations.filter(activity_work=snap, text_hidden_at__isnull=True).exclude(reason='').order_by('created_at', 'id')
        result.append({'id': snap.work_id, 'title': snap.snapshot_title, 'author': snap.snapshot_author,
            'cover_url': catalog.cover_url(snap.snapshot_cover_url), 'recommendation_count': rc.get(snap.pk, 0),
            'like_count': lc.get(snap.pk, 0), 'recommended': snap.work_id in own, 'liked': snap.work_id in own_likes,
            'own_reason': own[snap.work_id].reason if snap.work_id in own else '',
            'reason_count': visible.count(), 'reasons': list(visible.values('reason')[:2])})
    return {'activity': summary(activity), 'results': result, 'total': len(ordered), 'page': int(page), 'page_size': size}


def submit(activity_id, client, data):
    activity = require_open(activity_id)
    reason = normalize(data['reason'], 'reason')
    work = books.resolve_submission(data)
    if work.review_status == 'pending':
        PendingActivitySubmission.objects.create(activity=activity, work=work, anonymous_client=client,
            reason=reason, copy_public_reason=data['copy_public_reason'])
        return {'status': 'pending'}
    books.enroll(work)  # No public recommendation or like is created by activity participation.
    snap = ActivityWork.objects.filter(activity=activity, work=work, archived_at__isnull=True, merged_into__isnull=True).first()
    if snap is None:
        snap = ActivityWork.objects.create(activity=activity, work=work, snapshot_title=normalize(work.display_title, 'title', required=True),
            snapshot_author=normalize(work.author_override if work.author_overridden else work.wenku8_author or '', 'title'),
            snapshot_cover_url=catalog.cover_url(work.cover_url_override if work.cover_url_overridden else work.wenku8_cover_url) or '')
    row = recs(activity_id).filter(work=work, anonymous_client=client).first()
    if row:
        edit_reason(row, reason)
    else:
        history = Recommendation.objects.filter(scope='activity', activity=activity, work=work, anonymous_client=client)
        if history.filter(invalidated_at__isnull=False).exists():
            raise services.DomainError('moderated', '该记录已失效，需管理员处理', 409)
        hidden = history.filter(text_hidden_at__isnull=False).order_by('-id').first()
        Recommendation.objects.create(scope='activity', activity=activity, activity_work=snap, work=work,
            anonymous_client=client, reason=reason, text_hidden_at=hidden.text_hidden_at if hidden else None)
    if data['copy_public_reason'] and reason and not books.reasons().filter(work=work, anonymous_client=client).exists():
        books.set_reason(work, client, reason)
    return {'status': 'saved', 'activity_id': activity.pk, 'work_id': work.pk}


def edit_reason(row, text):
    # Preserve moderated original text through append-only audit before a user edit.
    if row.text_hidden_at and row.reason != text:
        services.audit(None, 'activity.reason.user_edit_hidden', row, before={'reason': row.reason}, after={'reason': text})
    row.reason = text
    row.save(update_fields=['reason', 'updated_at'])


def withdraw(activity_id, work_id, client):
    require_open(activity_id); books.require_work(work_id)
    recs(activity_id).filter(work_id=work_id, anonymous_client=client).update(withdrawn_at=timezone.now(), updated_at=timezone.now())
    return {'status': 'saved'}


def reason(activity_id, work_id, client, value):
    require_open(activity_id); books.require_work(work_id)
    row = recs(activity_id).filter(work_id=work_id, anonymous_client=client).first()
    if row is None:
        raise services.DomainError('not_found', '本期推荐不存在', 404)
    edit_reason(row, normalize(value, 'reason'))
    return {'status': 'saved'}


def like(activity_id, work_id, client, enabled):
    require_open(activity_id); books.require_work(work_id)
    row = votes(activity_id).filter(work_id=work_id, anonymous_client=client).first()
    if not enabled:
        if row:
            row.cancelled_at = timezone.now(); row.save(update_fields=['cancelled_at', 'updated_at'])
    elif row is None:
        snap = ActivityWork.objects.filter(activity_id=activity_id, work_id=work_id, archived_at__isnull=True, merged_into__isnull=True).first()
        if snap is None or not (recs(activity_id).filter(activity_work=snap).exists() or votes(activity_id).filter(activity_work=snap).exists()):
            raise services.DomainError('not_in_activity', '请先推荐到本期活动', 409)
        Vote.objects.create(scope='activity', activity_id=activity_id, activity_work=snap, work_id=work_id, anonymous_client=client)
    return {'status': 'saved'}


def conflict(start, end, exclude=None):
    return Activity.objects.filter(status__in=['scheduled', 'active', 'paused'], start_at__lt=end,
        scheduled_end_at__gt=start).exclude(pk=exclude).exists()


def transition(row, target, effective, processed, reason, admin=None):
    previous = row.status; row.status = target; row.save()
    ActivityTransition.objects.create(activity=row, from_status=previous, to_status=target,
        effective_at=effective, processed_at=processed, admin=admin, reason=reason)


@transaction.atomic
def advance_due():
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
