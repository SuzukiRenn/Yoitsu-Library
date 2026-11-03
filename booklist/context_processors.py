from django.conf import settings
import re


def site_legal(request):
    account = getattr(request, 'account', None)
    from .models import Notification
    section = request.GET.get('section', 'works')
    for prefix, value in (('/manage/readers/', 'readers'), ('/manage/works/', 'works'), ('/manage/activities/', 'activities'),
                          ('/manage/feedback/', 'feedback'),
                          ('/manage/source-reviews/', 'source_reviews'), ('/manage/merge/', 'merges')):
        if request.path.startswith(prefix):
            section = value
            break
    feedback_source = request.get_full_path() if re.fullmatch(r'/(?:works/[1-9][0-9]*/|activities/(?:[1-9][0-9]*/|history/)?|catalog/)?', request.path) else ''
    if len(feedback_source) > 1000:
        feedback_source = request.path
    from .navigation import safe_return,login_url
    from urllib.parse import urlencode
    source=safe_return(request.get_full_path())
    notification_source=safe_return(request.GET.get('from','/')) if request.path=='/notifications/' else source
    notification_url='/notifications/?'+urlencode({'from':notification_source})
    from .activity_notices import header_state
    return {'login_url':login_url(source),'notification_url':notification_url,'activity_notice_state': header_state(request), 'qq_email_login': settings.QQ_EMAIL_LOGIN, 'contact_email': settings.CONTACT_EMAIL, 'icp_record': settings.ICP_RECORD, 'feedback_source': feedback_source,
            'current_account': account,
            'unread_notifications': Notification.objects.filter(recipient=account, read_at__isnull=True).count() if account else 0,
            'has_admin_access': getattr(request, 'admin', None) is not None,
            'admin_navigation_section': section}
