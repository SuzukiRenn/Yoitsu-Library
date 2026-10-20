from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from .list_pagination import PAGE_SIZE, navigation
from .shelf_sort import controls
from . import public_booklist as books, services
from .catalog_views import page, parameters, read_limit
from .protocol import pagination
from .views import endpoint


def list_data(request, fixed_size=None):
    if set(request.GET) - {'page', 'page_size', 'tag', 'sort'} or any(len(request.GET.getlist(key)) != 1 for key in request.GET if key != 'tag'):
        raise ValidationError('查询参数无效')
    return books.listing(request.anonymous_client, request.GET.get('page', '1'), fixed_size if fixed_size is not None else request.GET.get('page_size', '20'), request.GET.getlist('tag'), request.GET.get('sort', 'recommendations'))


@endpoint('GET')
def home(request, data):
    read_limit(request)
    result = list_data(request, PAGE_SIZE)
    def link(number):
        return '?' + urlencode([('tag', tag) for tag in result['selected_tags']] + [('page_size', result['page_size']), ('sort', result['sort']), ('page', number)])
    result['paging'] = navigation(result, link)
    result['sort_options'] = controls(result)
    if result['page'] > result['paging']['count']:
        return redirect(request.path + result['paging']['last_url'])
    return page(request, 'booklist/public.html', result)


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


@endpoint('POST', fields={'work_id': (int, type(None)), 'title': str, 'reason': str, 'confirmed_unmatched': bool}, optional_fields={'metadata': dict})
def submit(request, data):
    return write(request, 'public.submit', data, lambda: books.submit(request.anonymous_client, data))


@endpoint('POST', fields={'enabled': bool})
def recommend(request, data, work_id):
    return relation(request, data, work_id, 'recommend')


@endpoint('POST', fields={'enabled': bool})
def like(request, data, work_id):
    return relation(request, data, work_id, 'like')


def relation(request, data, work_id, kind):
    def operation():
        work = books.require_work(work_id)
        books.set_relation(work, request.anonymous_client, kind, data['enabled'])
        result = books.serialize([work], request.anonymous_client)[0]
        result.update(in_shelf=result['recommendation_count'] > 0, resubmission_required='manual_retired_at' in work.source_metadata)
        return {'work': result}
    return write(request, f'public.{kind}.{work_id}', data, operation)


@endpoint('POST', fields={'reason': str})
def reason(request, data, work_id):
    def operation():
        work = books.require_work(work_id)
        if not books.recommendations().filter(work=work).exists():
            raise services.DomainError('not_in_public', '请先重新推荐这本书', 409)
        books.set_reason(work, request.anonymous_client, data['reason'])
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
