"""Private anonymous feedback; reports never trigger automatic moderation."""
import re
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from . import services
from .models import Feedback, Work
from .text import normalize
from .views import endpoint
from .catalog_views import page, parameters
from .public_views import write
from .admin_views import admin_page


def context_url(value):
    if len(value) > 1000 or any(ord(c) < 32 or c == '\\' for c in value):
        raise ValidationError('页面地址无效')
    if not value:
        return ''
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not re.fullmatch(r'/(?:works/[1-9][0-9]*/|activities/(?:[1-9][0-9]*/|history/)?|catalog/)?', parts.path):
        raise ValidationError('仅支持本站作品和书单页面')
    return value


def text(value, limit, required=False):
    result = normalize(value, 'description', required=required)
    if len(result) > limit:
        raise ValidationError('内容超出长度限制')
    return result


@endpoint('GET')
def home(request, data):
    parameters(request, ('from',))
    source = context_url(request.GET.get('from', ''))
    rows = Feedback.objects.filter(anonymous_client=request.anonymous_client).order_by('-id')[:20] if request.anonymous_client else []
    return page(request, 'booklist/feedback.html', {'source_url': source, 'feedback_rows': rows, 'categories': Feedback._meta.get_field('category').choices})


@endpoint('POST', fields={'category': str, 'message': str, 'page_url': str})
def submit(request, data):
    message = text(data['message'], 1000, required=True)
    source = context_url(data['page_url'])
    if data['category'] not in dict(Feedback._meta.get_field('category').choices):
        raise ValidationError('反馈类型无效')
    def operation():
        services.rate_limit('feedback_actor', str(request.anonymous_client.pk), 20, 3600)
        services.rate_limit('feedback_ip', request.META.get('REMOTE_ADDR', ''), 120, 3600)
        row = Feedback.objects.create(anonymous_client=request.anonymous_client, category=data['category'], message=message, page_url=source)
        return {'status': 'received', 'id': row.pk}
    return write(request, 'feedback.submit', data, operation)


@admin_page
@endpoint('GET', admin=True)
def detail(request, data, feedback_id):
    parameters(request, ())
    row = Feedback.objects.filter(pk=feedback_id).first()
    if row is None:
        raise services.DomainError('not_found', '反馈不存在', 404)
    match = re.match(r'^/works/([1-9][0-9]*)/', row.page_url)
    work = Work.objects.filter(pk=int(match[1])).first() if match else None
    # Keep the original report intact while linking management to the canonical work.
    while work and work.merged_into_id:
        work = Work.objects.filter(pk=work.merged_into_id).first()
    return page(request, 'booklist/admin/feedback.html', {'feedback': row, 'reported_work': work})


@endpoint('POST', admin=True, fields={'status': str, 'reply': str, 'revision': int})
def handle(request, data, feedback_id):
    if data['status'] not in dict(Feedback._meta.get_field('status').choices):
        raise ValidationError('处理状态无效')
    reply = text(data['reply'], 500, required=True)
    with transaction.atomic():
        row = Feedback.objects.filter(pk=feedback_id).first()
        if row is None:
            raise services.DomainError('not_found', '反馈不存在', 404)
        if row.revision != data['revision']:
            raise services.DomainError('stale_feedback', '反馈已被处理，请刷新后查看', 409)
        before = {'status': row.status, 'reply': row.reply, 'revision': row.revision}
        row.status, row.reply, row.handled_by = data['status'], reply, request.admin
        row.revision += 1
        row.save(update_fields=['status', 'reply', 'handled_by', 'revision', 'updated_at'])
        services.audit(request.admin, 'feedback.handle', row, before, {'status': row.status, 'reply': reply, 'revision': row.revision}, reply)
    return JsonResponse({'status': 'saved'})
