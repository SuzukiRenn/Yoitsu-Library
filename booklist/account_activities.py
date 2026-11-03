"""Account-only effective participation, grouped by activity, never public votes."""
from urllib.parse import urlencode
from django.db.models import Count,Exists,OuterRef,Subquery,Q
from django.db.models.functions import Coalesce,Greatest
from . import catalog,services,activities
from .models import Activity,ActivityWork,Recommendation,Vote
from .protocol import pagination
from .list_pagination import navigation


def relations(principal):
    common={'anonymous_client':principal,'scope':'activity','archived_at__isnull':True,
        'activity_work__archived_at__isnull':True,'activity_work__merged_into__isnull':True,
        'work_id__in':catalog.visible_works(include_retired=True).values('pk')}
    allowed=Q(activity__status='ended')|~Q(work__source_metadata__has_key='manual_retired_at')
    return (Recommendation.objects.filter(**common,withdrawn_at=None,invalidated_at=None).exclude(activity__status__in=['draft','cancelled']).filter(allowed),
            Vote.objects.filter(**common,cancelled_at=None).exclude(activity__status__in=['draft','cancelled']).filter(allowed))


def periods(principal):
    recs,likes=relations(principal)
    recs=recs.filter(activity_id=OuterRef('pk'));likes=likes.filter(activity_id=OuterRef('pk'))
    def count(query):return Subquery(query.order_by().values('activity_id').annotate(total=Count('work_id',distinct=True)).values('total'))
    rows=Activity.objects.exclude(status__in=['draft','cancelled']).annotate(
        recommended_count=Coalesce(count(recs),0),liked_count=Coalesce(count(likes),0),
        last_recommended=Subquery(recs.order_by('-created_at','-id').values('created_at')[:1]),
        last_liked=Subquery(likes.order_by('-created_at','-id').values('created_at')[:1]))
    return rows.filter(Q(recommended_count__gt=0)|Q(liked_count__gt=0)).annotate(
        participation_at=Greatest(Coalesce('last_recommended','last_liked'),Coalesce('last_liked','last_recommended'))
    ).order_by('-participation_at','-id')


def context(request,principal,periods_query):
    offset,size=pagination(request.GET.get('page','1'),12)
    selected=request.GET.get('activity')
    if selected is None:
        result={'page':offset//size+1,'page_size':size,'total':periods_query.count(),
            'participated_activities':[{**activities.summary(r),'recommended_count':r.recommended_count,
                'liked_count':r.liked_count,'at':r.participation_at} for r in periods_query[offset:offset+size]]}
        result['paging']=navigation(result,lambda n:'?'+urlencode({'tab':'activities','page':n}))
        return result
    from .recommend_views import positive
    period=periods_query.filter(pk=positive(selected)).first()
    if period is None:raise services.DomainError('not_found','暂无可查看的本期参与记录',404)
    recs,likes=[q.filter(activity=period,work_id=OuterRef('pk')) for q in relations(principal)]
    rows=catalog.visible_works(include_retired=period.status=='ended').annotate(
        own_recommended=Exists(recs),own_liked=Exists(likes),
        recent_rec=Subquery(recs.order_by('-created_at','-id').values('created_at')[:1]),
        recent_like=Subquery(likes.order_by('-created_at','-id').values('created_at')[:1]))
    rows=rows.filter(Q(own_recommended=True)|Q(own_liked=True)).annotate(
        participated_at=Greatest(Coalesce('recent_rec','recent_like'),Coalesce('recent_like','recent_rec'))).order_by('-participated_at','-id')
    result={'page':offset//size+1,'page_size':size,'total':rows.count(),
        'participation_activity':{**activities.summary(period),'recommended_count':period.recommended_count,'liked_count':period.liked_count}}
    page=list(rows[offset:offset+size]);info={w['id']:w for w in catalog.serialize_works(page)}
    snaps={s.work_id:s for s in ActivityWork.objects.filter(activity=period,work_id__in=[w.pk for w in page],archived_at=None,merged_into=None)}
    entries=[]
    for row in page:
        snap=snaps[row.pk];work=info[row.pk]
        work.update(title=snap.snapshot_title,author=snap.snapshot_author,cover_url=catalog.cover_url(snap.snapshot_cover_url))
        params={'activity':period.pk,'from':request.get_full_path()}
        entries.append({'work':work,'available':True,'at':row.participated_at,'scope':period.title,
            'recommended':row.own_recommended,'liked':row.own_liked,'url':f'/works/{row.pk}/?'+urlencode(params)})
    result['contributions']=entries
    result['paging']=navigation(result,lambda n:'?'+urlencode({'tab':'activities','activity':period.pk,'page':n}))
    return result
