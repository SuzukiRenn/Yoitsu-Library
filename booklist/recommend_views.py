from django.core.exceptions import ValidationError
from django.http import JsonResponse
from . import catalog, public_booklist as books, activities
from .catalog_views import parameters, read_limit, page, search_offset
from .views import endpoint


def positive(value):
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value or int(value)<=0:
        raise ValidationError('ID 无效')
    return int(value)


@endpoint('GET')
def recommend(request, data, activity_id=None):
    from django.conf import settings
    if settings.REQUIRE_ACCOUNT and not request.account:
        from django.shortcuts import redirect
        from urllib.parse import urlencode
        return redirect('/account/login/?' + urlencode({'next': request.get_full_path()}))
    parameters(request, ('work','edit')); read_limit(request)
    editing = request.GET.get('edit') == '1'
    if 'edit' in request.GET and not editing:
        raise ValidationError('编辑参数无效')
    work_id = positive(request.GET['work']) if 'work' in request.GET else None
    if editing and work_id is None:
        raise ValidationError('缺少作品')
    period = None; work = catalog.work_detail(work_id) if work_id else None
    if activity_id:
        activities.advance_due(); period = activities.summary(activities.get(activity_id))
        if editing:
            own = activities.recs(activity_id).filter(work_id=work_id, anonymous_client=request.anonymous_client).first() if request.anonymous_client else None
            if own is None:
                from .services import DomainError
                raise DomainError('not_found','本期推荐不存在',404)
            work['own_reason'] = own.reason
        elif work:
            own = activities.recs(activity_id).filter(work_id=work_id, anonymous_client=request.anonymous_client).first() if request.anonymous_client else None
            work['own_reason'] = own.reason if own else ''
    elif work:
        work.update(books.serialize([books.require_work(work_id, public=editing)], request.anonymous_client)[0])
        if editing and not work['recommended']:
            from .services import DomainError
            raise DomainError('recommendation_required', '请先推荐这本书，再写推荐理由', 409)
    if work:
        if activity_id:
            work['recommendation_count'] = activities.recs(activity_id).filter(work_id=work_id).count()
            work['in_scope'] = work['recommendation_count'] > 0
            work['recommended'] = own is not None
        else:
            work['in_scope'] = work['is_public']
    prefix = f'/api/v1/activities/{activity_id}' if activity_id else '/api/v1/public'
    target = f'{prefix}/works/{work_id}/reason/' if editing else f'{prefix}/recommendations/'
    return page(request,'booklist/recommend.html', {'activity':period, 'work':work, 'editing':editing,
        'target':target, 'success_url':f'/activities/{activity_id}/' if activity_id else '/',
        'readonly':period is not None and not period['writable']})


@endpoint('GET')
def candidates(request, data):
    parameters(request, ('q','activity','offset')); read_limit(request, search=True)
    activity_id = positive(request.GET['activity']) if 'activity' in request.GET else None
    if activity_id:
        activities.advance_due(); activities.get(activity_id)
    payload = catalog.search(request.GET.get('q',''), offset=search_offset(request))
    client = request.anonymous_client
    for row in payload['results']:
        recs = activities.recs(activity_id).filter(work_id=row['id']) if activity_id else books.recommendations().filter(work_id=row['id'])
        own = recs.filter(anonymous_client=client).first() if client else None
        row['recommendation_count'] = recs.count(); row['recommended'] = own is not None
        if activity_id:
            row['own_reason'] = own.reason if own else ''
        else:
            reason = books.reasons().filter(work_id=row['id'], anonymous_client=client).first() if client else None
            row['own_reason'] = reason.reason if reason else ''
        row['in_scope'] = row['recommendation_count'] > 0 if activity_id else row['is_public']
    return JsonResponse(payload)
