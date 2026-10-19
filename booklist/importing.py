"""Offline metadata synchronization. Parse outside the per-item write transaction."""
from datetime import date
import hashlib
import json
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone
from .models import (Work, SourceFieldState, Tag, TagSource, WorkTag, TagSuppression,
                     ImportBatch, ImportItem, ImportAttempt)
from .services import audit
from .text import normalize
from integrations.wenku8.offline import canonical_detail_url, extract, fingerprint, safe_cover, SnapshotError, MAX_HTML_BYTES

FIELD_NAMES = ('title','author','introduction','cover_url','publisher_category','status','last_updated','word_count','tags')
SOURCE_COLUMNS = {'title':'wenku8_title','author':'wenku8_author','introduction':'wenku8_description','cover_url':'wenku8_cover_url'}


def hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded_read(path, limit):
    if not path.is_file():
        raise SnapshotError('input_missing')
    with path.open('rb') as stream:
        data = stream.read(limit+1)
    if not data or len(data) > limit:
        raise SnapshotError('invalid_file_size')
    return data


def safe_path(base, relative):
    if not isinstance(relative,str):
        raise SnapshotError('invalid_manifest_path')
    path = Path(relative)
    if path.is_absolute() or path.drive or '..' in path.parts or not path.parts or path.suffix.lower() not in ('.htm','.html'):
        raise SnapshotError('invalid_manifest_path')
    current=base
    for part in path.parts:
        current=current/part
        if current.is_symlink() or (hasattr(current,'is_junction') and current.is_junction()):
            raise SnapshotError('linked_input_not_allowed')
    return current


def manifest(path):
    path=Path(path).absolute()
    raw=bounded_read(path,128*1024)
    try:
        doc=json.loads(raw.decode('utf-8'))
    except (ValueError,UnicodeError):
        raise SnapshotError('invalid_manifest_json') from None
    if not isinstance(doc,dict) or set(doc)!= {'version','items'} or type(doc['version']) is not int or doc['version']!=1 or not isinstance(doc['items'],list) or not 1<=len(doc['items'])<=20:
        raise SnapshotError('invalid_manifest')
    keys,ids=set(),set()
    for item in doc['items']:
        if not isinstance(item,dict) or set(item)!= {'key','book_id','source_url','path'}:
            raise SnapshotError('invalid_manifest_item')
        canonical_detail_url(item['book_id'],item['source_url'])
        if not isinstance(item['key'],str) or not 1<=len(item['key'])<=100 or item['key'] in keys or item['book_id'] in ids:
            raise SnapshotError('duplicate_or_invalid_manifest_identity')
        safe_path(path.parent,item['path'])
        keys.add(item['key'])
        ids.add(item['book_id'])
    return path,hash_bytes(raw),doc


def create_batch(path):
    path,manifest_digest,doc=manifest(path)
    prepared=[]
    for item in doc['items']:
        try:
            digest=hash_bytes(bounded_read(safe_path(path.parent,item['path']),MAX_HTML_BYTES))
        except (OSError,SnapshotError):
            digest=''
        prepared.append({**item,'digest':digest})
    with transaction.atomic():
        batch=ImportBatch.objects.create(manifest_path=str(path),manifest_hash=manifest_digest,parser_fingerprint=fingerprint())
        for item in prepared:
            ImportItem.objects.create(batch=batch,key=item['key'],book_id=item['book_id'],source_url=item['source_url'],relative_path=item['path'],content_hash=item['digest'])
    return batch


def resume_batch(batch_id):
    batch=ImportBatch.objects.get(pk=batch_id)
    _,digest,_=manifest(batch.manifest_path)
    if digest!=batch.manifest_hash or fingerprint()!=batch.parser_fingerprint:
        raise SnapshotError('batch_inputs_or_parser_changed')
    return batch


