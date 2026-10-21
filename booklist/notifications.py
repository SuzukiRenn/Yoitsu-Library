"""Work likes notify current recommenders in the same scope, once per actor/work/scope."""
from django.db import transaction
from .models import Account, WorkLikeEvent, Notification


@transaction.atomic
def work_liked(work, client, activity=None):
    actor = Account.objects.filter(principal=client, is_active=True).first()
    if actor is None:
        return
    key = f'activity:{activity.pk}' if activity else 'public'
    # Keep ledgers on their original works for reversible merges; inspect the
    # entire currently merged family so merging cannot reset notification limits.
    from .models import Work
    family = {work.pk}
    while True:
        expanded = family | set(Work.objects.filter(merged_into_id__in=family).values_list('id', flat=True))
        if expanded == family:
            break
        family = expanded
    if WorkLikeEvent.objects.filter(work_id__in=family, actor=actor, scope_key=key).exists():
        return
    event, created = WorkLikeEvent.objects.get_or_create(work=work, actor=actor, scope_key=key)
    if not created:
        return
    if activity:
        from .activities import recs
        query = recs(activity.pk)
    else:
        from .public_booklist import recommendations
        query = recommendations()
    recipients = Account.objects.filter(is_active=True, principal_id__in=query.filter(work=work).values('anonymous_client_id')).exclude(pk=actor.pk)
    Notification.objects.bulk_create([Notification(event=event, recipient=recipient,
        work_title=work.display_title, activity_title=activity.title if activity else '') for recipient in recipients])


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
    rows = list(history.filter(work_id__in=work_ids).order_by('created_at', 'id'))
    owners = {account.principal_id: account for account in Account.objects.filter(
        principal_id__in={row.anonymous_client_id for row in rows})}
    firsts = {}
    seen = set()
    for row in rows:
        account = owners.get(row.anonymous_client_id)
        person = {'username': account.username if account else '',
            'display_name': account.display_name if account else '历史匿名推荐者',
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
