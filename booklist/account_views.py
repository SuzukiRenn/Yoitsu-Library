from urllib.parse import urlencode
from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect
from django.utils import timezone
from django.db import transaction
from . import accounts, services
from .catalog_views import page, parameters
from .models import Notification
from .views import endpoint, set_cookie
from .protocol import pagination
from .navigation import safe_return, login_url


def auth_limit(request, name='', registration=False):
    services.rate_limit('account_register' if registration else 'account_login_ip',
        request.META.get('REMOTE_ADDR', ''), 5 if registration else 20, 900)
    if not registration:
        services.rate_limit('account_login_name', name.strip().lower()[:100], 10, 900)


def authenticated(request, result):
    token, session = result
    if request.account_session:
        request.account_session.delete()
    rotate_token(request)
    response = JsonResponse({'account': accounts.public(session.account)})
    set_cookie(response, settings.ACCOUNT_COOKIE, token, settings.ACCOUNT_AGE)
    response.delete_cookie(settings.ANONYMOUS_COOKIE, samesite='Lax')
    return response


@endpoint('POST', fields=('username', 'display_name', 'password'))
def register(request, data):
    if settings.QQ_EMAIL_LOGIN:
        raise services.DomainError('email_login_required', '请使用 QQ 邮箱验证码登录', 409)
    auth_limit(request, registration=True)
    return authenticated(request, accounts.register(data['username'], data['display_name'], data['password']))


@endpoint('POST', fields=('username', 'password'))
def login(request, data):
    auth_limit(request, data['username'])
    result = accounts.login(data['username'], data['password'])
    if settings.QQ_EMAIL_LOGIN and result[1].account.qq_email:
        result[1].delete()
        raise services.DomainError('email_login_required', '已绑定邮箱，请使用验证码登录', 409)
    return authenticated(request, result)


@endpoint('POST')
def logout(request, data):
    if request.account_session:
        request.account_session.delete()
    rotate_token(request)
    response = JsonResponse({'status': 'logged_out'})
    response.delete_cookie(settings.ACCOUNT_COOKIE, samesite='Lax')
    response.delete_cookie(settings.ANONYMOUS_COOKIE, samesite='Lax')
    return response


@endpoint('GET')
def me(request, data):
    return JsonResponse({'account': accounts.public(accounts.require(request))})


@endpoint('POST', fields=('display_name',))
def profile(request, data):
    with transaction.atomic():
        account = accounts.require(request)
        services.rate_limit('account_profile', str(account.pk), 20, 3600)
        account.display_name = accounts.display_name(data['display_name'])
        account.save(update_fields=['display_name', 'updated_at'])
    return JsonResponse({'account': accounts.public(account)})


@endpoint('POST', fields=('current_password', 'new_password'))
def password(request, data):
    account = accounts.require(request)
    if settings.QQ_EMAIL_LOGIN and account.qq_email:
        raise services.DomainError('email_login_required', '邮箱账户使用验证码登录', 409)
    services.rate_limit('account_password', str(account.pk), 10, 900)
    accounts.change_password(request, data['current_password'], data['new_password'])
    rotate_token(request)
    response = JsonResponse({'status': 'password_changed'})
    response.delete_cookie(settings.ACCOUNT_COOKIE, samesite='Lax')
    return response


