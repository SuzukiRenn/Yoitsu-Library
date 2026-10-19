"""Transactional work merge; undo refuses intervening content edits."""
import json
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from . import services, moderation, catalog
from .models import (Work, WorkAlias, WorkTag, TagSuppression, ActivityWork, Recommendation, Vote,
    PublicRecommendation, PublicLike, PendingActivitySubmission, ChangeEvent, WorkMergeOperation, WorkMergeChange)

MODELS = (Work, WorkAlias, WorkTag, TagSuppression, ActivityWork, Recommendation, Vote, PublicRecommendation, PublicLike, PendingActivitySubmission)
RELATIONS = (Recommendation, Vote, PublicRecommendation, PublicLike)
TABLES = {model._meta.db_table: model for model in MODELS}


def snapshot(row):
    return json.loads(json.dumps({f.attname: getattr(row, f.attname) for f in row._meta.concrete_fields}, default=lambda value: value.isoformat()))


def rows_for(first, second):
    return [row for model in MODELS for row in model.objects.filter(**({'pk__in': [first, second]} if model is Work else {'work_id__in': [first, second]})).order_by('pk')]


def fingerprint(first, second):
    events = ChangeEvent.objects.filter(work_id__in=[first, second], table_name__in=TABLES).order_by('-id').first()
    return events.pk if events else 0


@transaction.atomic
def plan(first, second):
    primary, secondary = moderation.get_work(first), moderation.get_work(second)
    if first == second or primary.review_status != 'clear' or primary.hidden_at:
        raise services.DomainError('invalid_merge', '请选择不同的正式主作品', 409)
    if secondary.wenku8_book_id and secondary.wenku8_book_id != primary.wenku8_book_id:
        raise services.DomainError('source_conflict', '来源身份不同；有来源身份的作品须作为主作品', 409)
    conflicts = []
    for field in ('title', 'author', 'description', 'cover_url'):
        if getattr(primary, field+'_overridden') and getattr(secondary, field+'_overridden') and getattr(primary, field+'_override') != getattr(secondary, field+'_override'):
            conflicts.append({'key': field, 'label': {'title':'书名','author':'作者','description':'简介','cover_url':'封面'}[field], 'primary': getattr(primary, field+'_override'), 'secondary': getattr(secondary, field+'_override')})
    for row in Recommendation.objects.filter(work=secondary, archived_at__isnull=True, invalidated_at__isnull=True, withdrawn_at__isnull=True):
        other = Recommendation.objects.filter(work=primary, anonymous_client=row.anonymous_client, scope=row.scope,
            activity_id=row.activity_id, archived_at__isnull=True, invalidated_at__isnull=True, withdrawn_at__isnull=True).first()
        if other and other.updated_at == row.updated_at:
            conflicts.append({'key': 'reason:'+str(row.pk), 'label': f'理由 #{other.pk} / #{row.pk}', 'primary': other.reason, 'secondary': row.reason})
    if secondary.review_status == 'pending':
        seen = set()
        for claim in PendingActivitySubmission.objects.filter(work=secondary).exclude(activity__status__in=['draft','cancelled']).order_by('-created_at','-id'):
            key = (claim.activity_id, claim.anonymous_client_id)
            if key in seen: continue
            seen.add(key)
            other = Recommendation.objects.filter(work=primary, activity_id=claim.activity_id, anonymous_client_id=claim.anonymous_client_id,
                archived_at__isnull=True, invalidated_at__isnull=True, withdrawn_at__isnull=True).first()
            if other and other.updated_at == claim.created_at:
                conflicts.append({'key':'pending:'+str(claim.pk),'label':f'活动申请 #{claim.pk}', 'primary':other.reason,'secondary':claim.reason})
    return {'primary': primary, 'secondary': secondary, 'conflicts': conflicts, 'version': fingerprint(first, second),
        'pending_count': PendingActivitySubmission.objects.filter(work=secondary).count(),
        'snapshot_count': ActivityWork.objects.filter(work_id__in=[first,second],archived_at__isnull=True).count()}


def save(row, **values):
    for key, value in values.items():
        setattr(row, key, value)
    row.save(update_fields=[*values, 'updated_at'])


def live(row):
    return not any(getattr(row, field, None) for field in ('archived_at', 'invalidated_at', 'withdrawn_at', 'cancelled_at'))


