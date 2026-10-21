from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from .admin_views import admin_page
from .catalog_views import page, parameters
from .models import Account, AccountSession
from . import services, moderation
from .views import endpoint
from .protocol import pagination


@admin_page
@endpoint('GET', admin=True)
def index(request, data):
    parameters(request, ('page', 'q'))
    offset, size = pagination(request.GET.get('page', '1'), 30)
    query = request.GET.get('q', '').strip()[:100]
    rows = Account.objects.only('id', 'username', 'display_name', 'is_active', 'created_at').order_by('-id')
    if query:
        rows = rows.filter(Q(username__icontains=query) | Q(display_name__icontains=query))
    from urllib.parse import urlencode
    link = lambda number: '?' + urlencode({'page': number, 'q': query})
    return page(request, 'booklist/admin/accounts.html', {'accounts': rows[offset:offset+size], 'query': query,
        'next': link(offset//size+2) if rows.count() > offset+size else None,
        'previous': link(offset//size) if offset else None})


@endpoint('POST', admin=True, fields={'enabled': bool, 'reason': str})
def status(request, data, account_id):
    with transaction.atomic():
        services.require_active(request.admin)
        reason = moderation.reason(data['reason'])
        row = Account.objects.filter(pk=account_id).first()
        if row is None:
            raise services.DomainError('not_found', '账户不存在', 404)
        if row.is_active != data['enabled']:
            before = row.is_active
            row.is_active = data['enabled']
            row.session_version += 1
            row.save(update_fields=['is_active', 'session_version', 'updated_at'])
            AccountSession.objects.filter(account=row).delete()
            services.audit(request.admin, 'account.status', row,
                before={'is_active': before}, after={'is_active': row.is_active}, reason=reason)
    return JsonResponse({'status': 'saved'})
