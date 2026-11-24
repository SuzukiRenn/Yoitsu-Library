"""Request and validation helpers for the course pages."""
from functools import wraps

from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_protect

from . import accounts, catalog, services
from .catalog_views import page, read_limit
from .navigation import login_url
from .text import normalize
from .views import database_unavailable


def course_page(view):
    @csrf_protect
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method not in ('GET', 'POST'):
            return HttpResponseNotAllowed(['GET', 'POST'])
        try:
            if request.method == 'GET':
                read_limit(request)
                return view(request, *args, **kwargs)
            if request.account is None and request.admin is None:
                return redirect(login_url(request.path))
            services.rate_limit('course_write_ip', request.META.get('REMOTE_ADDR', ''), 80, 60)
            if request.account:
                services.rate_limit('course_write_actor', str(request.account.pk), 40, 60)
            with transaction.atomic():
                return view(request, *args, **kwargs)
        except ValidationError as exc:
            return page_error(request, exc.messages[0], 400)
        except services.DomainError as exc:
            return page_error(request, exc.message, exc.status)
        except Http404:
            return page_error(request, '记录不存在或暂不可见。', 404)
        except OperationalError as exc:
            return database_unavailable(request, exc)
    return wrapped


def page_error(request, message, status):
    response = page(request, 'booklist/course/error.html', {'message': message, 'back_url': request.path})
    response.status_code = status
    return response


def require_reader(request):
    # Existing account checks include revoked sessions and disabled readers.
    accounts.require(request)
    return request.account


def deny():
    raise services.DomainError('forbidden', '当前账户没有执行此操作的权限。', 403)


def conflict(message='内容已更新。请返回并刷新页面后重试。'):
    raise services.DomainError('conflict', message, 409)


def integer(value, minimum=1, maximum=2**63-1):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value)>19:
        raise ValidationError('编号或版本无效。')
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValidationError('编号或版本超出范围。')
    return number


def require_version(post, obj):
    if integer(post.get('version', ''), minimum=0) != obj.version:
        conflict()


def clean(value, limit, required=True):
    value = normalize(value, 'description', required=required)
    if any(ord(char)<32 and char not in '\n\t' for char in value):
        raise ValidationError('文本含有无效控制字符。')
    if len(value)>limit:
        raise ValidationError(f'内容不能超过 {limit} 字。')
    return value


def visible_work(work_id):
    work = catalog.visible_works().filter(pk=work_id).first()
    if work is None:
        raise Http404
    return work


def bump(obj, *fields):
    obj.version += 1
    obj.save(update_fields=(*fields, 'version', 'updated_at'))
