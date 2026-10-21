from django.conf import settings


def site_legal(request):
    section = request.GET.get('section', 'works')
    for prefix, value in (('/manage/works/', 'works'), ('/manage/activities/', 'activities'),
                          ('/manage/source-reviews/', 'source_reviews'), ('/manage/merge/', 'merges')):
        if request.path.startswith(prefix):
            section = value
            break
    return {'icp_record': settings.ICP_RECORD,
            'has_admin_access': getattr(request, 'admin', None) is not None,
            'admin_navigation_section': section}