def validate_snapshot(snapshot):
    if not isinstance(snapshot,dict) or type(snapshot.get('version')) is not int or snapshot.get('version')!=1:
        raise SnapshotError('invalid_snapshot_version')
    canonical_detail_url(snapshot.get('book_id'),snapshot.get('source_url'))
    if not isinstance(snapshot.get('fields'),dict) or set(snapshot['fields']) != set(FIELD_NAMES):
        raise SnapshotError('invalid_snapshot_fields')
    fields,warnings={},[]
    for name,field in snapshot['fields'].items():
        if not isinstance(field,dict) or field.get('state') not in ('value','empty','not_fetched','parse_error','invalid'):
            raise SnapshotError('invalid_field_diagnostic')
        field=field.copy()
        value=field.get('value')
        try:
            if field['state']=='empty':
                field['value']=None
            elif field['state']=='value':
                if name=='cover_url':
                    field['value']=safe_cover(value)
                elif name=='word_count':
                    if type(value) is not int or value<0:
                        raise ValidationError('Invalid word count')
                elif name=='last_updated':
                    if not isinstance(value,str):
                        raise ValidationError('Invalid source date')
                    date.fromisoformat(value)
                elif name=='tags':
                    if not isinstance(value,list) or len(value)>200:
                        raise ValidationError('Invalid tags')
                    tags=[]
                    seen={}
                    for entry in value:
                        if not isinstance(entry,dict) or set(entry)!= {'id','name'} or (entry['id'] is not None and (type(entry['id']) is not int or entry['id']<=0)):
                            raise ValidationError('Invalid tag identity')
                        entry={'id':entry['id'],'name':normalize(entry['name'],'title',required=True)}
                        key=('id',entry['id']) if entry['id'] is not None else ('name',entry['name'])
                        if key in seen and seen[key]!=entry['name']:
                            raise ValidationError('Conflicting tag identity')
                        if key not in seen:
                            tags.append(entry)
                        seen[key]=entry['name']
                    field['value']=tags
                else:
                    field['value']=normalize(value,'description' if name=='introduction' else 'title',required=name=='title')
                    if not field['value']:
                        field['state']='empty'
                        field['value']=None
        except (ValidationError,ValueError,TypeError):
            field.update(state='invalid',value=None,code='invalid_or_overlong_'+name)
        if name=='title' and field['state']!='value':
            raise SnapshotError('invalid_standard_title')
        if name=='tags' and field.get('complete') is not True:
            warnings.append('tags_incomplete')
        if field['state'] not in ('value','empty'):
            warnings.append(field.get('code') or name+'_'+field['state'])
        fields[name]=field
    return fields,sorted(set(warnings))


def resolve_tag(tag):
    visited=set()
    while tag.merged_into_id:
        if tag.pk in visited:
            raise SnapshotError('tag_mapping_cycle')
        visited.add(tag.pk)
        tag=Tag.objects.get(pk=tag.merged_into_id)
    return tag


def source_tag(entry,warnings):
    name=entry['name']
    key='wenku8:id:'+str(entry['id']) if entry['id'] is not None else 'wenku8:name:'+name
    source=TagSource.objects.select_related('tag').filter(source_key=key).first()
    if source:
        tag=resolve_tag(source.tag)
        if source.original_name!=name:
            source.original_name=name
            source.save(update_fields=['original_name','updated_at'])
            if not tag.duplicate_review_needed:
                tag.duplicate_review_needed=True
                tag.save(update_fields=['duplicate_review_needed','updated_at'])
            warnings.append('tag_name_change_needs_review')
        return tag
    duplicate=Tag.objects.filter(normalized_name=name,merged_into__isnull=True)
    has_duplicate=duplicate.exists()
    tag=Tag.objects.create(name=name,normalized_name=name,duplicate_review_needed=has_duplicate)
    if has_duplicate:
        duplicate.filter(duplicate_review_needed=False).update(duplicate_review_needed=True)
        warnings.append('duplicate_tag_name_needs_review')
    TagSource.objects.create(source_key=key,tag=tag,original_name=name)
    return tag


def sync_tags(work,field,warnings):
    if field['state'] not in ('value','empty'):
        return
    desired=set()
    for entry in field.get('value') or []:
        tag=source_tag(entry,warnings)
        desired.add(tag.pk)
        suppression=TagSuppression.objects.filter(work=work,tag=tag,is_active=True,scope__in=['source','all']).exists()
        if suppression:
            warnings.append('source_tag_suppressed')
            continue
        if WorkTag.objects.filter(work=work,tag=tag,source_type='source',state='active').exists():
            continue
        previous=WorkTag.objects.filter(work=work,tag=tag,source_type='source').exclude(state='merge_archived').order_by('-id').first()
        if previous and previous.state=='admin_removed' and not TagSuppression.objects.filter(work=work,tag=tag,scope='source',is_active=False).exists():
            warnings.append('admin_removed_source_tag_preserved')
            continue
        if previous:
            previous.state='active'
            previous.removed_at=None
            previous.save(update_fields=['state','removed_at','updated_at'])
        else:
            WorkTag.objects.create(work=work,tag=tag,source_type='source')
    if field.get('complete') is True:
        WorkTag.objects.filter(work=work,source_type='source',state='active').exclude(tag_id__in=desired).update(state='source_removed',removed_at=timezone.now(),updated_at=timezone.now())


def sync_diagnostic(work,name,field):
    values={'state':field['state'],'raw_value':field.get('raw'),'error_code':field.get('code',''),'collection_complete':field.get('complete') if name=='tags' else None}
    old=SourceFieldState.objects.filter(work=work,field=name).first()
    if not old:
        SourceFieldState.objects.create(work=work,field=name,**values)
    elif any(getattr(old,key)!=value for key,value in values.items()):
        for key,value in values.items():
            setattr(old,key,value)
        old.save(update_fields=[*values,'updated_at'])


