"""Quarantine possible source/manual duplicates without blocking unrelated imports."""
from difflib import SequenceMatcher
import hashlib
import json
from django.db import transaction
from django.utils import timezone
from . import catalog, services
from .importing import apply_snapshot, validate_snapshot
from .models import (Work, WorkAlias, SourceIdentityReview, ActivityWork, Recommendation,
                     PublicRecommendation, PublicLike, Vote, PendingActivitySubmission, UserWorkMetadata)


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def manual_candidates(title):
    key = catalog.search_key(title)
    candidates = []
    for work in Work.objects.filter(wenku8_book_id__isnull=True, merged_into__isnull=True).order_by('id'):
        names = [work.display_title, *WorkAlias.objects.filter(work=work, archived_at__isnull=True).values_list('alias', flat=True)]
        kind = None
        for name in names:
            other = catalog.search_key(name)
            if key == other:
                kind = '同名或同别名'; break
            if min(len(key), len(other)) >= 4 and SequenceMatcher(None, key, other, autojunk=False).ratio() >= .8:
                kind = '名称相近'
        if kind:
            candidates.append({'work_id': work.pk, 'title': work.display_title, 'match': kind})
    return candidates


@transaction.atomic
def import_with_review(snapshot):
    fields, _ = validate_snapshot(snapshot)
    existing = Work.objects.filter(wenku8_book_id=snapshot['book_id']).first()
    candidates = manual_candidates(fields['title']['value']) if existing is None else []
    work, warnings, created = apply_snapshot(snapshot)
    if candidates:
        work.review_status = 'pending'
        work.save(update_fields=['review_status', 'updated_at'])
        review = SourceIdentityReview.objects.create(source_work=work, book_id=snapshot['book_id'],
            snapshot=snapshot, snapshot_hash=digest(snapshot), candidates=candidates)
        services.audit(None, 'source.identity.pending', review,
            after={'source_work_id': work.pk, 'candidate_ids': [c['work_id'] for c in candidates]})
    return work, warnings, created


@transaction.atomic
def resolve(actor, pk, action, work_id, expected_hash, reason):
    from .moderation import reason as normalize_reason
    services.require_active(actor); reason = normalize_reason(reason)
    review = SourceIdentityReview.objects.select_for_update().filter(pk=pk).first()
    if review is None:
        raise services.DomainError('not_found', '核对记录不存在', 404)
    if review.status != 'pending' or expected_hash != review.snapshot_hash or digest(review.snapshot) != expected_hash:
        raise services.DomainError('stale_review', '核对资料已变更，请刷新页面', 409)
    validate_snapshot(review.snapshot)
    source = Work.objects.get(pk=review.source_work_id)
    if source.wenku8_book_id != review.book_id or source.merged_into_id or source.review_status != 'pending':
        raise services.DomainError('source_changed', '来源作品已变更，请重新核对', 409)
    if action == 'separate':
        if work_id != 0:
            raise services.DomainError('invalid_target', '独立作品无需关联目标')
        source.review_status = 'clear'; source.save(update_fields=['review_status', 'updated_at'])
        target = source
    elif action == 'link':
        if work_id not in [c['work_id'] for c in review.candidates]:
            raise services.DomainError('invalid_target', '请选择待核对的原作品')
        target = Work.objects.filter(pk=work_id, wenku8_book_id__isnull=True, merged_into__isnull=True,
            hidden_at__isnull=True, review_status='clear').first()
        if target is None:
            raise services.DomainError('target_changed', '原作品已变更，请重新核对', 409)
        # Source quarantine rows have never been public. Refuse to absorb any contributions.
        related = (ActivityWork, Recommendation, PublicRecommendation, PublicLike, Vote, PendingActivitySubmission, UserWorkMetadata)
        if source.public_added_at or any(model.objects.filter(work=source).exists() for model in related):
            raise services.DomainError('source_in_use', '来源作品已有用户内容，需通过作品合并处理', 409)
        source.wenku8_book_id = None; source.identity_status = 'unmatched'; source.merged_into = target
        source.save(update_fields=['wenku8_book_id', 'identity_status', 'merged_into', 'updated_at'])
        target.wenku8_book_id = review.book_id; target.identity_status = 'matched'
        target.save(update_fields=['wenku8_book_id', 'identity_status', 'updated_at'])
        apply_snapshot(review.snapshot)
        target.refresh_from_db()
        if UserWorkMetadata.objects.filter(work=target, archived_at__isnull=True).exclude(description='').exists():
            target.source_metadata['preserve_user_description'] = True
            target.save(update_fields=['source_metadata', 'updated_at'])
        if source.display_title != target.original_submitted_title:
            title = target.original_submitted_title
            if title:
                WorkAlias.objects.get_or_create(work=target, alias=title, archived_at=None,
                    defaults={'normalized_alias': catalog.search_key(title), 'created_by': actor})
    else:
        raise services.DomainError('invalid_action', '操作无效')
    review.status = 'linked' if action == 'link' else 'separate'
    review.resolved_work = target; review.resolved_by = actor; review.resolved_at = timezone.now(); review.reason = reason
    review.save(update_fields=['status', 'resolved_work', 'resolved_by', 'resolved_at', 'reason', 'updated_at'])
    services.audit(actor, 'source.identity.'+action, review,
        before={'source_work_id': source.pk, 'book_id': review.book_id}, after={'work_id': target.pk}, reason=reason)
    return target
