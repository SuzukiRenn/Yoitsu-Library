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
    context = {**context, 'navigation_activities': request.path.startswith('/activities/') or bool(context.get('activity'))}
    response = render(request, template, context)
    response['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    return response


@endpoint('GET')
def search(request, data):
    parameters(request, ('q', 'limit', 'offset'))
    read_limit(request, search=True)
    raw = request.GET.get('limit', '10')
    if not raw.isascii() or not raw.isdecimal() or str(int(raw)) != raw:
        raise ValidationError('候选数量无效')
    return JsonResponse(catalog.search(request.GET.get('q', ''), int(raw), search_offset(request)))


def search_offset(request):
    raw = request.GET.get('offset', '0')
    if len(raw) > 6 or not raw.isascii() or not raw.isdecimal() or str(int(raw)) != raw:
        raise ValidationError('候选位置无效')
    return int(raw)


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
    parameters(request, ('activity', 'from'))
    read_limit(request)
    from . import public_booklist as books
    from . import user_metadata, activities
    from .recommend_views import positive
    return_url = shelf_return_url(request.GET.get('from', ''), request.GET.get('activity'))
    period = activities.get(positive(request.GET['activity'])) if 'activity' in request.GET else None
    if period: activities.advance_due(); period.refresh_from_db()
    historical = period is not None and period.status == 'ended'
    work = catalog.work_detail(work_id, include_retired=historical)
    current_work = catalog.visible_works().filter(pk=work_id).first()
    can_edit_metadata = user_metadata.editable(current_work, request.anonymous_client) if current_work else False
    if 'activity' in request.GET:
        from . import activities
        from .recommend_views import positive
        activity_id = positive(request.GET['activity'])
        activities.advance_due()
        result = activities.listing(activity_id, request.anonymous_client, page_size=1, work_id=work_id)
        interaction = result['results'][0] if result['results'] else None
        # Activity tags match the snapshot used by its shelf filter; introduction is current metadata.
        if interaction:
            work.update({key: interaction[key] for key in ('title', 'author', 'cover_url', 'tags')})
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
        if historical:
            from .models import ActivityWork
            history = ActivityWork.objects.filter(activity_id=activity_id, work_id=work_id, archived_at__isnull=True, merged_into__isnull=True).first()
            if history and history.snapshot_description is not None:
                work.update(description=history.snapshot_description, tags=history.snapshot_tags or [])
            can_edit_metadata = False
        return page(request, 'booklist/work.html', {'work': work, 'interaction': interaction, 'activity': result['activity'], 'can_edit_metadata': can_edit_metadata, 'return_url': return_url})
    interaction = books.serialize([books.require_work(work_id)], request.anonymous_client)[0] if work['is_public'] else None
    return page(request, 'booklist/work.html', {'work': work, 'interaction': interaction, 'can_edit_metadata': can_edit_metadata, 'return_url': return_url})


def shelf_return_url(value, activity=None):
    from urllib.parse import urlsplit, parse_qs
    from .protocol import pagination
    from .shelf_filters import search_text, tag_ids
    from .shelf_sort import validate, validate_period
    fallback = f'/activities/{activity}/' if activity and activity.isascii() and activity.isdecimal() and str(int(activity)) == activity and int(activity) > 0 else '/'
    try:
        if not value or len(value) > 4096 or any(ord(char) < 32 for char in value) or '\\' in value:
            return fallback
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path != fallback:
            return fallback
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=30)
        allowed = {'page', 'page_size', 'sort', 'q', 'tag'} | ({'period'} if fallback == '/' else set())
        if set(params) - allowed or any(len(values) != 1 for key, values in params.items() if key != 'tag'):
            return fallback
        pagination(params.get('page', ['1'])[0], params.get('page_size', ['20'])[0])
        search_text(params.get('q', [''])[0]); tag_ids(params.get('tag', []))
        validate(params.get('sort', ['recommendations'])[0]); validate_period(params.get('period', ['all'])[0])
        return value
    except (ValueError, ValidationError):
        return fallback
