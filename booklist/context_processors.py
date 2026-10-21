from django.conf import settings


def site_legal(request):
    account = getattr(request, 'account', None)
    from .models import Notification
    section = request.GET.get('section', 'works')
    for prefix, value in (('/manage/readers/', 'readers'), ('/manage/works/', 'works'), ('/manage/activities/', 'activities'),
                          ('/manage/source-reviews/', 'source_reviews'), ('/manage/merge/', 'merges')):
        if request.path.startswith(prefix):
            section = value
            break
    return {'icp_record': settings.ICP_RECORD,
            'current_account': account,
            'unread_notifications': Notification.objects.filter(recipient=account, read_at__isnull=True).count() if account else 0,
            'has_admin_access': getattr(request, 'admin', None) is not None,
            'admin_navigation_section': section}
