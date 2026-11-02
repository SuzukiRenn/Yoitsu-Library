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
    services.rate_limit('qq_verify_ip', request.META.get('REMOTE_ADDR', ''), 30, 900)
    return authenticated(request, qq_login.verify(data['email'], data['challenge'], data['code'], 'login'))


@endpoint('POST', fields=('email', 'challenge', 'code'))
def bind(request, data):
    account = accounts.require(request)
    services.rate_limit('qq_verify_ip', request.META.get('REMOTE_ADDR', ''), 30, 900)
    return authenticated(request, qq_login.verify(data['email'], data['challenge'], data['code'], 'bind', account))
