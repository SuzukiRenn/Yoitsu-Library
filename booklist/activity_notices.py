"""Published theme discovery; viewing is separate from participation or inbox messages."""
from django.db import transaction, OperationalError
from django.http import JsonResponse
from django.utils import timezone
from . import accounts, services
from .catalog_views import parameters, read_limit
from .models import Activity, ActivityNoticeSeen
from .views import endpoint


def published_ids():
    return list(Activity.objects.filter(status__in=['scheduled','active','paused'],
        scheduled_end_at__gt=timezone.now()).order_by('-id').values_list('pk',flat=True)[:100])


def state(request):
    ids=published_ids()
    account=getattr(request,'account',None)
    seen=set(ActivityNoticeSeen.objects.filter(account=account,activity_id__in=ids)
        .values_list('activity_id',flat=True)) if account and ids else set()
    return {'activity_ids':ids,'unseen_ids':[pk for pk in ids if pk not in seen],
            'account_key':str(account.pk) if account else 'guest'}


def header_state(request):
    # Raw template previews and admin screens do not discover reader activities.
    if not hasattr(request,'account') or request.path.startswith('/manage/'):
        return None
    try:
        return state(request)
    except OperationalError:
        # Keep the existing database-unavailable page renderable without this optional cue.
        return None


@endpoint('GET')
def listing(request,data):
    parameters(request,()); read_limit(request)
    return JsonResponse(state(request))


@endpoint('POST', fields={'ids':list,'account_id':int})
def seen(request,data):
    ids=data['ids']
    if len(ids)>100 or any(type(pk) is not int or not 0<pk<2**63 for pk in ids):
        raise services.DomainError('invalid_request','活动编号无效')
    with transaction.atomic():
        account=accounts.require(request)
        if account.pk!=data['account_id']:
            raise services.DomainError('account_changed','账号已变更，请刷新页面',409)
        eligible=set(published_ids()).intersection(ids)
        ActivityNoticeSeen.objects.bulk_create([ActivityNoticeSeen(account=account,activity_id=pk)
            for pk in eligible],ignore_conflicts=True)
    return JsonResponse(state(request))
