"""Public launch information; no identity or contribution write is required."""
from django.conf import settings
from django.views.decorators.http import require_safe
from .catalog_views import page


@require_safe
def information(request, section):
    return page(request, 'booklist/site_information.html', {
        'legal_section': section,
        'account_edition': bool(getattr(settings, 'ACCOUNT_COOKIE', '')),
    })
