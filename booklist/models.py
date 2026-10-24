import uuid
from django.db import models
from django.db.models import Q, F
from django.db.models.functions import Length
from django.core.validators import MinValueValidator

models.CharField.register_lookup(Length)
models.TextField.register_lookup(Length)
P = models.PROTECT


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Admin(Timestamped):
    username = models.CharField(max_length=100, unique=True)
    password_hash = models.CharField(max_length=256)
    is_active = models.BooleanField(default=True)
    session_version = models.PositiveIntegerField(default=1)
    password_changed_at = models.DateTimeField(null=True)


class AdminSession(models.Model):
    token_hash = models.CharField(max_length=64, unique=True)
    admin = models.ForeignKey(Admin, on_delete=P)
    session_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)


class AnonymousClient(Timestamped):
    token_hash = models.CharField(max_length=64, unique=True)
    last_seen_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)


class Work(Timestamped):
    wenku8_book_id = models.PositiveIntegerField(null=True, unique=True, validators=[MinValueValidator(1)])
    original_submitted_title = models.TextField(null=True)
    wenku8_title = models.TextField(null=True)
    wenku8_author = models.TextField(null=True)
    wenku8_description = models.TextField(null=True)
    wenku8_cover_url = models.TextField(null=True)
    wenku8_detail_url = models.TextField(null=True)
    source_metadata = models.JSONField(default=dict)
    title_override = models.CharField(max_length=300, default='')
    title_overridden = models.BooleanField(default=False)
    author_override = models.CharField(max_length=300, default='')
    author_overridden = models.BooleanField(default=False)
    description_override = models.TextField(default='')
    description_overridden = models.BooleanField(default=False)
    cover_url_override = models.TextField(default='')
    cover_url_overridden = models.BooleanField(default=False)
    identity_status = models.CharField(max_length=20, default='unmatched')
    review_status = models.CharField(max_length=20, default='clear')
    public_added_at = models.DateTimeField(null=True)
    hidden_at = models.DateTimeField(null=True)
    merged_into = models.ForeignKey('self', null=True, on_delete=P, related_name='merged_works')

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(wenku8_book_id__isnull=True) | Q(wenku8_book_id__gt=0), name='work_source_positive'),
            models.CheckConstraint(condition=Q(review_status__in=['clear', 'pending']), name='work_review_state'),
            models.CheckConstraint(condition=Q(identity_status__in=['unmatched', 'matched']), name='work_identity_state'),
            models.CheckConstraint(condition=(Q(identity_status='matched', wenku8_book_id__isnull=False) | Q(identity_status='unmatched', wenku8_book_id__isnull=True)), name='work_identity_binding'),
            models.CheckConstraint(condition=~Q(merged_into=F('id')), name='work_no_self_merge'),
            models.CheckConstraint(condition=(Q(title_overridden=True) & ~Q(title_override='')) | (Q(title_overridden=False) & ((Q(wenku8_title__isnull=False) & ~Q(wenku8_title='')) | (Q(original_submitted_title__isnull=False) & ~Q(original_submitted_title='')))), name='work_display_title_exists'),
            models.CheckConstraint(condition=Q(title_override__length__lte=300), name='work_override_title_limit'),
            models.CheckConstraint(condition=Q(description_override__length__lte=10000), name='work_override_intro_limit'),
        ]

    @property
    def display_title(self):
        return self.title_override if self.title_overridden else self.wenku8_title or self.original_submitted_title


class UserWorkMetadata(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    author = models.CharField(max_length=300, default='')
    description = models.TextField(default='')
    revision = models.PositiveIntegerField(default=1)
    archived_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['work'], condition=Q(archived_at__isnull=True), name='user_metadata_active_work'),
            models.CheckConstraint(condition=Q(description__length__lte=10000), name='user_metadata_description_limit'),
        ]


class SourceFieldState(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    field = models.CharField(max_length=50)
    state = models.CharField(max_length=20)
    raw_value = models.JSONField(null=True)
    error_code = models.CharField(max_length=80, default='')
    collection_complete = models.BooleanField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['work', 'field'], name='source_field_unique'),
            models.CheckConstraint(condition=Q(state__in=['value', 'empty', 'not_fetched', 'parse_error', 'invalid']), name='source_field_state')]


class WorkAlias(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    alias = models.CharField(max_length=300)
    normalized_alias = models.CharField(max_length=300, db_index=True)
    created_by = models.ForeignKey(Admin, null=True, on_delete=P)
    archived_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['work', 'alias'], condition=Q(archived_at__isnull=True), name='alias_active_unique')]


