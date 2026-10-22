from django.conf import settings
import re


def site_legal(request):
    section = request.GET.get('section', 'works')
    for prefix, value in (('/manage/works/', 'works'), ('/manage/activities/', 'activities'),
                          ('/manage/feedback/', 'feedback'),
                          ('/manage/source-reviews/', 'source_reviews'), ('/manage/merge/', 'merges')):
        if request.path.startswith(prefix):
            section = value
            break
    feedback_source = request.get_full_path() if re.fullmatch(r'/(?:works/[1-9][0-9]*/|activities/(?:[1-9][0-9]*/|history/)?|catalog/)?', request.path) else ''
    if len(feedback_source) > 1000:
        feedback_source = request.path
    return {'icp_record': settings.ICP_RECORD, 'feedback_source': feedback_source,
            'has_admin_access': getattr(request, 'admin', None) is not None,
            'admin_navigation_section': section}
