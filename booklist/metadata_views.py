from django.http import JsonResponse
from . import catalog, user_metadata, public_booklist as books, services
from .catalog_views import page, parameters, read_limit
from .public_views import write
from .views import endpoint


@endpoint('GET')
def edit(request, data, work_id):
    parameters(request, ()); read_limit(request)
    work = books.require_work(work_id, public=False)
    if not user_metadata.editable(work, request.anonymous_client):
        raise services.DomainError('metadata_forbidden', '仅原提交者可编辑未匹配作品的资料', 403)
    return page(request, 'booklist/edit_metadata.html', {'work': catalog.work_detail(work_id),
        'metadata': user_metadata.values(work, request.anonymous_client)})


@endpoint('POST', fields={'description': str, 'tags': list, 'revision': int})
def save(request, data, work_id):
    def operation():
        work = books.require_work(work_id, public=False)
        return user_metadata.update(work, request.anonymous_client,
            {'description': data['description'], 'tags': data['tags']}, data['revision'])
    return write(request, f'work.metadata.{work_id}', data, operation)
