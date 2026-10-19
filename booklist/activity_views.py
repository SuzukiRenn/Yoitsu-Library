from django.http import JsonResponse
from django.core.exceptions import ValidationError
from . import activities, activity_admin, catalog, public_booklist as books
from .models import Activity
from .catalog_views import page, parameters, read_limit
from .public_views import write
from .views import endpoint
from .protocol import pagination


@endpoint('GET')
def index(request, data):
    parameters(request, ()); read_limit(request); activities.advance_due()
    periods = [activities.summary(row) for row in Activity.objects.exclude(status='draft').order_by('-start_at', '-id')]
    current = [row for row in periods if row['status'] in ('active','paused')]
    return page(request, 'booklist/activities.html', {'activity':current[0] if current else None,
        'current':current, 'upcoming':[row for row in reversed(periods) if row['status']=='scheduled'],
        'history':[row for row in periods if row['status'] in ('ended','cancelled')]})


def activity_result(request, activity_id):
    parameters(request, ('page', 'page_size')); read_limit(request); activities.advance_due()
    return activities.listing(activity_id, request.anonymous_client, request.GET.get('page', '1'), request.GET.get('page_size', '20'))


@endpoint('GET')
def detail(request, data, activity_id):
    result = activity_result(request, activity_id)
    base = f'?page_size={result["page_size"]}&page='
    result['previous'] = base+str(result['page']-1) if result['page']>1 else None
    result['next'] = base+str(result['page']+1) if result['page']*result['page_size']<result['total'] else None
    return page(request, 'booklist/activity.html', result)


@endpoint('GET')
def detail_api(request, data, activity_id):
    return JsonResponse(activity_result(request, activity_id))


FIELDS = {'work_id': (int, type(None)), 'title': str, 'reason': str, 'confirmed_unmatched': bool, 'copy_public_reason': bool}


@endpoint('POST', fields=FIELDS)
def submit(request, data, activity_id):
    activities.advance_due()
    return write(request, f'activity.submit.{activity_id}', data, lambda: activities.submit(activity_id, request.anonymous_client, data))


@endpoint('POST', fields={})
def withdraw(request, data, activity_id, work_id):
    activities.advance_due()
    return write(request, f'activity.withdraw.{activity_id}.{work_id}', data, lambda: activities.withdraw(activity_id, work_id, request.anonymous_client))


@endpoint('POST', fields={'enabled': bool})
def recommend(request, data, activity_id, work_id):
    activities.advance_due()
    return write(request, f'activity.recommend.{activity_id}.{work_id}', data,
                 lambda: activities.recommend(activity_id, work_id, request.anonymous_client, data['enabled']))


@endpoint('POST', fields={'enabled': bool})
def like(request, data, activity_id, work_id):
    activities.advance_due()
    return write(request, f'activity.like.{activity_id}.{work_id}', data, lambda: activities.like(activity_id, work_id, request.anonymous_client, data['enabled']))


@endpoint('POST', fields={'reason': str})
def reason(request, data, activity_id, work_id):
    activities.advance_due()
    return write(request, f'activity.reason.{activity_id}.{work_id}', data, lambda: activities.reason(activity_id, work_id, request.anonymous_client, data['reason']))


@endpoint('GET')
def reasons(request, data, activity_id, work_id):
    parameters(request, ('page','page_size')); read_limit(request); activities.advance_due()
    activities.get(activity_id); books.require_work(work_id)
    offset, size = pagination(request.GET.get('page','1'),request.GET.get('page_size','20'))
    query = activities.recs(activity_id).filter(work_id=work_id, text_hidden_at__isnull=True).exclude(reason='').order_by('created_at','id')
    return JsonResponse({'total':query.count(), 'results':list(query.values('reason')[offset:offset+size])})


@endpoint('POST', admin=True, fields=('title','theme','start_at','end_at'))
def create(request, data):
    return JsonResponse(activity_admin.create(request.admin, data), status=201)


@endpoint('POST', admin=True, fields=('title','theme','start_at','end_at','reason'))
def update(request, data, activity_id):
    activities.advance_due()
    return JsonResponse(activity_admin.update(request.admin, activity_id, data))


@endpoint('POST', admin=True, fields=('action','reason'))
def action(request, data, activity_id):
    activities.advance_due()
    return JsonResponse(activity_admin.action(request.admin, activity_id, data['action'],data['reason']))
