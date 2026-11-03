"""Account interaction notices, deduplicated independently by kind and scope."""
from django.db import transaction
from django.utils import timezone
from .models import Account, WorkLikeEvent, Notification, Work


def merged_family(work):
    family = {work.pk}
    while True:
        expanded = family | set(Work.objects.filter(merged_into_id__in=family).values_list('id', flat=True))
        if expanded == family:
            return family
        family = expanded


def visible_inbox(account):
    return Notification.objects.filter(recipient=account, retracted_at__isnull=True)


def retract_co_recommendation(work, client, activity=None):
    # Preserve the permanent event ledger: rejoining must not create repeat notices.
    key = f'activity:{activity.pk}' if activity else 'public'
    return Notification.objects.filter(retracted_at__isnull=True,
        event__kind='co_recommendation', event__scope_key=key,
        event__actor__principal=client, event__work_id__in=merged_family(work)
    ).update(retracted_at=timezone.now())


def current_recommenders(work, activity=None):
    if activity:
        from .activities import recs
        query = recs(activity.pk)
    else:
        from .public_booklist import recommendations
        query = recommendations()
    return Account.objects.filter(is_active=True, principal__revoked_at__isnull=True,
        principal_id__in=query.filter(work=work).values('anonymous_client_id'))


@transaction.atomic
def notify(work, client, kind, recipients, activity=None, subject_id=0):
    actor = Account.objects.filter(principal=client, is_active=True, principal__revoked_at__isnull=True).first()
    if actor is None:
        return
    key = f'activity:{activity.pk}' if activity else 'public'
    # Merges never reset dedup history; retain original work and reason IDs for undo.
    events = WorkLikeEvent.objects.filter(work_id__in=merged_family(work), actor=actor,
        scope_key=key, kind=kind, subject_id=subject_id)
    if events.exists():
        return
    event, created = WorkLikeEvent.objects.get_or_create(work=work, actor=actor,
        scope_key=key, kind=kind, subject_id=subject_id)
    if not created:
        return
    title = work.display_title
    if activity:
        from .models import ActivityWork
        title = ActivityWork.objects.filter(activity=activity, work=work,
            archived_at__isnull=True, merged_into__isnull=True).values_list('snapshot_title', flat=True).first() or title
    Notification.objects.bulk_create([Notification(event=event, recipient=recipient,
        work_title=title, activity_title=activity.title if activity else '')
        for recipient in recipients.exclude(pk=actor.pk)])


def work_liked(work, client, activity=None):
    notify(work, client, 'work_like', current_recommenders(work, activity), activity)


def co_recommended(work, client, activity=None):
    # Pending, hidden and retired catalog records do not generate public messages.
    from .catalog import visible_works
    if not visible_works().filter(pk=work.pk).exists():
        return
    notify(work, client, 'co_recommendation', current_recommenders(work, activity), activity)


def reason_liked(reason, client):
    recipients = Account.objects.filter(principal_id=reason.anonymous_client_id,
        is_active=True, principal__revoked_at__isnull=True)
    notify(reason.work, client, 'reason_like', recipients, reason.activity, subject_id=reason.pk)


def attribution(query, work_ids):
    """Preserve the first recommender of this cycle even if they withdraw."""
    grouped = {pk: [] for pk in work_ids}
    from .models import PublicRecommendation, Recommendation
    if query.model is PublicRecommendation:
        history = PublicRecommendation.objects.filter(archived_at__isnull=True, invalidated_at__isnull=True)
    else:
        activity_ids = query.values('activity_id')
        history = Recommendation.objects.filter(scope='activity', activity_id__in=activity_ids,
            archived_at__isnull=True, invalidated_at__isnull=True,
            activity_work__archived_at__isnull=True, activity_work__merged_into__isnull=True)
    rows = list(history.filter(work_id__in=work_ids, anonymous_client__imported_attribution__isnull=True).order_by('created_at', 'id'))
    owners = {account.principal_id: account for account in Account.objects.filter(
        principal_id__in={row.anonymous_client_id for row in rows})}
    firsts = {}
    seen = set()
    for row in rows:
        account = owners.get(row.anonymous_client_id)
        person = {'display_name': account.display_name if account else '历史匿名推荐者',
            'withdrawn': row.withdrawn_at is not None}
        firsts.setdefault(row.work_id, (row.anonymous_client_id, person))
        if row.withdrawn_at is None and (row.work_id, row.anonymous_client_id) not in seen:
            seen.add((row.work_id, row.anonymous_client_id))
            grouped[row.work_id].append((row.anonymous_client_id, person))
    result = {}
    for pk, names in grouped.items():
        first_id, first = firsts.get(pk, (None, None))
        # A rejoined original recommender is active again.
        active_first = next((person for client_id, person in names if client_id == first_id), None)
        result[pk] = {'first_recommender': active_first or first,
            'co_recommenders': [person for client_id, person in names if client_id != first_id],
            'recommenders': [person for _, person in names]}
    return result
