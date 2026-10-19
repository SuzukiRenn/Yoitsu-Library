from functools import wraps
import json
from django.conf import settings
from django.core.exceptions import ValidationError, RequestDataTooBig
from django.db import OperationalError, connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse, HttpResponse
from django.middleware.csrf import get_token, rotate_token
from . import services


def error(code, message, status):
    return JsonResponse({'error': {'code': code, 'message': message}}, status=status)


def endpoint(method, admin=False, fields=()):
    def decorate(fn):
        @wraps(fn)
        def wrapped(request, *args, **kwargs):
            try:
                if request.method != method:
                    response = error('method_not_allowed', '请求方法不支持', 405)
                    response['Allow'] = method
                    return response
                if admin:
                    services.require_active(request.admin)
                    if method != 'GET':
                        services.rate_limit('admin_write', str(request.admin.pk), 60, 60)
                data = {}
                if method == 'POST':
                    if request.content_type != 'application/json':
                        return error('invalid_content_type', '需要 application/json', 415)
                    body = request.read(32769)
                    if len(body) > 32768:
                        return error('request_too_large', '请求体过大', 413)
                    data = json.loads(body.decode('utf-8'))
                    types = fields if isinstance(fields, dict) else {key: str for key in fields}
                    if not isinstance(data, dict) or set(data) != set(types) or any(type(data[k]) not in (kind if isinstance(kind, tuple) else (kind,)) for k, kind in types.items()):
                        return error('invalid_request', '字段缺失、类型错误或含未知字段', 400)
                return fn(request, data, *args, **kwargs)
            except services.DomainError as exc:
                response = error(exc.code, exc.message, exc.status)
                if exc.status == 429:
                    response['Retry-After'] = '60'
                return response
            except (ValueError, UnicodeError, ValidationError):
                return error('invalid_request', '输入不符合协议或校验未通过', 400)
            except RequestDataTooBig:
                return error('request_too_large', '请求体过大', 413)
            except OperationalError:
                return error('database_unavailable', '数据库暂不可用，请稍后重试', 503)
        return wrapped
    return decorate


def set_cookie(response, name, value, age):
    response.set_cookie(name, value, max_age=age, httponly=True, secure=settings.COOKIE_SECURE, samesite='Lax', path='/')


def csrf_failure(request, reason=''):
    return error('csrf_failed', '请求来源或 CSRF 校验失败', 403)


@endpoint('GET')
def home(request, data):
    return HttpResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>共享书单</title><h1>共享书单</h1><p>项目基础服务已就绪，书单功能正在分阶段实现。</p></html>')


@endpoint('GET')
def health(request, data):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1 FROM booklist_admin LIMIT 1')
        fk = cursor.execute('PRAGMA foreign_keys').fetchone()[0]
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if fk != 1 or pending:
        return error('schema_unready', '数据库尚未就绪', 503)
    return JsonResponse({'status': 'ok', 'phase': 1})


@endpoint('GET')
def csrf_token(request, data):
    return JsonResponse({'csrf_token': get_token(request)})


@endpoint('POST')
def identity(request, data):
    services.rate_limit('identity', request.META.get('REMOTE_ADDR', ''), 20, 3600)
    token, client = services.issue_anonymous(request.anonymous_client)
    response = JsonResponse({'status': 'ready'})
    set_cookie(response, settings.ANONYMOUS_COOKIE, token or request.COOKIES[settings.ANONYMOUS_COOKIE], settings.ANONYMOUS_AGE)
    return response


@endpoint('POST')
def reset_identity(request, data):
    services.rate_limit('identity', request.META.get('REMOTE_ADDR', ''), 20, 3600)
    token, _ = services.issue_anonymous(request.anonymous_client, reset=True)
    response = JsonResponse({'status': 'reset', 'message': '旧贡献保留，新身份不继承其管理权限'})
    set_cookie(response, settings.ANONYMOUS_COOKIE, token, settings.ANONYMOUS_AGE)
    return response


@endpoint('POST', fields=('username', 'password'))
def admin_login(request, data):
    services.rate_limit('login', request.META.get('REMOTE_ADDR', ''), 10, 900)
    token, _ = services.login(data['username'], data['password'])
    # Retire this browser's prior session only after successful replacement.
    if request.admin_session:
        request.admin_session.delete()
    rotate_token(request)
    response = JsonResponse({'status': 'authenticated'})
    set_cookie(response, settings.ADMIN_COOKIE, token, settings.ADMIN_AGE)
    return response


@endpoint('POST', admin=True)
def admin_logout(request, data):
    request.admin_session.delete()
    response = JsonResponse({'status': 'logged_out'})
    response.delete_cookie(settings.ADMIN_COOKIE, samesite='Lax')
    return response


@endpoint('GET', admin=True)
def admin_me(request, data):
    return JsonResponse({'username': request.admin.username})


@endpoint('POST', admin=True, fields=('username', 'password'))
def admin_create(request, data):
    account = services.create_admin(request.admin, data['username'], data['password'])
    return JsonResponse({'id': account.pk, 'username': account.username}, status=201)


@endpoint('POST', admin=True)
def admin_disable(request, data, admin_id):
    services.disable_admin(request.admin, admin_id)
    return JsonResponse({'status': 'disabled'})


@endpoint('POST', admin=True, fields=('current_password', 'new_password'))
def admin_password(request, data):
    services.change_password(request.admin, data['current_password'], data['new_password'])
    response = JsonResponse({'status': 'password_changed', 'message': '请重新登录'})
    response.delete_cookie(settings.ADMIN_COOKIE, samesite='Lax')
    return response
