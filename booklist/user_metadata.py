"""Owner-editable manual metadata, separate from imported and admin fields."""
import unicodedata
from django.core.exceptions import ValidationError
from django.utils import timezone
from . import services
from .models import UserWorkMetadata, Tag, WorkTag, TagSuppression, PublicRecommendation, Recommendation, PendingActivitySubmission
from .text import normalize


def validate(data):
    if type(data) is not dict or set(data) != {'description', 'tags'} or type(data['tags']) is not list or len(data['tags']) > 20:
        raise ValidationError('资料字段无效，标签最多20个')
    description = normalize(data['description'], 'description')
    names = {}
    for raw in data['tags']:
        name = normalize(raw, 'title', required=True)
        if len(name) > 30 or any(ord(c) < 32 for c in name):
            raise ValidationError('每个标签限1至30字')
        key = unicodedata.normalize('NFKC', name).casefold()
        names.setdefault(key, name)
    return description, names


def owner_id(work):
    row = UserWorkMetadata.objects.filter(work=work, archived_at__isnull=True).first()
    if row:
        return row.anonymous_client_id
    # Legacy manual works: the earliest submitter retains ownership, including withdrawals.
    firsts = []
    for query in (PublicRecommendation.objects.filter(work=work, origin='submit'),
                  Recommendation.objects.filter(work=work, scope='activity'),
                  PendingActivitySubmission.objects.filter(work=work)):
        first = query.order_by('created_at', 'id').first()
        if first:
            firsts.append(first)
    return min(firsts, key=lambda row: (row.created_at, row.anonymous_client_id)).anonymous_client_id if firsts else None


def editable(work, client):
    return bool(client and work.wenku8_book_id is None and not work.merged_into_id and
                not work.hidden_at and work.review_status == 'clear' and owner_id(work) == client.pk)


def initialize(work, client, data):
    # Called only for a newly created manual identity, inside submission's transaction.
    description, names = validate(data if data is not None else {'description': '', 'tags': []})
    row = UserWorkMetadata.objects.create(work=work, anonymous_client=client, description=description)
    sync_tags(work, client, names)
    return row


def sync_tags(work, client, names):
    desired = []
    for key, name in names.items():
        # Reuse existing active vocabulary (including source tags); never revive moderation.
        tag = Tag.objects.filter(normalized_name__in=[name, key], is_active=True, merged_into__isnull=True).order_by('id').first()
        if tag is None:
            if Tag.objects.filter(normalized_name__in=[name, key]).exists():
                raise services.DomainError('tag_unavailable', '该标签暂不可用', 409)
            tag = Tag.objects.create(name=name, normalized_name=key)
        if TagSuppression.objects.filter(work=work, tag=tag, scope='all', is_active=True).exists():
            raise services.DomainError('tag_unavailable', '该标签暂不可用', 409)
        desired.append(tag.pk)
        # Admin removal of this actor's link cannot be bypassed with a fresh link.
        if WorkTag.objects.filter(work=work, tag=tag, source_type='user', anonymous_client=client, state='admin_removed').exists():
            raise services.DomainError('tag_unavailable', '该标签暂不可用', 409)
        WorkTag.objects.get_or_create(work=work, tag=tag, source_type='user', anonymous_client=client, state='active')
    WorkTag.objects.filter(work=work, source_type='user', anonymous_client=client, state='active').exclude(
        tag_id__in=desired).update(state='user_removed', removed_at=timezone.now(), updated_at=timezone.now())


def values(work, client):
    row = UserWorkMetadata.objects.filter(work=work, archived_at__isnull=True).first()
    tags = WorkTag.objects.filter(work=work, source_type='user', anonymous_client=client,
        state='active', tag__is_active=True, tag__merged_into__isnull=True).order_by('id').values_list('tag__name', flat=True)
    return {'description': row.description if row else '', 'tags': list(tags), 'revision': row.revision if row else 0}


def update(work, client, data, revision):
    if not editable(work, client):
        raise services.DomainError('metadata_forbidden', '仅原提交者可编辑未匹配作品的资料', 403)
    description, names = validate(data)
    row = UserWorkMetadata.objects.filter(work=work, archived_at__isnull=True).first()
    if revision != (row.revision if row else 0):
        raise services.DomainError('metadata_changed', '资料已更新，请刷新后重新编辑', 409)
    if row is None:
        row = UserWorkMetadata.objects.create(work=work, anonymous_client=client)
    before = {'description': row.description, 'revision': row.revision}
    sync_tags(work, client, names)
    row.description = description
    row.revision += 1
    row.save(update_fields=['description', 'revision', 'updated_at'])
    services.audit(None, 'work.metadata.user_edit', work, before=before,
        after={'description': description, 'revision': row.revision, 'anonymous_client_id': client.pk, 'tags': list(names.values())})
    return {'status': 'saved', 'revision': row.revision}
