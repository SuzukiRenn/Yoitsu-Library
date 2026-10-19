from django.conf import settings


def site_legal(request):
    return {'icp_record': settings.ICP_RECORD}
