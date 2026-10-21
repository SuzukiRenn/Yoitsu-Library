"""Administrator-only, consistent text snapshots for locally rendered share images."""
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone
from . import activities, catalog, services, cover_cache
from .admin_views import admin_page
from .catalog_views import page, parameters
from .models import ActivityWork
from .views import endpoint


@admin_page
@endpoint('GET', admin=True)
def preview(request, data, activity_id):
    parameters(request, ())
    activities.advance_due()
    period = activities.get(activity_id)
    response = page(request, 'booklist/admin/activity_export.html', {'period': period})
    response['Content-Security-Policy'] = response['Content-Security-Policy'].replace(
        "img-src 'self' https:", "img-src 'self' blob:")
    return response


@transaction.atomic
def snapshot(activity_id):
    period = activities.get(activity_id)
    snaps = list(ActivityWork.objects.filter(activity=period, archived_at__isnull=True,
        merged_into__isnull=True, work_id__in=catalog.visible_works(
            include_retired=period.status == 'ended').values('id')).order_by('id')[:1001])
    if len(snaps) > 1000:
        raise services.DomainError('export_too_large', '本期书目过多，暂无法一次生成图片', 422)
    ids = [row.pk for row in snaps]
    recs = activities.recs(period.pk).filter(activity_work_id__in=ids)
    counts = lambda query: dict(query.values('activity_work_id').annotate(total=Count('id'))
                               .values_list('activity_work_id', 'total'))
    recommended = counts(recs)
    liked = counts(activities.votes(period.pk).filter(activity_work_id__in=ids))
    reason_counts = counts(recs.filter(text_hidden_at__isnull=True).exclude(reason=''))
    ordered = sorted((row for row in snaps if recommended.get(row.pk, 0)),
                     key=lambda row: (-recommended[row.pk], -liked.get(row.pk, 0),
                                      -row.created_at.timestamp(), row.pk))
    works = [{'title': row.snapshot_title, 'author': row.snapshot_author,
              'cover_url': cover_cache.local_url(row.snapshot_cover_url),
              'recommendation_count': recommended[row.pk], 'like_count': liked.get(row.pk, 0),
              'reason_count': reason_counts.get(row.pk, 0)} for row in ordered]
    return {'activity': activities.summary(period), 'generated_label': timezone.localtime(
        timezone.now()).strftime('%Y-%m-%d %H:%M'), 'works': works,
        'total': len(works), 'recommendations': sum(row['recommendation_count'] for row in works)}


@endpoint('GET', admin=True)
def data(request, data, activity_id):
    parameters(request, ())
    services.rate_limit('admin_activity_export', str(request.admin.pk), 10, 60)
    activities.advance_due()
    return JsonResponse(snapshot(activity_id))
