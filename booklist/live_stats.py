"""Small, bounded interaction snapshots for cards already on screen."""
from django.core.exceptions import ValidationError
from django.db.models import Count, OuterRef, Subquery
from django.http import JsonResponse
from . import activities, catalog, public_booklist as books
from .catalog_views import parameters, read_limit
from .models import ActivityWork
from .views import endpoint


def snapshot(ids, client, activity_id=None):
    activity = activities.summary(activities.get(activity_id)) if activity_id is not None else None
    works = catalog.visible_works(include_retired=bool(activity and activity['status'] == 'ended')).filter(pk__in=ids, public_added_at__isnull=False)
    if activity_id is not None:
        works = works.filter(pk__in=ActivityWork.objects.filter(activity_id=activity_id,
            archived_at__isnull=True, merged_into__isnull=True).values('work_id'))
        recs, likes = activities.recs(activity_id), activities.votes(activity_id)
        reasons = recs.filter(text_hidden_at__isnull=True).exclude(reason='')
    else:
        recs, likes = books.recommendations(), books.likes()
        reasons = books.reasons().filter(text_hidden_at__isnull=True).exclude(reason='')
    recs, likes, reasons = [query.filter(work_id__in=ids) for query in (recs, likes, reasons)]
    def counts(query):
        return dict(query.values('work_id').annotate(total=Count('id')).values_list('work_id', 'total'))
    rc, lc, reason_counts = counts(recs), counts(likes), counts(reasons)
    own_recs = set(recs.filter(anonymous_client=client).values_list('work_id', flat=True)) if client else set()
    own_likes = set(likes.filter(anonymous_client=client).values_list('work_id', flat=True)) if client else set()
    own_reasons = dict((recs if activity else books.reasons().filter(work_id__in=ids)).filter(
        anonymous_client=client).values_list('work_id', 'reason')) if client else {}
    from .reason_likes import ranked, entries
    previews = dict(works.annotate(preview=Subquery(ranked(reasons.filter(work_id=OuterRef('pk'))).values('id')[:1])).values_list('pk', 'preview'))
    top_reasons = {row['id']: row for row in entries(reasons.filter(pk__in=[pk for pk in previews.values() if pk]), client,
        writable=activity['writable'] if activity else True)}
    result = []
    for pk in ids:
        if pk not in previews:
            result.append({'id': pk, 'available': False})
            continue
        result.append({'id': pk, 'available': True, 'recommendation_count': rc.get(pk, 0),
            'like_count': lc.get(pk, 0), 'recommended': pk in own_recs, 'liked': pk in own_likes,
            'own_reason': own_reasons.get(pk, ''), 'reason_count': reason_counts.get(pk, 0),
            'reason_preview': top_reasons.get(previews[pk], {}).get('reason', ''), 'top_reason': top_reasons.get(previews[pk]), 'writable': activity['writable'] if activity else True,
            'can_join': bool(rc.get(pk, 0)) if activity else True, 'in_shelf': bool(rc.get(pk, 0))})
    return {'results': result, **({'activity': activity} if activity else {})}


@endpoint('GET')
def stats(request, data, activity_id=None):
    parameters(request, ('ids',))
    raw = request.GET.get('ids', '').split(',')
    if not 1 <= len(raw) <= 100 or any(not value.isascii() or not value.isdecimal() or
            len(value) > 18 or int(value) <= 0 or str(int(value)) != value for value in raw):
        raise ValidationError('作品编号无效')
    read_limit(request)
    if activity_id is not None:
        activities.advance_due()
    return JsonResponse(snapshot(list(dict.fromkeys(map(int, raw))), request.anonymous_client, activity_id))
