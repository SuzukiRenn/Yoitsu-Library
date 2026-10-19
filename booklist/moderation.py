"""Audited moderation and explicit pending-work decisions."""
from django.db import transaction
from django.utils import timezone
from . import services, activities, public_booklist as books
from .models import Work, Recommendation, Activity, ActivityWork, PendingActivitySubmission, PublicRecommendation, PublicLike, Vote
from .text import normalize


def reason(value):
    return normalize(value, 'theme', required=True)


@transaction.atomic
def create_work(actor, title, author, note):
    services.require_active(actor); note = reason(note)
    title = normalize(title, 'title', required=True); author = normalize(author, 'title')
    work = Work.objects.create(original_submitted_title=title, author_override=author,
        author_overridden=bool(author), public_added_at=timezone.now())
    services.audit(actor, 'work.create.reviewed', work, after={'title':title,'author':author}, reason=note)
    return work


def get_work(pk):
    row = Work.objects.filter(pk=pk).first()
    if row is None:
        raise services.DomainError('not_found', '作品不存在', 404)
    if row.merged_into_id:
        raise services.DomainError('merged_work', '请管理正式主作品', 409)
    return row


def corrected(work_id=None, activity_id=None):
    activities.advance_due()
    ids = ActivityWork.objects.filter(work_id=work_id).values('activity_id') if work_id else [activity_id]
    Activity.objects.filter(pk__in=ids, status='ended').update(corrected_at=timezone.now(), updated_at=timezone.now())


@transaction.atomic
def visibility(actor, work_id, hidden, note):
    services.require_active(actor); note = reason(note); row = get_work(work_id)
    before = {'hidden': row.hidden_at is not None}
    row.hidden_at = timezone.now() if hidden else None
    row.save(update_fields=['hidden_at', 'updated_at'])
    corrected(work_id=work_id)
    services.audit(actor, 'work.visibility', row, before, {'hidden': hidden}, note)


@transaction.atomic
def moderate_reason(actor, pk, action, note):
    services.require_active(actor); note = reason(note)
    row = Recommendation.objects.filter(pk=pk).first()
    if row is None:
        raise services.DomainError('not_found', '理由不存在', 404)
    if action not in ('hide', 'restore', 'invalidate'):
        raise services.DomainError('invalid_action', '操作无效')
    if row.archived_at or row.invalidated_at:
        raise services.DomainError('archived_record', '记录已归档或失效', 409)
    before = {'reason': row.reason, 'hidden': row.text_hidden_at is not None, 'invalidated': False}
    if action == 'invalidate':
        row.invalidated_at = timezone.now()
    else:
        row.text_hidden_at = timezone.now() if action == 'hide' else None
    row.save(update_fields=['text_hidden_at', 'invalidated_at', 'updated_at'])
    if row.activity_id:
        corrected(activity_id=row.activity_id)
    services.audit(actor, 'reason.'+action, row, before,
        {'hidden': row.text_hidden_at is not None, 'invalidated': row.invalidated_at is not None}, note)


def release_pending(work, actor, note, target_work=None):
    """Approval preserves the original submission and creates no public +1 for monthly entries."""
    books.enroll(work)
    created = {}; seen = set()
    for submission in PendingActivitySubmission.objects.filter(work=work).order_by('-created_at', '-id'):
        key = (submission.activity_id, submission.anonymous_client_id)
        if key in seen: continue
        seen.add(key)
        period = submission.activity
        if period.status in ('draft', 'cancelled'):
            continue
        snap = ActivityWork.objects.filter(work=work, activity=period, archived_at__isnull=True).first()
        if snap is None:
            snap = ActivityWork.objects.create(work=work, activity=period, snapshot_title=work.display_title,
                snapshot_author=work.author_override if work.author_overridden else work.wenku8_author or '')
        if not Recommendation.objects.filter(work=work, activity=period, anonymous_client=submission.anonymous_client,
                archived_at__isnull=True, invalidated_at__isnull=True, withdrawn_at__isnull=True).exists():
            rec = Recommendation.objects.create(work=work, activity=period, activity_work=snap, scope='activity',
                anonymous_client=submission.anonymous_client, reason=submission.reason)
            Recommendation.objects.filter(pk=rec.pk).update(created_at=submission.created_at, updated_at=submission.created_at)
            created[rec.pk] = 'pending:'+str(submission.pk)
        has_target_reason = target_work and books.reasons().filter(work=target_work, anonymous_client=submission.anonymous_client).exists()
        if submission.copy_public_reason and submission.reason and not has_target_reason and not books.reasons().filter(work=work, anonymous_client=submission.anonymous_client).exists():
            books.set_reason(work, submission.anonymous_client, submission.reason)
        corrected(activity_id=period.pk)
        services.audit(actor, 'pending.activity.approve', submission, after={'work_id': work.pk}, reason=note)
    return created


@transaction.atomic
def approve(actor, work_id, note):
    services.require_active(actor); note = reason(note); work = get_work(work_id)
    if work.review_status != 'pending' or work.hidden_at:
        raise services.DomainError('not_pending', '作品不在待核查状态或已隐藏', 409)
    work.review_status = 'clear'; work.save(update_fields=['review_status', 'updated_at'])
    release_pending(work, actor, note)
    services.audit(actor, 'work.approve', work, {'review_status': 'pending'}, {'review_status': 'clear'}, note)


@transaction.atomic
def invalidate_relation(actor, kind, pk, note):
    services.require_active(actor); note = reason(note)
    models = {'recommendation': PublicRecommendation, 'like': PublicLike, 'vote': Vote}
    if kind not in models:
        raise services.DomainError('invalid_action', '记录类型无效')
    row = models[kind].objects.filter(pk=pk).first()
    if row is None:
        raise services.DomainError('not_found', '记录不存在', 404)
    field = 'cancelled_at' if kind == 'vote' else 'invalidated_at'
    previous = getattr(row, field)
    before = {field: previous.isoformat() if previous else None}
    if kind == 'vote':
        row.cancelled_at = timezone.now(); field = 'cancelled_at'
        corrected(activity_id=row.activity_id)
    else:
        row.invalidated_at = timezone.now(); field = 'invalidated_at'
    row.save(update_fields=[field, 'updated_at'])
    services.audit(actor, kind+('.cancel' if kind == 'vote' else '.invalidate'), row,
        before=before, after={field: getattr(row, field).isoformat()}, reason=note)
