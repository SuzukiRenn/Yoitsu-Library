from django.conf import settings
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from .models import AdminSession, AnonymousClient
from .services import token_hash, valid_token


class IdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.admin = request.admin_session = request.anonymous_client = None
        length = request.META.get('CONTENT_LENGTH', '')
        if length and (not length.isdigit() or int(length) > 32768):
            return JsonResponse({'error': {'code': 'request_too_large', 'message': '请求体过大'}}, status=413)
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
        response = self.get_response(request)
        response['Cache-Control'] = 'no-store'
        response['X-Frame-Options'] = 'DENY'
        return response
