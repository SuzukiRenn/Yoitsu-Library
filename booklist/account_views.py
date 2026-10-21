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
    auth_limit(request, registration=True)
    return authenticated(request, accounts.register(data['username'], data['display_name'], data['password']))


@endpoint('POST', fields=('username', 'password'))
def login(request, data):
    auth_limit(request, data['username'])
    return authenticated(request, accounts.login(data['username'], data['password']))


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
    services.rate_limit('account_password', str(account.pk), 10, 900)
    accounts.change_password(request, data['current_password'], data['new_password'])
    rotate_token(request)
    response = JsonResponse({'status': 'password_changed'})
    response.delete_cookie(settings.ACCOUNT_COOKIE, samesite='Lax')
    return response


@endpoint('GET')
def screen(request, data, mode='login'):
    parameters(request, ('next',))
    if mode in ('profile', 'notifications') and not request.account:
        return redirect('/account/login/?next=' + request.path)
    context = {'mode': mode}
    if mode == 'notifications':
        context['notifications'] = inbox_query(request).select_related('event__actor').order_by('-id')[:50]
    return page(request, 'booklist/account.html', context)


def inbox_query(request):
    return Notification.objects.filter(recipient=accounts.require(request))


@endpoint('GET')
def unread(request, data):
    parameters(request, ())
    return JsonResponse({'unread_count': inbox_query(request).filter(read_at__isnull=True).count()})


@endpoint('GET')
def notification_target(request, data, notification_id):
    parameters(request, ())
    note = inbox_query(request).select_related('event__work').filter(pk=notification_id).first()
    if note is None:
        raise services.DomainError('not_found', '消息不存在', 404)
    work = note.event.work
    while work.merged_into_id:
        work = work.merged_into
    from . import catalog
    catalog.work_detail(work.pk, include_retired=note.event.scope_key != 'public')
    suffix = '?activity=' + note.event.scope_key.split(':')[1] if note.event.scope_key.startswith('activity:') else ''
    return redirect(f'/works/{work.pk}/{suffix}')


@endpoint('GET')
def inbox(request, data):
    parameters(request, ('page',))
    offset, size = pagination(request.GET.get('page', '1'), 50)
    query = inbox_query(request)
    return JsonResponse({'unread_count': query.filter(read_at__isnull=True).count(), 'total': query.count(),
        'results': [{'id': row.pk, 'actor': accounts.public(row.event.actor), 'work_title': row.work_title,
            'scope': row.event.scope_key, 'work_id': row.event.work_id, 'url': f'/notifications/{row.pk}/work/', 'read': row.read_at is not None,
            'activity_title': row.activity_title, 'created_at': row.created_at.isoformat()}
            for row in query.select_related('event__actor').order_by('-id')[offset:offset+size]]})


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
