"""Archive completed recommendation cycles without erasing moderation or activity history."""
from django.utils import timezone
from .models import ActivityWork, Recommendation, Vote, PublicRecommendation, PublicLike, UserWorkMetadata, WorkTag


def freeze_activity_metadata(activity_id):
    from . import catalog
    for snap in ActivityWork.objects.filter(activity_id=activity_id, archived_at__isnull=True, merged_into__isnull=True):
        if not catalog.visible_works(include_retired=True).filter(pk=snap.work_id).exists():
            continue
        data = catalog.work_detail(snap.work_id, include_retired=True)
        snap.snapshot_description = data['description']
        snap.snapshot_tags = data['tags']
        snap.save(update_fields=['snapshot_description', 'snapshot_tags', 'updated_at'])


def manual_can_retire(work):
    return not (work.wenku8_book_id or work.merged_into_id or work.hidden_at or
        work.review_status != 'clear' or 'manual_retired_at' in work.source_metadata)


def retire_manual_if_unused(work):
    from . import catalog, public_booklist as books, services
    if not manual_can_retire(work):
        return
    if books.recommendations().filter(work=work).exists():
        return
    current = Recommendation.objects.filter(work=work, scope='activity', withdrawn_at__isnull=True,
        archived_at__isnull=True, invalidated_at__isnull=True, activity__status__in=['active', 'paused'],
        activity_work__archived_at__isnull=True, activity_work__merged_into__isnull=True)
    if current.exists():
        return
    # Fill legacy historical snapshots before retiring their live manual metadata.
    data = catalog.work_detail(work.pk, include_retired=True)
    for snap in ActivityWork.objects.filter(work=work, activity__status='ended',
            archived_at__isnull=True, snapshot_description__isnull=True):
        snap.snapshot_description = data['description']; snap.snapshot_tags = data['tags']
        snap.save(update_fields=['snapshot_description', 'snapshot_tags', 'updated_at'])
    now = timezone.now()
    UserWorkMetadata.objects.filter(work=work, archived_at__isnull=True).update(archived_at=now, updated_at=now)
    WorkTag.objects.filter(work=work, source_type='user', state='active').update(state='user_removed', removed_at=now, updated_at=now)
    work.source_metadata = {**work.source_metadata, 'manual_retired_at': now.isoformat()}
    work.save(update_fields=['source_metadata', 'updated_at'])
    services.audit(None, 'work.manual.retired', work, after={'resubmission_required': True})


def finish_public_cycle(work):
    now = timezone.now()
    Recommendation.objects.filter(work=work, scope='public', archived_at__isnull=True).update(archived_at=now, updated_at=now)
    PublicLike.objects.filter(work=work, archived_at__isnull=True).update(archived_at=now, updated_at=now)
    Vote.objects.filter(work=work, scope='public', archived_at__isnull=True).update(archived_at=now, updated_at=now)
    PublicRecommendation.objects.filter(work=work, archived_at__isnull=True).update(archived_at=now, updated_at=now)
    retire_manual_if_unused(work)


def finish_activity_cycle(activity_id, work):
    now = timezone.now()
    Recommendation.objects.filter(work=work, activity_id=activity_id, scope='activity', archived_at__isnull=True).update(archived_at=now, updated_at=now)
    Vote.objects.filter(work=work, activity_id=activity_id, scope='activity', archived_at__isnull=True).update(archived_at=now, updated_at=now)
    ActivityWork.objects.filter(work=work, activity_id=activity_id, archived_at__isnull=True).update(archived_at=now, updated_at=now)
    retire_manual_if_unused(work)


def reconcile_inactive_cycles(apply=False):
    """Explicit upgrade reconciliation; never runs during ordinary page requests."""
    from django.db import transaction
    from . import activities, catalog, public_booklist as books
    with transaction.atomic():
        activity_cycles = [(snap.activity_id, snap.work_id) for snap in ActivityWork.objects.filter(
            activity__status__in=['active', 'paused'], archived_at__isnull=True, merged_into__isnull=True)
            if not activities.recs(snap.activity_id).filter(activity_work=snap).exists()]
        public_cycles = []
        manual_ids = []
        for work in catalog.visible_works().filter(public_added_at__isnull=False):
            if books.recommendations().filter(work=work).exists():
                continue
            if (PublicRecommendation.objects.filter(work=work,archived_at__isnull=True).exists() or
                PublicLike.objects.filter(work=work,archived_at__isnull=True).exists() or
                Recommendation.objects.filter(work=work,scope='public',archived_at__isnull=True).exists()):
                public_cycles.append(work.pk)
            if work.wenku8_book_id is None and not Recommendation.objects.filter(work=work,scope='activity',
                    withdrawn_at__isnull=True,archived_at__isnull=True,invalidated_at__isnull=True,
                    activity__status__in=['active','paused'],activity_work__archived_at__isnull=True,
                    activity_work__merged_into__isnull=True).exists():
                manual_ids.append(work.pk)
        plan = {'activity_cycles':activity_cycles,'public_cycles':public_cycles,'manual_ids':manual_ids}
        if apply:
            from .models import Work
            for activity_id,work_id in activity_cycles:
                finish_activity_cycle(activity_id,Work.objects.get(pk=work_id))
            for work_id in public_cycles:
                finish_public_cycle(Work.objects.get(pk=work_id))
            for work_id in manual_ids:
                retire_manual_if_unused(Work.objects.get(pk=work_id))
        return plan
