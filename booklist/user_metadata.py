"""Owner-editable manual metadata, separate from imported and admin fields."""
import unicodedata
from django.db.models import Count, Q, Exists, OuterRef
from django.core.exceptions import ValidationError
from django.utils import timezone
from . import services
from .models import TagSource, UserWorkMetadata, Tag, WorkTag, TagSuppression, PublicRecommendation, Recommendation, PendingActivitySubmission
from .text import normalize
from .protocol import pagination


def tag_key(name):
    return unicodedata.normalize('NFKC', name).casefold()


def tag_index(rows):
    active, known = {}, set()
    for tag in rows:
        key = tag_key(tag.name)
        known.add(key)
        if tag.is_active and tag.merged_into_id is None:
            active.setdefault(key, tag)
    return active, known


def tag_suggestions(query, work=None, client=None, *, page=1, exclude=()):
    _, size = pagination(page, 8)
    if not isinstance(exclude, (list, tuple)) or len(exclude) > 20 or any(not isinstance(name, str) or len(name) > 30 for name in exclude):
        raise ValidationError('已选标签无效')
    excluded = {tag_key(normalize(name, 'title')) for name in exclude}
    query = normalize(query, 'title')
    if len(query) > 30:
        raise ValidationError('标签查询最多30字')
    key = tag_key(query)
    from . import catalog
    links = WorkTag.objects.filter(tag_id=OuterRef('pk'))
    activity_support = Recommendation.objects.filter(scope='activity', withdrawn_at__isnull=True,
        archived_at__isnull=True, invalidated_at__isnull=True, activity_work__archived_at__isnull=True,
        activity_work__merged_into__isnull=True, activity__status__in=['active', 'paused', 'ended'],
        work_id__in=catalog.visible_works().values('id'))
    supported = links.filter(state='active').filter(Q(source_type__in=['source', 'admin']) |
        Q(work_id__in=catalog.public_works().values('id')) | Q(work_id__in=activity_support.values('work_id')))
    rows = Tag.objects.annotate(usage=Count('worktag', filter=Q(worktag__state='active')),
        has_history=Exists(links), supported=Exists(supported),
        trusted=Exists(TagSource.objects.filter(tag_id=OuterRef('pk')))).order_by('id')
    active, _ = tag_index(rows)
    # Keep saved metadata; exclude unsupported manual vocabulary from suggestions.
    allowed = {tag_key(tag.name) for tag in rows if tag.supported or tag.trusted or not tag.has_history}
    banned = set()
    if work:
        banned.update(TagSuppression.objects.filter(work=work, scope='all', is_active=True).values_list('tag_id', flat=True))
        banned.update(WorkTag.objects.filter(work=work, anonymous_client=client, source_type='user',
            state='admin_removed').values_list('tag_id', flat=True))
    matches = [(0 if name == key else 1 if name.startswith(key) else 2, -tag.usage, tag.name, tag.pk)
        for name, tag in active.items() if name in allowed and (not key or key in name) and name not in excluded and len(tag.name) <= 30 and tag.pk not in banned]
    matches.sort()
    total = len(matches)
    pages = max(1, (total + size - 1)//size)
    current = min(int(page), pages)
    offset = (current - 1)*size
    return {'results': [{'id': pk, 'name': name} for _, _, name, pk in matches[offset:offset+size]],
        'total': total, 'page': current, 'pages': pages, 'page_size': size}


def validate(data):
    if type(data) is not dict or not {'description', 'tags'} <= set(data) or set(data) - {'author', 'description', 'tags'} or type(data['tags']) is not list or len(data['tags']) > 20:
        raise ValidationError('资料字段无效，标签最多20个')
    author = normalize(data.get('author', ''), 'title')
    if len(author) > 20:
        raise ValidationError('作者最多 20 字')
    description = normalize(data['description'], 'description')
    if len(description) > 2000:
        raise ValidationError('简介最多 2000 字')
    names = {}
    for raw in data['tags']:
        name = normalize(raw, 'title', required=True)
        if len(name) > 30 or any(ord(c) < 32 for c in name):
            raise ValidationError('每个标签限1至30字')
        key = tag_key(name)
        names.setdefault(key, name)
    return author, description, names


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
    author, description, names = validate(data if data is not None else {'description': '', 'tags': []})
    row = UserWorkMetadata.objects.create(work=work, anonymous_client=client, author=author, description=description)
    sync_tags(work, client, names)
    return row


def sync_tags(work, client, names):
    desired = []
    active, known = tag_index(Tag.objects.order_by('id'))
    for key, name in names.items():
        # Reuse existing active vocabulary (including source tags); never revive moderation.
        tag = active.get(key)
        if tag is None:
            if key in known:
                raise services.DomainError('tag_unavailable', '该标签暂不可用', 409)
            tag = Tag.objects.create(name=name, normalized_name=key)
            active[key] = tag; known.add(key)
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
    return {'author': row.author if row else '', 'description': row.description if row else '', 'tags': list(tags), 'revision': row.revision if row else 0}


def update(work, client, data, revision):
    if not editable(work, client):
        raise services.DomainError('metadata_forbidden', '仅原提交者可编辑未匹配作品的资料', 403)
    author, description, names = validate(data)
    row = UserWorkMetadata.objects.filter(work=work, archived_at__isnull=True).first()
    if revision != (row.revision if row else 0):
        raise services.DomainError('metadata_changed', '资料已更新，请刷新后重新编辑', 409)
    if row is None:
        row = UserWorkMetadata.objects.create(work=work, anonymous_client=client)
    before = {'author': row.author, 'description': row.description, 'revision': row.revision}
    sync_tags(work, client, names)
    if 'author' in data: row.author = author
    row.description = description
    row.revision += 1
    row.save(update_fields=['author', 'description', 'revision', 'updated_at'])
    services.audit(None, 'work.metadata.user_edit', work, before=before,
        after={'author': row.author, 'description': description, 'revision': row.revision, 'anonymous_client_id': client.pk, 'tags': list(names.values())})
    return {'status': 'saved', 'revision': row.revision}
