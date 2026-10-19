from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Work, TagSuppression, WorkTag
from .services import require_active, audit, DomainError
from .text import normalize
from integrations.wenku8.offline import safe_cover


@transaction.atomic
def set_override(actor,work_id,field,value=None,enabled=True):
    require_active(actor)
    if field not in ('title','author','description','cover_url') or type(enabled) is not bool:
        raise DomainError('invalid_override','覆盖字段或状态无效')
    work=Work.objects.get(pk=work_id)
    if work.merged_into_id:
        raise DomainError('merged_work','请管理正式主作品',409)
    if enabled:
        value=normalize(value,'description' if field=='description' else 'title',required=field=='title') if field!='cover_url' else (safe_cover(value) if value else '')
    else:
        value=''
    flag=field+'_overridden'
    column=field+'_override'
    before={flag:getattr(work,flag),column:getattr(work,column)}
    setattr(work,flag,enabled)
    setattr(work,column,value)
    work.save(update_fields=[flag,column,'updated_at'])
    audit(actor,'work.override' if enabled else 'work.restore_source',work,before=before,after={flag:enabled,column:value})
    return work


@transaction.atomic
def suppress_tag(actor,work_id,tag_id,scope,enabled,reason):
    require_active(actor)
    if scope not in ('source','all') or type(enabled) is not bool or not isinstance(reason,str) or not reason.strip():
        raise DomainError('invalid_suppression','需要范围、状态及原因')
    work=Work.objects.get(pk=work_id)
    if work.merged_into_id:
        raise DomainError('merged_work','请管理正式主作品',409)
    row=TagSuppression.objects.filter(work_id=work_id,tag_id=tag_id,scope=scope).order_by('-id').first()
    before={'is_active':row.is_active} if row else {}
    if row:
        row.is_active=enabled
        row.admin=actor
        row.reason=reason
        row.save(update_fields=['is_active','admin','reason','updated_at'])
    else:
        row=TagSuppression.objects.create(work_id=work_id,tag_id=tag_id,scope=scope,is_active=enabled,admin=actor,reason=reason)
    if enabled and scope=='source':
        WorkTag.objects.filter(work_id=work_id,tag_id=tag_id,source_type='source',state='active').update(state='admin_removed',removed_at=timezone.now(),updated_at=timezone.now())
    audit(actor,'tag.suppression',row,before=before,after={'scope':scope,'is_active':enabled},reason=reason)
    return row