class Tag(Timestamped):
    name = models.CharField(max_length=300)
    normalized_name = models.CharField(max_length=300, db_index=True)
    is_active = models.BooleanField(default=True)
    merged_into = models.ForeignKey('self', null=True, on_delete=P)
    duplicate_review_needed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(merged_into=F('id')), name='tag_no_self_merge')]


class TagSource(Timestamped):
    source_key = models.CharField(max_length=300, unique=True)
    tag = models.ForeignKey(Tag, on_delete=P)
    original_name = models.CharField(max_length=300)


class WorkTag(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    tag = models.ForeignKey(Tag, on_delete=P)
    source_type = models.CharField(max_length=20)
    anonymous_client = models.ForeignKey(AnonymousClient, null=True, on_delete=P)
    admin = models.ForeignKey(Admin, null=True, on_delete=P)
    state = models.CharField(max_length=30, default='active')
    removed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(state__in=['active', 'source_removed', 'user_removed', 'admin_removed', 'merge_archived']), name='work_tag_state'),
            models.CheckConstraint(condition=Q(source_type='source', anonymous_client__isnull=True, admin__isnull=True) | Q(source_type='user', anonymous_client__isnull=False, admin__isnull=True) | Q(source_type='admin', anonymous_client__isnull=True, admin__isnull=False), name='work_tag_actor'),
            *[models.UniqueConstraint(fields=['work', 'tag'] + actor, condition=Q(state='active', source_type=kind), name='work_tag_unique_' + kind) for kind, actor in [('source', []), ('user', ['anonymous_client']), ('admin', ['admin'])]],
        ]


class TagSuppression(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    tag = models.ForeignKey(Tag, on_delete=P)
    scope = models.CharField(max_length=20)  # source or all
    admin = models.ForeignKey(Admin, on_delete=P)
    is_active = models.BooleanField(default=True)
    reason = models.TextField()

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(scope__in=['source', 'all']), name='suppression_scope'),
            models.UniqueConstraint(fields=['work', 'tag', 'scope'], condition=Q(is_active=True), name='suppression_active_unique')]


class Activity(Timestamped):
    title = models.CharField(max_length=100)
    theme = models.CharField(max_length=500, default='')
    start_at = models.DateTimeField()
    scheduled_end_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)
    cancelled_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=20, default='draft')
    end_reason = models.CharField(max_length=100, default='')
    cancel_reason = models.TextField(default='')
    created_by = models.ForeignKey(Admin, on_delete=P)
    corrected_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(start_at__lt=F('scheduled_end_at')), name='activity_time_order'),
            models.CheckConstraint(condition=Q(started_at__isnull=True) | Q(started_at__lt=F('scheduled_end_at')), name='activity_started_before_deadline'),
            models.CheckConstraint(condition=Q(ended_at__isnull=True) | (Q(ended_at__gte=F('started_at')) & Q(ended_at__lte=F('scheduled_end_at'))), name='activity_end_consistency'),
            models.CheckConstraint(condition=Q(status='draft', started_at__isnull=True, ended_at__isnull=True, cancelled_at__isnull=True) | Q(status='scheduled', started_at__isnull=True, ended_at__isnull=True, cancelled_at__isnull=True) | Q(status__in=['active','paused'], started_at__isnull=False, ended_at__isnull=True, cancelled_at__isnull=True) | Q(status='ended', started_at__isnull=False, ended_at__isnull=False, cancelled_at__isnull=True) | Q(status='cancelled', started_at__isnull=True, ended_at__isnull=True, cancelled_at__isnull=False), name='activity_state_times')]


class ActivityTransition(models.Model):
    activity = models.ForeignKey(Activity, on_delete=P)
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    effective_at = models.DateTimeField()
    processed_at = models.DateTimeField()
    admin = models.ForeignKey(Admin, null=True, on_delete=P)
    reason = models.CharField(max_length=100)


class ActivityWork(Timestamped):
    activity = models.ForeignKey(Activity, on_delete=P)
    work = models.ForeignKey(Work, on_delete=P)
    snapshot_title = models.CharField(max_length=300)
    snapshot_author = models.CharField(max_length=300, default='')
    snapshot_cover_url = models.TextField(default='')
    snapshot_description = models.TextField(null=True)
    snapshot_tags = models.JSONField(null=True)
    archived_at = models.DateTimeField(null=True)
    merged_into = models.ForeignKey('self', null=True, on_delete=P)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['activity', 'work'], condition=Q(archived_at__isnull=True), name='activity_work_active_unique'),
            models.CheckConstraint(condition=~Q(merged_into=F('id')), name='activity_work_not_self')]


