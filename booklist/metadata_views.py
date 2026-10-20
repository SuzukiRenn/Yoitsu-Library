from django.http import JsonResponse
import json
from django.core.exceptions import ValidationError
from . import catalog, user_metadata, public_booklist as books, services
from .catalog_views import page, parameters, read_limit
from .public_views import write
from .views import endpoint


@endpoint('GET')
def tags(request, data):
    parameters(request, ('q', 'work', 'page', 'exclude')); read_limit(request, search=True)
    work = None
    if 'work' in request.GET:
        from .recommend_views import positive
        work = books.require_work(positive(request.GET['work']), public=False)
        if not user_metadata.editable(work, request.anonymous_client):
            raise services.DomainError('metadata_forbidden', '仅原提交者可编辑未匹配作品的资料', 403)
    excluded = request.GET.get('exclude', '[]')
    if len(excluded) > 4096: raise ValidationError('已选标签无效')
    return JsonResponse(user_metadata.tag_suggestions(request.GET.get('q', ''), work, request.anonymous_client,
        page=request.GET.get('page', '1'), exclude=json.loads(excluded)))


@endpoint('GET')
def edit(request, data, work_id):
    parameters(request, ()); read_limit(request)
    work = books.require_work(work_id, public=False)
    if not user_metadata.editable(work, request.anonymous_client):
        raise services.DomainError('metadata_forbidden', '仅原提交者可编辑未匹配作品的资料', 403)
    return page(request, 'booklist/edit_metadata.html', {'work': catalog.work_detail(work_id),
        'metadata': user_metadata.values(work, request.anonymous_client)})


@endpoint('POST', fields={'description': str, 'tags': list, 'revision': int}, optional_fields={'author': str})
def save(request, data, work_id):
    def operation():
        work = books.require_work(work_id, public=False)
        return user_metadata.update(work, request.anonymous_client,
            {'description': data['description'], 'tags': data['tags'], **({'author': data['author']} if 'author' in data else {})}, data['revision'])
    return write(request, f'work.metadata.{work_id}', data, operation)
