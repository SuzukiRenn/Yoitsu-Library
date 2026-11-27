"""Course feature records, separate from existing shelf activity."""
from django.db import models

from .models import Account, Timestamped, Work

PROTECT = models.PROTECT


class ReadingRoute(Timestamped):
    work = models.ForeignKey(Work, on_delete=PROTECT)
    author = models.ForeignKey(Account, on_delete=PROTECT)
    reason = models.TextField()
    reason_references = models.JSONField(default=list)
    contains_spoilers = models.BooleanField(default=False)
    withdrawn = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('work', 'author'), name='course_reading_route_owner')]


class CuratedShelf(Timestamped):
    owner = models.ForeignKey(Account, on_delete=PROTECT)
    title = models.CharField(max_length=100)
    version = models.PositiveIntegerField(default=1)
    hidden = models.BooleanField(default=False)


class Curator(models.Model):
    shelf = models.ForeignKey(CuratedShelf, related_name='memberships', on_delete=models.CASCADE)
    account = models.ForeignKey(Account, on_delete=PROTECT)
    role = models.CharField(max_length=10, choices=[('member', '成员'), ('editor', '策展者')])

    class Meta:
        constraints = [models.UniqueConstraint(fields=('shelf', 'account'), name='course_curator_unique')]


class Nomination(Timestamped):
    shelf = models.ForeignKey(CuratedShelf, related_name='nominations', on_delete=models.CASCADE)
    work = models.ForeignKey(Work, on_delete=PROTECT)
    author = models.ForeignKey(Account, on_delete=PROTECT)
    reason = models.CharField(max_length=200)
    approved = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('shelf', 'work'), name='course_nomination_unique')]


class CuratedEdition(models.Model):
    shelf = models.ForeignKey(CuratedShelf, related_name='editions', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    entries = models.JSONField(default=list)
    published_by = models.ForeignKey(Account, on_delete=PROTECT)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('shelf', 'number'), name='course_edition_unique')]


class BookQuestion(Timestamped):
    author = models.ForeignKey(Account, on_delete=PROTECT)
    title = models.CharField(max_length=100)
    clues = models.TextField()
    accepted = models.ForeignKey('BookCandidate', null=True, on_delete=PROTECT, related_name='+')
    version = models.PositiveIntegerField(default=1)
    hidden = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True)


class BookClue(Timestamped):
    question = models.ForeignKey(BookQuestion, related_name='additions', on_delete=models.CASCADE)
    text = models.CharField(max_length=500)


class BookCandidate(Timestamped):
    question = models.ForeignKey(BookQuestion, related_name='candidates', on_delete=models.CASCADE)
    work = models.ForeignKey(Work, on_delete=PROTECT)
    author = models.ForeignKey(Account, on_delete=PROTECT)
    reason = models.CharField(max_length=200)
    hidden = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('question', 'work'), name='course_candidate_unique')]


class BookCandidateLike(Timestamped):
    candidate = models.ForeignKey(BookCandidate, related_name='likes', on_delete=PROTECT)
    actor = models.ForeignKey(Account, on_delete=PROTECT)
    cancelled_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('candidate', 'actor'), name='course_candidate_like_unique')]
