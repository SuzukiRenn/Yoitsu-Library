"""Reason reactions are independent of work recommendations and work likes."""
from django.db.models import Count, Q, Exists, OuterRef, Value, BooleanField
from django.http import JsonResponse
from django.utils import timezone
from . import activities, public_booklist as books, services
from .models import ReasonLike
from .views import endpoint


def ranked(query):
    active = Q(reasonlike__cancelled_at__isnull=True, reasonlike__anonymous_client__revoked_at__isnull=True)
    return query.annotate(like_count=Count('reasonlike', filter=active)).order_by('-like_count', 'created_at', 'id')


def entries(query, client=None, *, limit=None, offset=0, popular=False, writable=True):
    query = ranked(query)
    if not popular:
        query = query.order_by('created_at', 'id')
    own = ReasonLike.objects.filter(recommendation_id=OuterRef('pk'), anonymous_client=client, cancelled_at__isnull=True) if client else None
    query = query.annotate(liked=Exists(own) if own is not None else Value(False, output_field=BooleanField()))
    query = query[offset:offset+limit] if limit is not None else query[offset:]
    from .reason_references import decorate
    return decorate([{**row, 'writable': writable} for row in query.values('id', 'reason', 'reason_references', 'contains_spoilers', 'like_count', 'liked')])


@endpoint('POST', fields={'enabled': bool})
def like(request, data, work_id, reason_id, activity_id=None):
    from .public_views import write
    def operation():
        if activity_id is not None:
            activities.advance_due(); activities.require_open(activity_id)
            books.require_work(work_id)
            query = activities.recs(activity_id)
        else:
            books.require_work(work_id)
            if not books.recommendations().filter(work_id=work_id).exists():
                raise services.DomainError('not_found', '理由不存在或暂不可用', 404)
            query = books.reasons()
        query = query.filter(pk=reason_id, work_id=work_id, text_hidden_at__isnull=True).exclude(reason='')
        reason = query.first()
        if reason is None:
            raise services.DomainError('not_found', '理由不存在或暂不可用', 404)
        row, _ = ReasonLike.objects.get_or_create(recommendation=reason, anonymous_client=request.anonymous_client,
            defaults={'cancelled_at': None if data['enabled'] else timezone.now()})
        row.cancelled_at = None if data['enabled'] else timezone.now()
        row.save(update_fields=['cancelled_at', 'updated_at'])
        from .live_stats import snapshot
        return {'reason': entries(query, request.anonymous_client)[0],
                'work': snapshot([work_id], request.anonymous_client, activity_id)['results'][0]}
    return write(request, f'reason.like.{activity_id or "public"}.{work_id}.{reason_id}', data, operation)


@endpoint('GET')
def stats(request, data, work_id, activity_id=None):
    from .catalog_views import parameters, read_limit
    from django.core.exceptions import ValidationError
    parameters(request, ('ids',)); read_limit(request)
    raw = request.GET.get('ids', '').split(',')
    if not 1 <= len(raw) <= 100 or any(not value.isascii() or not value.isdecimal() or len(value) > 18 or int(value) <= 0 for value in raw):
        raise ValidationError('理由编号无效')
    if activity_id is not None:
        activities.advance_due(); period = activities.get(activity_id)
        from . import catalog
        catalog.work_detail(work_id, include_retired=period.status == 'ended')
        query = activities.recs(activity_id)
        writable = activities.writable(period)
    else:
        books.require_work(work_id)
        query = books.reasons()
        writable = books.recommendations().filter(work_id=work_id).exists()
    query = query.filter(work_id=work_id, pk__in=list(map(int, raw)), text_hidden_at__isnull=True).exclude(reason='')
    return JsonResponse({'results': entries(query, request.anonymous_client, writable=writable)})
