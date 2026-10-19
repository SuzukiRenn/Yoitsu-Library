"""Restore client identity only from an explicitly trusted, single-hop proxy."""
from django.conf import settings
from django.http import JsonResponse
from config.proxy import canonical_ip


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            peer = canonical_ip(request.META.get('REMOTE_ADDR', ''))
        except ValueError:
            peer = None
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        # Downstream code must not accidentally start trusting unvalidated headers.
        for key in ('HTTP_FORWARDED', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST',
                    'HTTP_X_REAL_IP', 'HTTP_X_FORWARDED_PROTO'):
            request.META.pop(key, None)
        if peer in settings.TRUSTED_PROXY_IPS and forwarded is not None:
            try:
                request.META['REMOTE_ADDR'] = canonical_ip(forwarded)
            except ValueError:
                return JsonResponse({'error': {'code': 'invalid_proxy_header', 'message': '请求无效'}}, status=400)
        # TLS scheme is set by Gunicorn after its own source-address trust check.
        return self.get_response(request)
