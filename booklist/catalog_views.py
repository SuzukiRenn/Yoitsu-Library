from django.http import JsonResponse
from django.shortcuts import render
from django.core.exceptions import ValidationError
from . import catalog, services
from .views import endpoint


def parameters(request, allowed):
    if set(request.GET)-set(allowed) or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
        raise ValidationError('查询参数重复或含未知字段')


def read_limit(request, search=False):
    services.rate_limit('catalog_search' if search else 'catalog_read', request.META.get('REMOTE_ADDR', ''), 60 if search else 120, 60)


def page(request, template, context):
    context = {**context, 'navigation_monthly': request.path.startswith('/activities/') or bool(context.get('activity'))}
    response = render(request, template, context)
    response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    return response


@endpoint('GET')
def search(request, data):
    parameters(request, ('q', 'limit'))
    read_limit(request, search=True)
    raw = request.GET.get('limit', '10')
    if not raw.isascii() or not raw.isdecimal() or str(int(raw)) != raw:
        raise ValidationError('候选数量无效')
    return JsonResponse(catalog.search(request.GET.get('q', ''), int(raw)))


@endpoint('GET')
def detail(request, data, work_id):
    parameters(request, ())
    read_limit(request)
    return JsonResponse(catalog.work_detail(work_id))


@endpoint('GET')
def home(request, data):
    parameters(request, ('q',))
    query = request.GET.get('q', '')
    read_limit(request, search=bool(query))
    result = catalog.search(query) if query else {'results': catalog.recent_works()}
    return page(request, 'booklist/catalog.html', {'query': query, 'works': result['results'], 'searched': bool(query)})


@endpoint('GET')
def work_page(request, data, work_id):
    parameters(request, ('activity',))
    read_limit(request)
    from . import public_booklist as books
    work = catalog.work_detail(work_id)
    if 'activity' in request.GET:
        from . import activities
        from .recommend_views import positive
        activity_id = positive(request.GET['activity'])
        activities.advance_due()
        result = activities.listing(activity_id, request.anonymous_client, page_size=1, work_id=work_id)
        interaction = result['results'][0] if result['results'] else None
        # Historical title/author/cover stay tied to the activity snapshot; introduction is current metadata.
        if interaction:
            work.update({key: interaction[key] for key in ('title', 'author', 'cover_url')})
        else:
            from .models import ActivityWork
            snapshot = ActivityWork.objects.filter(activity_id=activity_id, work_id=work_id,
                archived_at__isnull=True, merged_into__isnull=True).first()
            if snapshot is None:
                raise services.DomainError('not_found', '本期作品不存在或暂不可见', 404)
            # A zero-contribution entry leaves the list, but its introduction remains readable.
            # Rejoining still uses the standalone recommendation form, never a stale +1 button.
            work.update(title=snapshot.snapshot_title, author=snapshot.snapshot_author,
                        cover_url=catalog.cover_url(snapshot.snapshot_cover_url))
        return page(request, 'booklist/work.html', {'work': work, 'interaction': interaction, 'activity': result['activity']})
    interaction = books.serialize([books.require_work(work_id)], request.anonymous_client)[0] if work['is_public'] else None
    return page(request, 'booklist/work.html', {'work': work, 'interaction': interaction})
