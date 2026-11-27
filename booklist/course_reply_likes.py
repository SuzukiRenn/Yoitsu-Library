"""Book-help reply visibility and reactions, independent of work likes."""
from django.db.models import BooleanField, Count, Exists, OuterRef, Q, Value
from django.utils import timezone

from . import catalog
from .course_models import BookCandidate, BookCandidateLike
from .models import Notification, WorkLikeEvent


def visible_replies(candidates):
    return candidates.filter(hidden=False, work_id__in=catalog.visible_works().values('pk'))


def public_replies():
    return visible_replies(BookCandidate.objects.filter(question__hidden=False, question__deleted_at__isnull=True))


def with_likes(replies, account):
    active = Q(likes__cancelled_at__isnull=True, likes__actor__is_active=True,
               likes__actor__principal__revoked_at__isnull=True)
    own = BookCandidateLike.objects.filter(candidate_id=OuterRef('pk'), actor=account, cancelled_at__isnull=True)
    return replies.annotate(like_count=Count('likes', filter=active),
                            liked=Exists(own) if account else Value(False, output_field=BooleanField()))


def scope_key(question_id):
    return f'book_help:{question_id}'


def own_notice(candidate, actor):
    return Notification.objects.filter(event__kind=WorkLikeEvent.REPLY_LIKE,
        event__scope_key=scope_key(candidate.question_id), event__subject_id=candidate.pk, event__actor=actor)


def retract_question_likes(question_id):
    Notification.objects.filter(event__kind=WorkLikeEvent.REPLY_LIKE,
        event__scope_key=scope_key(question_id), retracted_at__isnull=True).update(retracted_at=timezone.now())


def set_like(candidate, actor, enabled):
    # The caller holds the question write transaction. Each actor owns one reaction
    # and one lifetime notification, even if they cancel and like again.
    if not enabled:
        now = timezone.now()
        BookCandidateLike.objects.filter(candidate=candidate, actor=actor, cancelled_at__isnull=True).update(
            cancelled_at=now, updated_at=now)
        own_notice(candidate, actor).filter(retracted_at__isnull=True).update(retracted_at=now)
        return
    row, created = BookCandidateLike.objects.get_or_create(candidate=candidate, actor=actor)
    if not created and row.cancelled_at is None:
        return
    if not created:
        row.cancelled_at = None
        row.save(update_fields=('cancelled_at', 'updated_at'))
    event, new_event = WorkLikeEvent.objects.get_or_create(work_id=candidate.work_id, actor=actor,
        scope_key=scope_key(candidate.question_id), kind=WorkLikeEvent.REPLY_LIKE, subject_id=candidate.pk)
    author = candidate.author
    if new_event and author.is_active and author.principal.revoked_at is None:
        Notification.objects.create(event=event, recipient=author, work_title=candidate.question.title)