class ScopedContribution(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    scope = models.CharField(max_length=20)
    activity = models.ForeignKey(Activity, null=True, on_delete=P)
    activity_work = models.ForeignKey(ActivityWork, null=True, on_delete=P)
    archived_at = models.DateTimeField(null=True)

    class Meta:
        abstract = True


class Recommendation(ScopedContribution):
    reason = models.TextField(default='')
    contains_spoilers = models.BooleanField(default=False)
    reason_references = models.JSONField(default=list)
    withdrawn_at = models.DateTimeField(null=True)
    text_hidden_at = models.DateTimeField(null=True)
    invalidated_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(scope='public', activity__isnull=True, activity_work__isnull=True, withdrawn_at__isnull=True) | Q(scope='activity', activity__isnull=False, activity_work__isnull=False), name='recommendation_scope'),
            models.CheckConstraint(condition=~Q(scope='public', reason=''), name='public_reason_nonempty'),
            models.CheckConstraint(condition=Q(reason__length__lte=200), name='reason_length'),
            models.UniqueConstraint(fields=['work', 'anonymous_client'], condition=Q(scope='public', archived_at__isnull=True, invalidated_at__isnull=True), name='public_reason_active_unique'),
            models.UniqueConstraint(fields=['activity', 'work', 'anonymous_client'], condition=Q(scope='activity', withdrawn_at__isnull=True, archived_at__isnull=True, invalidated_at__isnull=True), name='activity_rec_active_unique')]


class ReasonLike(Timestamped):
    recommendation = models.ForeignKey(Recommendation, on_delete=models.CASCADE)
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    cancelled_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['recommendation', 'anonymous_client'], name='reason_like_person_unique')]


class Vote(ScopedContribution):
    cancelled_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(scope='public', activity__isnull=True, activity_work__isnull=True) | Q(scope='activity', activity__isnull=False, activity_work__isnull=False), name='vote_scope'),
            models.UniqueConstraint(fields=['work', 'anonymous_client'], condition=Q(scope='public', archived_at__isnull=True, cancelled_at__isnull=True), name='public_vote_active_unique'),
            models.UniqueConstraint(fields=['activity', 'work', 'anonymous_client'], condition=Q(scope='activity', archived_at__isnull=True, cancelled_at__isnull=True), name='activity_vote_active_unique')]


class PublicRecommendation(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    origin = models.CharField(max_length=20, default='submit')
    withdrawn_at = models.DateTimeField(null=True)
    invalidated_at = models.DateTimeField(null=True)
    archived_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(origin__in=['submit', 'plus_one', 'legacy', 'activity']), name='public_rec_origin'),
            models.UniqueConstraint(fields=['work', 'anonymous_client'], condition=Q(withdrawn_at__isnull=True, invalidated_at__isnull=True, archived_at__isnull=True), name='public_rec_person_unique'),
        ]


class PublicLike(Timestamped):
    work = models.ForeignKey(Work, on_delete=P)
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    cancelled_at = models.DateTimeField(null=True)
    invalidated_at = models.DateTimeField(null=True)
    archived_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['work', 'anonymous_client'], condition=Q(cancelled_at__isnull=True, invalidated_at__isnull=True, archived_at__isnull=True), name='public_like_person_unique')]


class PendingActivitySubmission(Timestamped):
    activity = models.ForeignKey(Activity, on_delete=P)
    work = models.ForeignKey(Work, on_delete=P)
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    reason = models.TextField(default='')
    copy_public_reason = models.BooleanField(default=False)
    contains_spoilers = models.BooleanField(default=False)
    reason_references = models.JSONField(default=list)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(reason__length__lte=200), name='pending_activity_reason_limit')]


class WorkMergeOperation(models.Model):
    primary_work = models.ForeignKey(Work, on_delete=P, related_name='primary_operations')
    merged_work = models.ForeignKey(Work, on_delete=P, related_name='merged_operations')
    admin = models.ForeignKey(Admin, on_delete=P)
    reason = models.TextField()
    status = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True)
    depends_on = models.ManyToManyField('self', symmetrical=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(primary_work=F('merged_work')), name='merge_different_works'),
            models.CheckConstraint(condition=Q(status__in=['completed', 'undone']), name='merge_status')]