@transaction.atomic
def merge(actor, first, second, choices, version, note):
    services.require_active(actor); note = moderation.reason(note); proposal = plan(first, second)
    if version != proposal['version']:
        raise services.DomainError('stale_merge', '作品已变更，请重新预览', 409)
    if set(choices) != {item['key'] for item in proposal['conflicts']} or any(value not in ('primary', 'secondary') for value in choices.values()):
        raise services.DomainError('merge_choices', '须逐项选择全部冲突字段', 409)
    primary, secondary = proposal['primary'], proposal['secondary']
    before = {(row._meta.db_table, row.pk): snapshot(row) for row in rows_for(first, second)}
    operation = WorkMergeOperation.objects.create(primary_work=primary, merged_work=secondary, admin=actor, reason=note, status='completed')
    operation.depends_on.set(WorkMergeOperation.objects.filter(status='completed').filter(Q(primary_work_id__in=[first, second]) | Q(merged_work_id__in=[first, second])).exclude(pk=operation.pk))
    pending_keys = {}
    if secondary.review_status == 'pending':
        save(secondary, review_status='clear')
        pending_keys = moderation.release_pending(secondary, actor, note, target_work=primary)
    materialized = {(row._meta.db_table,row.pk): snapshot(row) for row in rows_for(first, second)}
    updates = {}
    for field in ('title', 'author', 'description', 'cover_url'):
        if getattr(secondary, field+'_overridden') and (not getattr(primary, field+'_overridden') or choices.get(field) == 'secondary'):
            updates[field+'_overridden'] = True; updates[field+'_override'] = getattr(secondary, field+'_override')
    collected = [value for value in (primary.public_added_at, secondary.public_added_at) if value]
    if collected:
        updates['public_added_at'] = min(collected)
    if updates:
        save(primary, **updates)
    now = timezone.now()
    # Moderation history follows the canonical identity too; otherwise a merged ID could escape it.
    for model in RELATIONS:
        for row in model.objects.filter(work=secondary, archived_at__isnull=False):
            save(row, work_id=first)
    # Archive relations first to satisfy snapshot ownership and conditional uniqueness constraints.
    moving = [row for model in RELATIONS for row in model.objects.filter(work=secondary, archived_at__isnull=True)]
    for row in moving:
        save(row, archived_at=now)
    for snap in ActivityWork.objects.filter(work=secondary, archived_at__isnull=True):
        target = ActivityWork.objects.filter(work=primary, activity=snap.activity, archived_at__isnull=True).first()
        if target:
            save(snap, archived_at=now, merged_into_id=target.pk)
        else:
            save(snap, work_id=first)
    for row in moving:
        original = materialized[(row._meta.db_table, row.pk)]
        active = not any(original.get(field) for field in ('invalidated_at', 'withdrawn_at', 'cancelled_at'))
        query = type(row).objects.filter(work=primary, anonymous_client_id=row.anonymous_client_id, archived_at__isnull=True)
        if isinstance(row, (Recommendation, Vote)):
            query = query.filter(scope=row.scope, activity_id=row.activity_id)
        other = next((candidate for candidate in query if live(candidate)), None) if active else None
        keep = other is None
        if isinstance(row, Recommendation) and other:
            keep = original['updated_at'] > snapshot(other)['updated_at'] or (original['updated_at'] == snapshot(other)['updated_at'] and choices.get(pending_keys.get(row.pk,'reason:'+str(row.pk))) == 'secondary')
            # Hidden text and invalidation cannot be escaped through merging.
            if row.text_hidden_at or other.text_hidden_at:
                save(other, text_hidden_at=row.text_hidden_at or other.text_hidden_at)
                row.text_hidden_at = row.text_hidden_at or other.text_hidden_at
            if keep:
                save(other, archived_at=now)
        if keep:
            values = {'work_id': first, 'archived_at': None}
            if isinstance(row, (Recommendation, Vote)) and row.activity_id:
                values['activity_work_id'] = ActivityWork.objects.get(work=primary, activity_id=row.activity_id, archived_at__isnull=True).pk
            if isinstance(row, Recommendation):
                values['text_hidden_at'] = row.text_hidden_at
            save(row, **values)
    for row in Recommendation.objects.filter(work=primary, archived_at__isnull=True, text_hidden_at__isnull=True):
        hidden = Recommendation.objects.filter(work_id__in=[first, second], anonymous_client_id=row.anonymous_client_id,
            scope=row.scope, activity_id=row.activity_id, text_hidden_at__isnull=False).first()
        if hidden:
            save(row, text_hidden_at=hidden.text_hidden_at)
    for model in (Recommendation, PublicRecommendation, PublicLike):
        for row in model.objects.filter(work=primary, archived_at__isnull=True, invalidated_at__isnull=True):
            history = model.objects.filter(work_id__in=[first,second], anonymous_client_id=row.anonymous_client_id, invalidated_at__isnull=False)
            if model is Recommendation:
                history = history.filter(scope=row.scope, activity_id=row.activity_id)
            invalid = history.first()
            if invalid:
                save(row, invalidated_at=invalid.invalidated_at)
    for row in WorkAlias.objects.filter(work=secondary, archived_at__isnull=True):
        if WorkAlias.objects.filter(work=primary, alias=row.alias, archived_at__isnull=True).exists():
            save(row, archived_at=now)
        else:
            save(row, work_id=first)
    if secondary.display_title != primary.display_title and not WorkAlias.objects.filter(work=primary, alias=secondary.display_title, archived_at__isnull=True).exists():
        WorkAlias.objects.create(work=primary, alias=secondary.display_title,
            normalized_alias=catalog.search_key(secondary.display_title), created_by=actor)
    for row in WorkTag.objects.filter(work=secondary, state='active'):
        duplicate = WorkTag.objects.filter(work=primary, tag=row.tag, state='active', source_type=row.source_type,
            anonymous_client_id=row.anonymous_client_id, admin_id=row.admin_id).exists()
        save(row, **({'state': 'merge_archived'} if duplicate else {'work_id': first}))
    for row in TagSuppression.objects.filter(work=secondary, is_active=True):
        duplicate = TagSuppression.objects.filter(work=primary, tag=row.tag, scope=row.scope, is_active=True).exists()
        if not duplicate:
            save(row, work_id=first)
    # Duplicate hidden work must never become public through a merge.
    if secondary.hidden_at and not primary.hidden_at:
        save(primary, hidden_at=secondary.hidden_at)
    save(secondary, merged_into_id=first)
    moderation.corrected(work_id=first); moderation.corrected(work_id=second)
    for row in rows_for(first, second):
        key = (row._meta.db_table, row.pk); after = snapshot(row)
        if before.get(key) != after:
            WorkMergeChange.objects.create(operation=operation, table_name=key[0], record_id=key[1], before_data=before.get(key, {}),
                after_data=after, before_sequence=version, after_sequence=fingerprint(first, second), old_work_id=second, new_work_id=first, action='update')
    services.audit(actor, 'work.merge', operation, after={'primary': first, 'secondary': second}, reason=note)
    return operation


