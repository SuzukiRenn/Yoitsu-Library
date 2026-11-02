from django.conf import settings
from django.http import JsonResponse
from . import accounts, qq_login, services
from .account_views import authenticated
from .views import endpoint


@endpoint('POST', fields=('email', 'purpose'))
def code(request, data):
    account = accounts.require(request) if data['purpose'] == 'bind' else None
    return JsonResponse(qq_login.request_code(data['email'], data['purpose'], request.META.get('REMOTE_ADDR', ''), account))


@endpoint('POST', fields=('email', 'challenge', 'code'))
def login(request, data):
    qq_login.rate_limit('qq_verify_ip', request.META.get('REMOTE_ADDR', ''), settings.QQ_VERIFY_IP_LIMIT, 900,
        f'当前网络每 15 分钟验证请求已达 {settings.QQ_VERIFY_IP_LIMIT} 次上限')
    return authenticated(request, qq_login.verify(data['email'], data['challenge'], data['code'], 'login'))


@endpoint('POST', fields=('email', 'challenge', 'code'))
def bind(request, data):
    account = accounts.require(request)
    qq_login.rate_limit('qq_verify_ip', request.META.get('REMOTE_ADDR', ''), settings.QQ_VERIFY_IP_LIMIT, 900,
        f'当前网络每 15 分钟验证请求已达 {settings.QQ_VERIFY_IP_LIMIT} 次上限')
    return authenticated(request, qq_login.verify(data['email'], data['challenge'], data['code'], 'bind', account))
