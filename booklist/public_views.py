from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from .list_pagination import PAGE_SIZE, navigation
from .shelf_sort import controls, period_controls
from .shelf_filters import personal_filters, personal_parameters, tag_mode_parameters
from . import public_booklist as books, services
from .catalog_views import page, parameters, read_limit
from .protocol import pagination
from .views import endpoint


def list_data(request, fixed_size=None):
    if set(request.GET) - {'page', 'page_size', 'tag', 'sort', 'period', 'q', 'recommended', 'liked', 'tag_mode'} or any(len(request.GET.getlist(key)) != 1 for key in request.GET if key != 'tag'):
        raise ValidationError('查询参数无效')
    return books.listing(request.anonymous_client, request.GET.get('page', '1'), fixed_size if fixed_size is not None else request.GET.get('page_size', '20'), request.GET.getlist('tag'), request.GET.get('sort', 'recommendations'), period=request.GET.get('period', 'all'), q=request.GET.get('q', ''), tag_mode=request.GET.get('tag_mode', 'all'), **personal_filters(request.GET))


@endpoint('GET')
def home(request, data):
    read_limit(request)
    result = list_data(request, PAGE_SIZE)
    def link(number):
        return '?' + urlencode(personal_parameters(result) + tag_mode_parameters(result) + [('tag', tag) for tag in result['selected_tags']] + ([('q', result['query'])] if result['query'] else []) + [('page_size', result['page_size']), ('sort', result['sort']), ('period', result['period']), ('page', number)])
    result['paging'] = navigation(result, link)
    result['sort_options'] = controls(result)
    result['period_options'] = period_controls(result)
    if result['page'] > result['paging']['count']:
        return redirect(request.path + result['paging']['last_url'])
    return page(request, 'booklist/public.html', result)


@endpoint('GET')
def random_book(request, data):
    parameters(request, ('exclude', 'activity'))
    read_limit(request)
    from .recommend_views import positive
    def identifier(name):
        value = request.GET.get(name)
        if value is None:
            return None
        if len(value) > 18:
            raise ValidationError('编号无效')
        return positive(value)
    current = identifier('exclude')
    activity_id = identifier('activity')
    activity = None
    if activity_id is not None:
        from . import activities, catalog
        from .models import ActivityWork
        activities.advance_due()
        period = activities.get(activity_id)
        activity = activities.summary(period)
        snapshots = ActivityWork.objects.filter(activity=period, archived_at__isnull=True,
            merged_into__isnull=True, pk__in=activities.recs(activity_id).values('activity_work_id'))
        eligible = catalog.visible_works(include_retired=period.status == 'ended').filter(pk__in=snapshots.values('work_id'))
    else:
        eligible = books.public_works()
    work_id = eligible.exclude(pk=current).order_by('?').values_list('pk', flat=True).first() if current is not None else eligible.order_by('?').values_list('pk', flat=True).first()
    suffix = f'?activity={activity_id}' if activity_id is not None else ''
    if work_id is not None:
        response = redirect(f'/works/{work_id}/{suffix}')
    elif current is not None and eligible.filter(pk=current).exists():
        response = page(request, 'booklist/random_single.html', {'current_work_id': current, 'activity': activity})
    else:
        response = page(request, 'booklist/random_empty.html', {'activity': activity})
    response['Cache-Control'] = 'no-store'
    return response


@endpoint('GET')
def listing(request, data):
    read_limit(request)
    return JsonResponse(list_data(request))


def write(request, scope, data, operation):
    client = request.anonymous_client
    if client is None:
        raise services.DomainError('identity_required', '需要匿名身份', 401)
    services.rate_limit('public_write_ip', request.META.get('REMOTE_ADDR', ''), 120, 60)
    services.rate_limit('public_write_actor', str(client.pk), 60, 60)
    return JsonResponse(services.idempotent(client, scope, request.headers.get('Idempotency-Key'), data, operation))


@endpoint('POST', fields={'work_id': (int, type(None)), 'title': str, 'reason': str, 'confirmed_unmatched': bool}, optional_fields={'metadata': dict, 'contains_spoilers': bool, 'references':list})
def submit(request, data):
    return write(request, 'public.submit', data, lambda: books.submit(request.anonymous_client, data))


@endpoint('POST', fields={'enabled': bool}, optional_fields={'confirmation': str})
def recommend(request, data, work_id):
    return relation(request, data, work_id, 'recommend')


@endpoint('POST', fields={'enabled': bool})
def like(request, data, work_id):
    return relation(request, data, work_id, 'like')


def relation(request, data, work_id, kind):
    def operation():
        work = books.require_work(work_id)
        if kind == 'recommend' and not data['enabled']:
            from .withdrawal import require_confirmation
            require_confirmation(work, request.anonymous_client, token=data.get('confirmation'))
        books.set_relation(work, request.anonymous_client, kind, data['enabled'])
        result = books.serialize([work], request.anonymous_client)[0]
        result.update(in_shelf=result['recommendation_count'] > 0, resubmission_required='manual_retired_at' in work.source_metadata)
        return {'work': result}
    return write(request, f'public.{kind}.{work_id}', data, operation)


@endpoint('POST', fields={'reason': str}, optional_fields={'contains_spoilers': bool, 'references':list})
def reason(request, data, work_id):
    def operation():
        work = books.require_work(work_id)
        if not books.recommendations().filter(work=work, anonymous_client=request.anonymous_client).exists():
            raise services.DomainError('recommendation_required', '请先推荐这本书，再写推荐理由', 409)
        books.set_reason(work, request.anonymous_client, data['reason'], data.get('contains_spoilers'), data.get('references'))
        return {'work': books.serialize([work], request.anonymous_client)[0]}
    return write(request, f'public.reason.{work_id}', data, operation)


@endpoint('GET')
def reasons(request, data, work_id):
    parameters(request, ('page', 'page_size'))
    read_limit(request)
    books.require_work(work_id)
    offset, size = pagination(request.GET.get('page', '1'), request.GET.get('page_size', '20'))
    query = books.reasons().filter(work_id=work_id, text_hidden_at__isnull=True).order_by('created_at', 'id')
    from .reason_likes import entries
    return JsonResponse({'total': query.count(), 'results': entries(query, request.anonymous_client, offset=offset, limit=size)})
