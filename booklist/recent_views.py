"""Private bounded account history. Browsing never creates a recommendation."""
from urllib.parse import urlencode
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from . import accounts,catalog,services
from .models import Account,RecentWorkView,Activity,ActivityWork
from .views import endpoint

MAX_RECENT=50


def require_owner(request,data):
    owner=accounts.require(request)
    if data['account_id']!=owner.pk:
        raise services.DomainError('account_changed','账户已更改，请刷新页面',409)
    return owner


def readable(work_id,activity_id=None):
    period=Activity.objects.exclude(status__in=['draft','cancelled']).filter(pk=activity_id).first() if activity_id else None
    if activity_id and period is None:
        return None
    work=catalog.visible_works(include_retired=bool(period and period.status=='ended')).filter(pk=work_id).first()
    if work is None:return None
    if period and not ActivityWork.objects.filter(activity=period,work=work,archived_at=None,merged_into=None).exists():return None
    return work


@endpoint('POST',fields={'work_id':int,'activity_id':(int,type(None)),'account_id':int})
@transaction.atomic
def visit(request,data):
    owner=require_owner(request,data)
    services.rate_limit('recent_view',str(owner.pk),120,60)
    if data['work_id']<=0 or (data['activity_id'] is not None and data['activity_id']<=0):
        raise services.DomainError('invalid_request','作品或活动编号无效')
    with transaction.atomic():
        if not Account.objects.get(pk=owner.pk).track_recent_views:return JsonResponse({'recorded':False})
        if readable(data['work_id'],data['activity_id']) is None:
            raise services.DomainError('not_found','作品暂不可访问',404)
        RecentWorkView.objects.update_or_create(account=owner,work_id=data['work_id'],
            defaults={'activity_id':data['activity_id'],'viewed_at':timezone.now()})
        keep=RecentWorkView.objects.filter(account=owner).order_by('-viewed_at','-id').values('pk')[:MAX_RECENT]
        RecentWorkView.objects.filter(account=owner).exclude(pk__in=keep).delete()
    return JsonResponse({'recorded':True})


@endpoint('POST',fields={'account_id':int,'action':str},optional_fields={'work_id':int,'enabled':bool})
@transaction.atomic
def manage(request,data):
    owner=require_owner(request,data)
    services.rate_limit('recent_manage',str(owner.pk),60,60)
    with transaction.atomic():
        if data['action']=='remove' and set(data)=={'account_id','action','work_id'} and data['work_id']>0:
            RecentWorkView.objects.filter(account=owner,work_id=data['work_id']).delete()
        elif data['action']=='clear' and set(data)=={'account_id','action'}:
            RecentWorkView.objects.filter(account=owner).delete()
        elif data['action']=='tracking' and set(data)=={'account_id','action','enabled'}:
            owner.track_recent_views=data['enabled'];owner.save(update_fields=['track_recent_views','updated_at'])
        else:raise services.DomainError('invalid_request','最近浏览操作无效')
    return JsonResponse({'status':'saved','enabled':owner.track_recent_views})


def entries(owner,offset,size,source):
    rows=list(RecentWorkView.objects.filter(account=owner).select_related('activity').order_by('-viewed_at','-id')[offset:offset+size])
    ids=[r.work_id for r in rows]
    current={w.pk:w for w in catalog.visible_works(include_retired=True).filter(pk__in=ids)}
    data={w['id']:w for w in catalog.serialize_works(list(current.values()))}
    snaps={(s.activity_id,s.work_id):s for s in ActivityWork.objects.filter(work_id__in=ids,
        activity_id__in=[r.activity_id for r in rows if r.activity_id],archived_at=None,merged_into=None)}
    result=[]
    for row in rows:
        work=current.get(row.work_id);visible=work is not None
        if row.activity_id:
            visible=visible and row.activity.status not in ('draft','cancelled') and (row.activity_id,row.work_id) in snaps
        if visible and work.source_metadata.get('manual_retired_at') and (not row.activity_id or row.activity.status!='ended'):visible=False
        if not visible:
            result.append({'work':{'id':row.work_id},'available':False,'at':row.viewed_at});continue
        view=dict(data[row.work_id])
        if row.activity_id:
            snap=snaps[(row.activity_id,row.work_id)]
            view.update(title=snap.snapshot_title,author=snap.snapshot_author,cover_url=catalog.cover_url(snap.snapshot_cover_url))
        params={'from':source}
        if row.activity_id:params['activity']=row.activity_id
        result.append({'work':view,'available':True,'at':row.viewed_at,
            'scope':row.activity.title if row.activity_id else '公共书架',
            'url':f'/works/{row.work_id}/?'+urlencode(params)})
    return result