@transaction.atomic
def undo(actor, pk, note):
    services.require_active(actor); note = moderation.reason(note)
    operation = WorkMergeOperation.objects.filter(pk=pk, status='completed').first()
    if operation is None:
        raise services.DomainError('not_found', '合并不存在或已撤销', 404)
    if WorkMergeOperation.objects.filter(depends_on=operation, status='completed').exists():
        raise services.DomainError('merge_dependency', '请先撤销后续依赖合并', 409)
    changes = list(WorkMergeChange.objects.filter(operation=operation))
    boundary = max(change.after_sequence for change in changes)
    if fingerprint(operation.primary_work_id, operation.merged_work_id) != boundary:
        raise services.DomainError('merge_modified', '合并后已有内容变更，不能直接撤销', 409)
    for change in changes:
        row = TABLES[change.table_name].objects.filter(pk=change.record_id).first()
        if row is None or snapshot(row) != change.after_data:
            raise services.DomainError('merge_modified', '合并后已有内容变更，不能直接撤销', 409)
    # Clear conditional uniqueness and snapshot references before restoring originals.
    for change in changes:
        model = TABLES[change.table_name]
        if model in RELATIONS:
            model.objects.filter(pk=change.record_id).update(archived_at=timezone.now())
        elif model is WorkAlias:
            model.objects.filter(pk=change.record_id).update(archived_at=timezone.now())
        elif model is WorkTag:
            model.objects.filter(pk=change.record_id).update(state='merge_archived')
        elif model is TagSuppression:
            model.objects.filter(pk=change.record_id).update(is_active=False)
    # New approval rows have protected snapshot FKs; remove children before their newly created snapshots.
    for model in (*RELATIONS, ActivityWork, WorkAlias):
        for change in changes:
            if TABLES[change.table_name] is model and not change.before_data:
                model.objects.filter(pk=change.record_id).delete()
    order = {model._meta.db_table: index for index, model in enumerate(MODELS)}
    for change in sorted(changes, key=lambda c: order[c.table_name]):
        model = TABLES[change.table_name]
        if not change.before_data:
            continue
        values = {f.attname: f.to_python(change.before_data[f.attname]) for f in model._meta.concrete_fields if not f.primary_key}
        model.objects.filter(pk=change.record_id).update(**values)
    operation.status='undone'; operation.undone_at=timezone.now(); operation.save(update_fields=['status','undone_at'])
    moderation.corrected(work_id=operation.primary_work_id); moderation.corrected(work_id=operation.merged_work_id)
    services.audit(actor, 'work.merge.undo', operation, reason=note)
