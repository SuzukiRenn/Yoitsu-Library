"""Reader-authored routes attached to a visible work."""
import json

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect

from . import reason_references, services
from .catalog_views import page
from .course_common import bump, clean, conflict, course_page, deny, integer, require_reader, visible_work
from .course_models import ReadingRoute


def rows(work_id, *, include_hidden=False, account=None):
    queryset = ReadingRoute.objects.filter(work_id=work_id, withdrawn=False).select_related('author').order_by('-updated_at', '-pk')
    if not include_hidden:
        queryset = queryset.filter(hidden=False)
    return reason_references.decorate({
        'id': 'route-' + str(item.pk), 'record_id': item.pk, 'reason': item.reason,
        'reason_references': item.reason_references, 'contains_spoilers': item.contains_spoilers,
        'author': item.author.display_name, 'version': item.version, 'hidden': item.hidden,
        'can_edit': account is not None and item.author_id == account.pk and not item.hidden,
    } for item in queryset[:50])


@course_page
def edit(request, work_id):
    work = visible_work(work_id)
    detail_url = f'/works/{work.pk}/#reading-routes'
    if request.method == 'POST' and request.POST.get('action') in ('hide', 'restore'):
        if not request.admin:
            deny()
        item = get_object_or_404(ReadingRoute, work=work, pk=integer(request.POST.get('route', '')))
        if item.version != integer(request.POST.get('version', '')):
            conflict()
        before = item.hidden
        item.hidden = request.POST['action'] == 'hide'
        bump(item, 'hidden')
        services.audit(request.admin, 'course.route.visibility', item, {'hidden': before}, {'hidden': item.hidden})
        return redirect(detail_url)

    if request.method == 'GET' and not request.account:
        from .navigation import login_url
        return redirect(login_url(request.path))
    reader = require_reader(request)
    item = ReadingRoute.objects.filter(work=work, author=reader).first()
    if item and item.hidden:
        deny()
    if request.method == 'POST':
        version = integer(request.POST.get('version', ''), minimum=0)
        if version != (item.version if item else 0):
            conflict()
        action = request.POST.get('action')
        if action == 'withdraw':
            if not item or item.withdrawn:
                conflict('当前没有可撤回的路线。')
            item.withdrawn = True
            bump(item, 'withdrawn')
        elif action == 'save':
            reason = clean(request.POST.get('reason', ''), 200)
            try:
                references = json.loads(request.POST['references']) if request.POST.get('references') else None
            except (ValueError, TypeError):
                raise ValidationError('作品引用数据无效。') from None
            if references is not None and not isinstance(references, list):
                raise ValidationError('作品引用数据无效。')
            validated = reason_references.validate(reason, references, item, work.pk)
            fields = {'reason': reason, 'reason_references': validated,
                      'contains_spoilers': request.POST.get('spoilers')=='on', 'withdrawn': False}
            if item:
                for name, value in fields.items():
                    setattr(item, name, value)
                bump(item, *fields)
            else:
                ReadingRoute.objects.create(work=work, author=reader, **fields)
        else:
            raise ValidationError('操作无效。')
        return redirect(detail_url)
    return page(request, 'booklist/course/route_edit.html', {'route_work': work, 'route': item,
        'route_refs': item.reason_references if item else [], 'version': item.version if item else 0})
