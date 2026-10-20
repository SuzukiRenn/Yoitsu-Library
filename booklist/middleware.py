from django.conf import settings
from django.db.models import F
from django.db import OperationalError
from django.http import JsonResponse
from django.utils import timezone
from .models import AdminSession, AnonymousClient
from .services import token_hash, valid_token
from . import cover_cache


class IdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.admin = request.admin_session = request.anonymous_client = None
        length = request.META.get('CONTENT_LENGTH', '')
        if length and (not length.isdigit() or int(length) > 32768):
            return JsonResponse({'error': {'code': 'request_too_large', 'message': '请求体过大'}}, status=413)
        prefix = '/media/covers/'
        public_cover = request.method in ('GET', 'HEAD') and request.path_info.startswith(prefix) and bool(
            cover_cache.NAME.fullmatch(request.path_info[len(prefix):]))
        if public_cover:
            # Cover bytes are identical for every visitor. Avoid identity queries and
            # let this one public raster endpoint retain its browser cache headers.
            response = self.get_response(request)
            matched = getattr(getattr(request, 'resolver_match', None), 'func', None)
            if matched is not cover_cache.serve or response.status_code not in (200, 304):
                response['Cache-Control'] = 'no-store'
            response['X-Frame-Options'] = 'DENY'
            return response
        try:
            token = request.COOKIES.get(settings.ADMIN_COOKIE)
            if valid_token(token):
                session = AdminSession.objects.select_related('admin').filter(token_hash=token_hash(token),
                    expires_at__gt=timezone.now(), admin__is_active=True, session_version=F('admin__session_version')).first()
                if session:
                    request.admin, request.admin_session = session.admin, session
            token = request.COOKIES.get(settings.ANONYMOUS_COOKIE)
            if valid_token(token):
                request.anonymous_client = AnonymousClient.objects.filter(token_hash=token_hash(token), revoked_at__isnull=True,
                    expires_at__gt=timezone.now()).first()
        except OperationalError as exc:
            from .views import database_unavailable
            response = database_unavailable(request, exc)
            response['X-Frame-Options'] = 'DENY'
            return response
        response = self.get_response(request)
        response['Cache-Control'] = 'no-store'
        response['X-Frame-Options'] = 'DENY'
        return response
