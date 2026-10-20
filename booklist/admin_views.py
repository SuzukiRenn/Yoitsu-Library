from functools import wraps
from urllib.parse import urlencode
from django.http import JsonResponse
from django.shortcuts import redirect
from django.db.models import Q
from . import activities, moderation, merging, catalog
from .models import Work, Recommendation, Activity, ActivityTransition, AdminAuditLog, PublicRecommendation, PublicLike, Vote, WorkMergeOperation, SourceIdentityReview
from .views import endpoint
from .catalog_views import page, parameters
from .protocol import pagination


def admin_page(fn):
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        if request.admin is None:
            return redirect('/manage/login/')
        return fn(request, *args, **kwargs)
    return wrapped


@endpoint('GET')
def login(request, data):
    return page(request, 'booklist/admin/login.html', {})


@admin_page
@endpoint('GET', admin=True)
def index(request, data):
    parameters(request, ('section','q','page'))
    activities.advance_due()
    section = request.GET.get('section', 'works'); query = request.GET.get('q', '').strip()
    offset, size = pagination(request.GET.get('page','1'), 30)
    context = {'section':section, 'query':query, 'admin':request.admin}
    if section in ('works', 'pending'):
        rows = Work.objects.filter(merged_into__isnull=True).order_by('-id')
        if section == 'pending': rows = rows.filter(review_status='pending')
        if query: rows = rows.filter(Q(original_submitted_title__icontains=query) | Q(title_override__icontains=query) | Q(wenku8_title__icontains=query))
    elif section == 'activities': rows = Activity.objects.order_by('-start_at','-id')
    elif section == 'audit': rows = AdminAuditLog.objects.select_related('admin').order_by('-id')
    elif section == 'merges': rows = WorkMergeOperation.objects.select_related('primary_work','merged_work').order_by('-id')
    elif section == 'source_reviews': rows = SourceIdentityReview.objects.select_related('source_work', 'resolved_work').order_by('status', '-id')
    else:
        from .services import DomainError
        raise DomainError('invalid_section','页面不存在',404)
    context['rows'] = rows[offset:offset+size]
    if section == 'activities':
        for row in context['rows']: row.status_label = activities.LABELS[row.status]
    if section == 'merges':
        for row in context['rows']: row.status_label = '已合并' if row.status == 'completed' else '已撤销'
    context['next'] = '?'+urlencode({'section':section, 'q':query, 'page':offset//size+2}) if rows.count()>offset+size else None
    context['previous'] = '?'+urlencode({'section':section, 'q':query, 'page':offset//size}) if offset else None
    return page(request, 'booklist/admin/index.html', context)


@admin_page
@endpoint('GET', admin=True)
def work(request, data, work_id):
    parameters(request, ('page',))
    offset, size = pagination(request.GET.get('page','1'),30)
    row = moderation.get_work(work_id)
    queries = {'reasons':Recommendation.objects.filter(work=row).select_related('activity').order_by('-id'),
        'public_recs':PublicRecommendation.objects.filter(work=row, archived_at__isnull=True, invalidated_at__isnull=True, withdrawn_at__isnull=True).order_by('-id'),
        'public_likes':PublicLike.objects.filter(work=row, archived_at__isnull=True, invalidated_at__isnull=True, cancelled_at__isnull=True).order_by('-id'),
        'votes':Vote.objects.filter(work=row, scope='activity', archived_at__isnull=True, cancelled_at__isnull=True).select_related('activity').order_by('-id')}
    context = {'work': row, **{key: query[offset:offset+size] for key, query in queries.items()}}
    context['next'] = '?page='+str(offset//size+2) if any(query.count()>offset+size for query in queries.values()) else None
    context['previous'] = '?page='+str(offset//size) if offset else None
    return page(request, 'booklist/admin/work.html', context)


@admin_page
@endpoint('GET', admin=True)
def activity(request, data, activity_id):
    activities.advance_due()
    row = Activity.objects.filter(pk=activity_id).first()
    if row is None:
        from .services import DomainError
        raise DomainError('not_found','活动不存在',404)
    row.status_label = activities.LABELS[row.status]
    transitions = list(ActivityTransition.objects.filter(activity=row).order_by('-id')[:100])
    for event in transitions:
        event.from_label = activities.LABELS[event.from_status]; event.to_label = activities.LABELS[event.to_status]
    return page(request,'booklist/admin/activity.html',{'period':row, 'transitions':transitions})


@admin_page
@endpoint('GET', admin=True)
def merge_preview(request, data):
    parameters(request, ('primary','secondary'))
    primary, secondary = int(request.GET.get('primary','')), int(request.GET.get('secondary',''))
    return page(request, 'booklist/admin/merge.html', merging.plan(primary, secondary))


@admin_page
@endpoint('GET', admin=True)
def source_review(request, data, review_id):
    from .services import DomainError
    row = SourceIdentityReview.objects.select_related('source_work', 'resolved_work').filter(pk=review_id).first()
    if row is None: raise DomainError('not_found', '核对记录不存在', 404)
    return page(request, 'booklist/admin/source_review.html', {'review': row,
        'source': catalog.serialize_works([row.source_work], detail=True)[0]})


@endpoint('POST', admin=True, fields={'action': str, 'work_id': int, 'snapshot_hash': str, 'reason': str})
def resolve_source_review(request, data, review_id):
    from .source_review import resolve
    row = resolve(request.admin, review_id, data['action'], data['work_id'], data['snapshot_hash'], data['reason'])
    return JsonResponse({'status': 'saved', 'work_id': row.pk})


@endpoint('POST', admin=True, fields={'action':str, 'reason':str})
def work_action(request, data, work_id):
    if data['action'] == 'approve': moderation.approve(request.admin, work_id, data['reason'])
    elif data['action'] in ('hide','restore'): moderation.visibility(request.admin, work_id, data['action']=='hide', data['reason'])
    else:
        from .services import DomainError
        raise DomainError('invalid_action','操作无效')
    return JsonResponse({'status':'saved'})


@endpoint('POST', admin=True, fields=('title','author','reason'))
def create_work(request, data):
    row = moderation.create_work(request.admin,data['title'],data['author'],data['reason'])
    return JsonResponse({'status':'saved','id':row.pk},status=201)


@endpoint('POST', admin=True, fields={'action':str, 'reason':str})
def reason_action(request, data, reason_id):
    moderation.moderate_reason(request.admin, reason_id, data['action'], data['reason'])
    return JsonResponse({'status':'saved'})


@endpoint('POST', admin=True, fields={'reason':str})
def relation_action(request, data, kind, record_id):
    moderation.invalidate_relation(request.admin, kind, record_id, data['reason'])
    return JsonResponse({'status':'saved'})


@endpoint('POST', admin=True, fields={'primary':int,'secondary':int,'choices':dict,'version':int,'reason':str})
def merge(request, data):
    operation = merging.merge(request.admin, data['primary'], data['secondary'], data['choices'], data['version'], data['reason'])
    return JsonResponse({'status':'saved', 'id':operation.pk})


@endpoint('POST', admin=True, fields={'reason':str})
def undo(request, data, operation_id):
    merging.undo(request.admin, operation_id, data['reason'])
    return JsonResponse({'status':'saved'})