class WorkMergeChange(models.Model):
    operation = models.ForeignKey(WorkMergeOperation, on_delete=P)
    table_name = models.CharField(max_length=100)
    record_id = models.PositiveBigIntegerField()
    before_data = models.JSONField()
    after_data = models.JSONField()
    before_sequence = models.PositiveBigIntegerField()
    after_sequence = models.PositiveBigIntegerField()
    old_work_id = models.PositiveBigIntegerField(null=True)
    new_work_id = models.PositiveBigIntegerField(null=True)
    action = models.CharField(max_length=30)


class AdminAuditLog(models.Model):
    admin = models.ForeignKey(Admin, null=True, on_delete=P)
    action = models.CharField(max_length=80)
    target_type = models.CharField(max_length=100)
    target_id = models.PositiveBigIntegerField()
    before_data = models.JSONField(default=dict)
    after_data = models.JSONField(default=dict)
    reason = models.TextField(default='')
    created_at = models.DateTimeField(auto_now_add=True)


class ChangeEvent(models.Model):
    table_name = models.CharField(max_length=100)
    record_id = models.PositiveBigIntegerField()
    work_id = models.PositiveBigIntegerField(null=True)  # Tombstones intentionally have no FK.
    action = models.CharField(max_length=10)
    occurred_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=['table_name', 'record_id', 'id'], name='change_entity_sequence')]


class IdempotencyRecord(models.Model):
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    scope = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['anonymous_client', 'scope', 'key_hash'], name='idempotency_actor_scope_key')]


class RateLimitBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)


class SourceIdentityReview(Timestamped):
    source_work = models.OneToOneField(Work, on_delete=P, related_name='source_identity_review')
    book_id = models.PositiveIntegerField(unique=True)
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    candidates = models.JSONField(default=list)
    status = models.CharField(max_length=20, default='pending')
    resolved_work = models.ForeignKey(Work, on_delete=P, null=True, related_name='resolved_source_reviews')
    resolved_by = models.ForeignKey(Admin, on_delete=P, null=True)
    resolved_at = models.DateTimeField(null=True)
    reason = models.TextField(default='')

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(status__in=['pending', 'linked', 'separate']), name='source_identity_review_status')]


class ImportBatch(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    manifest_path = models.TextField()
    manifest_hash = models.CharField(max_length=64)
    parser_fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=30, default='pending')


class ImportItem(Timestamped):
    batch = models.ForeignKey(ImportBatch, on_delete=P, related_name='items')
    key = models.CharField(max_length=100)
    book_id = models.PositiveIntegerField()
    source_url = models.TextField()
    relative_path = models.TextField()
    content_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default='pending')
    work = models.ForeignKey(Work, null=True, on_delete=P)
    error_code = models.CharField(max_length=100, default='')
    warnings = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'key'], name='import_batch_item_key'),
            models.CheckConstraint(condition=Q(status__in=['pending', 'imported', 'partial', 'failed']), name='import_item_status')]


class ImportAttempt(models.Model):
    item = models.ForeignKey(ImportItem, on_delete=P, related_name='attempts')
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20)
    error_code = models.CharField(max_length=100, default='')
    warnings = models.JSONField(default=list)
    snapshot = models.JSONField(default=dict)


class Feedback(Timestamped):
    anonymous_client = models.ForeignKey(AnonymousClient, on_delete=P)
    category = models.CharField(max_length=20, choices=[('metadata', '资料错误'), ('content', '不当内容'), ('votes', '疑似刷票'), ('other', '其他问题')])
    message = models.TextField()
    page_url = models.CharField(max_length=1000, default='')
    status = models.CharField(max_length=20, default='pending', choices=[('pending', '待处理'), ('resolved', '已处理'), ('dismissed', '暂不处理')])
    reply = models.CharField(max_length=500, default='')
    handled_by = models.ForeignKey(Admin, null=True, on_delete=P)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(category__in=['metadata', 'content', 'votes', 'other']), name='feedback_category'),
            models.CheckConstraint(condition=Q(status__in=['pending', 'resolved', 'dismissed']), name='feedback_status'),
            models.CheckConstraint(condition=~Q(message='') & Q(message__length__lte=1000), name='feedback_message_limit'),
        ]
