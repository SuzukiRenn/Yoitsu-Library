"""One-time nickname confirmation belongs to the account, never the browser."""
from urllib.parse import urlencode
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from .navigation import safe_return

SETUP_PATH='/account/nickname/'
ACCOUNT_WRITES=frozenset(('/api/v1/account/profile/','/api/v1/account/logout/',
    '/api/v1/account/login/','/api/v1/account/register/','/api/v1/account/email/code/',
    '/api/v1/account/email/login/','/api/v1/account/email/bind/'))

def required(account):
    return bool(settings.QQ_EMAIL_LOGIN and account and account.qq_email
                and account.nickname_confirmed_at is None)

def url(destination='/'):
    return SETUP_PATH+'?'+urlencode({'next':safe_return(destination)})

def gate(request):
    if not required(request.account):return None
    path=request.path_info
    if (path==SETUP_PATH or path in ('/health/','/privacy/','/guidelines/','/contact/')
            or path.startswith(('/manage/','/api/v1/admin/','/static/','/media/'))):return None
    if path.startswith('/api/'):
        if request.method not in ('GET','HEAD','OPTIONS') and path not in ACCOUNT_WRITES:
            return JsonResponse({'error':{'code':'nickname_required','message':'请先填写昵称'}},status=403)
        return None
    if request.method in ('GET','HEAD'):
        destination=request.GET.get('next','/') if path in ('/account/login/','/account/register/','/account/legacy-login/','/account/bind/') else request.get_full_path()
        return redirect(url(destination))
    return None
