"""Private account shelf, derived from effective contributions, without new ownership."""
from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from django.db.models import OuterRef, Subquery, Q
from django.db.models.functions import Coalesce, Greatest
from . import catalog, public_booklist as books, accounts
from .models import Recommendation, Vote, ActivityWork
from .protocol import pagination
from .list_pagination import navigation


def contributions(principal, kind):
    public = (books.recommendations() if kind == 'recommendations' else books.likes()).filter(
        anonymous_client=principal, work_id=OuterRef('pk'), work_id__in=catalog.public_works().values('pk'))
    activity = (Recommendation.objects.filter(withdrawn_at__isnull=True, invalidated_at__isnull=True)
                if kind == 'recommendations' else Vote.objects.filter(cancelled_at__isnull=True))
    activity = activity.filter(anonymous_client=principal, work_id=OuterRef('pk'), scope='activity',
        archived_at__isnull=True, activity_work__archived_at__isnull=True,
        activity_work__merged_into__isnull=True).exclude(activity__status__in=['draft','cancelled'])
    rows = catalog.visible_works(include_retired=True).annotate(
        public_at=Subquery(public.order_by('-created_at','-id').values('created_at')[:1]),
        activity_at=Subquery(activity.order_by('-created_at','-id').values('created_at')[:1]),
        last_activity_id=Subquery(activity.order_by('-created_at','-id').values('activity_id')[:1]),
        last_snapshot_id=Subquery(activity.order_by('-created_at','-id').values('activity_work_id')[:1]))
    return rows.filter(Q(public_at__isnull=False) | Q(activity_at__isnull=False)).annotate(
        contribution_at=Greatest(Coalesce('public_at','activity_at'), Coalesce('activity_at','public_at'))
    ).order_by('-contribution_at','-id')


def context(request):
    principal = accounts.require(request).principal
    tab = request.GET.get('tab','recommendations')
    if tab not in ('recommendations','likes'):
        raise ValidationError('个人主页分类无效')
    offset, size = pagination(request.GET.get('page','1'), 12)
    queries = {key:contributions(principal,key) for key in ('recommendations','likes')}
    counts = {key:query.count() for key,query in queries.items()}
    result = {'tab':tab, 'page':offset//size+1, 'page_size':size, 'total':counts[tab],
              'recommended_total':counts['recommendations'], 'liked_total':counts['likes']}
    result['paging'] = navigation(result, lambda number:'?'+urlencode({'tab':tab,'page':number}))
    rows = list(queries[tab][offset:offset+size])
    works = {work['id']:work for work in catalog.serialize_works(rows)}
    snapshots = {snap.pk:snap for snap in ActivityWork.objects.filter(
        pk__in=[row.last_snapshot_id for row in rows if row.last_snapshot_id]).select_related('activity')}
    entries = []
    for row in rows:
        work = works[row.pk]
        activity_latest = row.activity_at is not None and (row.public_at is None or row.activity_at >= row.public_at)
        snap = snapshots.get(row.last_snapshot_id) if activity_latest else None
        if snap:
            work.update(title=snap.snapshot_title,author=snap.snapshot_author,
                        cover_url=catalog.cover_url(snap.snapshot_cover_url))
        entries.append({'work':work, 'at':row.contribution_at,
            'scope':snap.activity.title if snap else '公共书架',
            'url':f'/works/{row.pk}/'+(f'?activity={snap.activity_id}' if snap else '')})
    result['contributions'] = entries
    return result