@transaction.atomic
def apply_snapshot(snapshot):
    """Internal source adapter API, never accepts a browser-provided identity assertion."""
    fields,warnings=validate_snapshot(snapshot)
    work=Work.objects.filter(wenku8_book_id=snapshot['book_id']).first()
    if work and work.merged_into_id:
        raise SnapshotError('source_work_is_merged')
    if work:
        previous=work.source_metadata.get('last_updated')
        incoming=fields['last_updated']
        if previous:
            try:
                previous=date.fromisoformat(previous)
            except (TypeError,ValueError):
                raise SnapshotError('invalid_existing_source_date') from None
            if incoming['state']=='value' and date.fromisoformat(incoming['value'])<previous:
                raise SnapshotError('stale_source_snapshot')
    created=work is None
    if created:
        work=Work(wenku8_book_id=snapshot['book_id'],identity_status='matched',wenku8_title=fields['title']['value'])
    changed=[]
    metadata=dict(work.source_metadata)
    for name,field in fields.items():
        if name=='tags' or field['state'] not in ('value','empty'):
            continue
        value=field['value']
        column=SOURCE_COLUMNS.get(name)
        if column:
            if getattr(work,column)!=value:
                setattr(work,column,value)
                changed.append(column)
        elif metadata.get(name)!=value or name not in metadata:
            metadata[name]=value
            changed.append('source_metadata')
    if work.wenku8_detail_url!=snapshot['source_url']:
        work.wenku8_detail_url=snapshot['source_url']
        changed.append('wenku8_detail_url')
    work.source_metadata=metadata
    if created:
        work.save()
    elif changed:
        work.save(update_fields=[*set(changed),'updated_at'])
    for name,field in fields.items():
        sync_diagnostic(work,name,field)
    sync_tags(work,fields['tags'],warnings)
    # Imported catalog data never creates public contributions or overwrites visibility/overrides.
    audit(None,'source.import',work,after={'book_id':snapshot['book_id'],'created':created,'changed_fields':sorted(set(changed))})
    return work,sorted(set(warnings)),created


def process_item(item_id,retry_failed=False):
    item=ImportItem.objects.select_related('batch').get(pk=item_id)
    snapshot,error={},''
    try:
        raw=bounded_read(safe_path(Path(item.batch.manifest_path).parent,item.relative_path),MAX_HTML_BYTES)
        if hash_bytes(raw)!=item.content_hash:
            raise SnapshotError('input_changed_start_new_batch')
        snapshot=extract(raw,item.book_id,item.source_url)
    except (SnapshotError,OSError) as exc:
        error=str(exc) if isinstance(exc,SnapshotError) else 'input_unreadable'
    with transaction.atomic():
        item=ImportItem.objects.get(pk=item_id)
        if item.status!='pending' and not (retry_failed and item.status=='failed'):
            return False
        warnings=[]
        try:
            if error:
                raise SnapshotError(error)
            with transaction.atomic():
                work,warnings,_=apply_snapshot(snapshot)
            item.work=work
            item.status='partial' if warnings else 'imported'
            item.error_code=''
        except (SnapshotError,DatabaseError) as exc:
            item.status='failed'
            item.error_code=str(exc) if isinstance(exc,SnapshotError) else 'database_write_failed'
        item.warnings=warnings
        item.save(update_fields=['status','work','error_code','warnings','updated_at'])
        ImportAttempt.objects.create(item=item,status=item.status,error_code=item.error_code,warnings=warnings,snapshot=snapshot)
    return True


def run_batch(batch,max_items=20,retry_failed=False):
    resume_batch(batch.pk)
    if type(max_items) is not int or not 1<=max_items<=20:
        raise SnapshotError('max_items_must_be_1_to_20')
    statuses=['pending','failed'] if retry_failed else ['pending']
    ids=list(batch.items.filter(status__in=statuses).order_by('id').values_list('id',flat=True)[:max_items])
    processed=sum(process_item(pk,retry_failed) for pk in ids)
    with transaction.atomic():
        batch.refresh_from_db()
        counts={status:batch.items.filter(status=status).count() for status in ('pending','imported','partial','failed')}
        batch.status='paused' if counts['pending'] else 'completed_with_errors' if counts['failed'] else 'completed_partial' if counts['partial'] else 'completed'
        batch.save(update_fields=['status','updated_at'])
    return {'batch_id':str(batch.pk),'status':batch.status,'processed_this_run':processed,'counts':counts,
        'items':[{'key':item.key,'book_id':item.book_id,'status':item.status,'work_id':item.work_id,'error_code':item.error_code,'warnings':item.warnings} for item in batch.items.order_by('id')]}