@endpoint('GET')
def screen(request, data, mode='login'):
    parameters(request, ('tab', 'page','activity') if mode == 'profile' else ('from','page') if mode == 'notifications' else ('next',))
    if mode in ('profile', 'settings', 'notifications', 'bind') and not request.account:
        return redirect(login_url(request.get_full_path()))
    if mode == 'bind' and not settings.QQ_EMAIL_LOGIN:
        return redirect(safe_return(request.GET.get('next','/account/settings/')))
    if mode == 'legacy' and request.account:
        return redirect('/account/bind/?'+urlencode({'next':safe_return(request.GET.get('next','/account/'))}))
    if mode == 'bind' and request.account.qq_email:
        return redirect(safe_return(request.GET.get('next','/account/settings/')))
    if mode == 'register' and settings.QQ_EMAIL_LOGIN:
        return redirect('/account/login/?' + urlencode({'next': request.GET.get('next','/')}))
    context = {'mode': mode, 'qq_email_login': settings.QQ_EMAIL_LOGIN}
    if mode == 'profile':
        from .account_home import context as home_context
        context.update(home_context(request))
        if context['page'] > context['paging']['count']:
            return redirect(request.path + context['paging']['last_url'])
        return page(request, 'booklist/account_home.html', context)
    if mode == 'notifications':
        context['return_url']=safe_return(request.GET.get('from','/'))
        context['notification_source']=request.get_full_path()
        offset,size=pagination(request.GET.get('page','1'),50)
        context['notification_page']=min(100,offset//size+1)
        context['notifications'] = inbox_query(request).select_related('event__actor').order_by('-id')[:50]
    return page(request, 'booklist/account.html', context)


def inbox_query(request):
    from .notifications import visible_inbox
    return visible_inbox(accounts.require(request))


@endpoint('GET')
def unread(request, data):
    parameters(request, ('since',))
    account = accounts.require(request)
    result = {'unread_count': inbox_query(request).filter(read_at__isnull=True).count()}
    if 'since' in request.GET:
        value = request.GET['since']
        if not value.isascii() or not value.isdecimal() or len(value) > 19 or not 0 < int(value) < 2**63:
            raise services.DomainError('invalid_request', '消息编号无效')
        result['retracted_ids'] = list(Notification.objects.filter(recipient=account,
            retracted_at__isnull=False, pk__gte=int(value)).order_by('-pk').values_list('pk', flat=True)[:5000])
    return JsonResponse(result)


@endpoint('GET')
def notification_target(request, data, notification_id):
    parameters(request, ('from',))
    note = inbox_query(request).select_related('event__work').filter(pk=notification_id).first()
    if note is None:
        raise services.DomainError('not_found', '消息不存在', 404)
    work = note.event.work
    while work.merged_into_id:
        work = work.merged_into
    from . import catalog
    catalog.work_detail(work.pk, include_retired=note.event.scope_key != 'public')
    suffix = '?activity=' + note.event.scope_key.split(':')[1] if note.event.scope_key.startswith('activity:') else ''
    anchor = '#reasons' if note.event.kind == 'reason_like' else ''
    if 'from' in request.GET:
        source=safe_return(request.GET['from'],'/notifications/')
        if source.startswith('/notifications/'):
            suffix += ('&' if suffix else '?')+urlencode({'from':source})
    return redirect(f'/works/{work.pk}/{suffix}{anchor}')


@endpoint('GET')
def inbox(request, data):
    parameters(request, ('page', 'before'))
    offset, size = pagination(request.GET.get('page', '1'), 50)
    query = inbox_query(request)
    selected = query
    if 'before' in request.GET:
        value = request.GET['before']
        if 'page' in request.GET or not value.isascii() or not value.isdecimal() or len(value) > 19 or not 0 < int(value) < 2**63:
            raise services.DomainError('invalid_request', '消息分页编号无效')
        selected = selected.filter(pk__lt=int(value))
    rows = list(selected.select_related('event__actor').order_by('-id')[offset:offset+size])
    has_more = bool(rows) and query.filter(pk__lt=rows[-1].pk).exists()
    return JsonResponse({'unread_count': query.filter(read_at__isnull=True).count(), 'total': query.count(), 'has_more': has_more,
        'results': [{'id': row.pk, 'actor': accounts.public(row.event.actor), 'work_title': row.work_title,
            'kind': row.event.kind,
            'scope': row.event.scope_key, 'work_id': row.event.work_id, 'url': f'/notifications/{row.pk}/work/', 'read': row.read_at is not None,
            'activity_title': row.activity_title, 'created_at': row.created_at.isoformat()}
            for row in rows]})


@endpoint('POST', fields={'ids': list})
def read(request, data):
    ids = data['ids']
    if len(ids) > 100 or any(type(pk) is not int or pk <= 0 for pk in ids):
        raise services.DomainError('invalid_request', '消息编号无效')
    changed = inbox_query(request).filter(pk__in=ids, read_at__isnull=True).update(read_at=timezone.now())
    return JsonResponse({'status': 'read', 'changed': changed})


@endpoint('POST')
def read_all(request, data):
    query = inbox_query(request)
    # Mark only messages visible at this transaction boundary.
    with transaction.atomic():
        changed = query.filter(read_at__isnull=True).update(read_at=timezone.now())
    return JsonResponse({'status': 'read', 'changed': changed})
