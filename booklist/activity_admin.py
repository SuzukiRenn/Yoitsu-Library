from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import Activity
from . import services, activities
from .text import normalize
from .protocol import utc_milliseconds


def timestamp(value):
    result = parse_datetime(value)
    if result is None or timezone.is_naive(result):
        raise services.DomainError('invalid_time', '需要带时区的时间')
    return result


def destination(value):
    if value not in activities.DESTINATIONS:
        raise services.DomainError('invalid_destination', '推荐归属无效')
    return value


def state(row):
    return {'title': row.title, 'theme': row.theme, 'status': row.status,
        'recommendation_destination': row.recommendation_destination,
        'destination_locked': activities.destination_locked(row),
        'start_at': utc_milliseconds(row.start_at), 'end_at': utc_milliseconds(row.scheduled_end_at)}


@transaction.atomic
def create(actor, data):
    services.require_active(actor)
    start, end = timestamp(data['start_at']), timestamp(data['end_at'])
    if start >= end:
        raise services.DomainError('invalid_time', '开始时间须早于结束时间')
    row = Activity.objects.create(title=normalize(data['title'], 'activity_title', required=True),
        theme=normalize(data['theme'], 'theme'), start_at=start, scheduled_end_at=end, created_by=actor,
        recommendation_destination=destination(data.get('recommendation_destination', 'public')))
    services.audit(actor, 'activity.create', row, after=state(row))
    return {'id': row.pk, **state(row)}


@transaction.atomic
def update(actor, activity_id, data):
    services.require_active(actor)
    row = Activity.objects.select_for_update().filter(pk=activity_id).first()
    if row is None:
        raise services.DomainError('not_found', '活动不存在', 404)
    if row.status in ('ended', 'cancelled') or (row.status in ('active', 'paused') and timezone.now() >= row.scheduled_end_at):
        raise services.DomainError('activity_readonly', '活动已结束或取消', 409)
    reason = normalize(data['reason'], 'activity_title', required=True)
    before = state(row); start, end = timestamp(data['start_at']), timestamp(data['end_at'])
    if start >= end:
        raise services.DomainError('invalid_time', '开始时间须早于结束时间')
    if row.status in ('active', 'paused'):
        # Public API timestamps use milliseconds; internally retain the original precise boundary.
        same_start = start in (row.start_at, row.start_at.replace(microsecond=row.start_at.microsecond//1000*1000))
        if end == row.scheduled_end_at.replace(microsecond=row.scheduled_end_at.microsecond//1000*1000):
            end = row.scheduled_end_at
        if not same_start:
            raise services.DomainError('invalid_time', '活动已开始，开始时间不可修改', 409)
        start = row.start_at
    if row.status != 'draft' and (end <= timezone.now() or activities.conflict(row.started_at or start, end, row.pk)):
        raise services.DomainError('activity_conflict', '活动时段无效或冲突', 409)
    target_destination = destination(data.get('recommendation_destination', row.recommendation_destination))
    if target_destination != row.recommendation_destination and activities.destination_locked(row):
        raise services.DomainError('activity_destination_locked', '本期已有参与推荐，推荐归属不可修改', 409)
    row.recommendation_destination = target_destination
    row.title = normalize(data['title'], 'activity_title', required=True)
    row.theme = normalize(data['theme'], 'theme'); row.start_at=start; row.scheduled_end_at=end
    row.save(); services.audit(actor, 'activity.update', row, before=before, after=state(row), reason=reason)
    return {'id': row.pk, **state(row)}


@transaction.atomic
def action(actor, activity_id, action, reason):
    services.require_active(actor)
    row = Activity.objects.select_for_update().filter(pk=activity_id).first()
    if row is None:
        raise services.DomainError('not_found', '活动不存在', 404)
    now = timezone.now(); reason = normalize(reason, 'activity_title', required=True); before=state(row)
    matrix = {'schedule': (('draft',), 'scheduled'), 'start': (('scheduled',), 'active'),
        'pause': (('active',), 'paused'), 'resume': (('paused',), 'active'),
        'end': (('active','paused'), 'ended'), 'cancel': (('draft','scheduled'), 'cancelled')}
    if action not in matrix or row.status not in matrix[action][0]:
        raise services.DomainError('invalid_transition', '活动状态不可执行此操作', 409)
    target = matrix[action][1]
    if action != 'cancel' and now >= row.scheduled_end_at:
        raise services.DomainError('activity_readonly', '活动已到截止时间', 409)
    if action in ('schedule','start','resume') and activities.conflict(now if action=='start' else row.started_at or row.start_at, row.scheduled_end_at, row.pk):
        raise services.DomainError('activity_conflict', '活动时段冲突', 409)
    if action == 'start':
        row.started_at = now; row.start_at = now
    if action == 'end':
        row.ended_at = now; row.end_reason='manual_end'
    if action == 'cancel':
        row.cancelled_at=now; row.cancel_reason=reason
    activities.transition(row, target, now, now, reason, actor)
    services.audit(actor, 'activity.'+action, row, before=before, after=state(row), reason=reason)
    return {'id': row.pk, **state(row)}
