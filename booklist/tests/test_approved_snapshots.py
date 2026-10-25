from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from booklist import activities, moderation, merging, services, catalog
from booklist.models import Activity, ActivityWork, Work, UserWorkMetadata, Tag, WorkTag, PendingActivitySubmission, PublicRecommendation


class ApprovedSnapshotTests(TestCase):
    def setUp(self):
        self.admin = services.initialize_admin('snapshot-admin', 'Snapshot-Regression!Password-49')
        _, self.person = services.issue_anonymous()
        now = timezone.now()
        self.period = Activity.objects.create(title='快照回归', status='active', created_by=self.admin,
            start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1))
        self.work = Work.objects.create(original_submitted_title='手工作品', review_status='pending',
            wenku8_cover_url='https://img.wenku8.com/image/0/13/13s.jpg')
        UserWorkMetadata.objects.create(work=self.work, anonymous_client=self.person, author='手工作者', description='手工简介')
        self.tag = Tag.objects.create(name='合成快照标签')
        WorkTag.objects.create(work=self.work, tag=self.tag, source_type='user', anonymous_client=self.person)

    def pending(self):
        return PendingActivitySubmission.objects.create(work=self.work, activity=self.period,
            anonymous_client=self.person, reason='推荐理由', copy_public_reason=False)

    def assert_complete(self, work):
        snap = ActivityWork.objects.get(work=work, activity=self.period, archived_at=None)
        self.assertEqual(snap.snapshot_author, '手工作者')
        self.assertEqual(snap.snapshot_description, '手工简介')
        self.assertEqual(snap.snapshot_tags, [{'id': self.tag.pk, 'name': self.tag.name}])
        self.assertEqual(snap.snapshot_cover_url, 'https://img.wenku8.com/image/0/13/13s.jpg')
        self.assertEqual(activities.listing(self.period.pk, q='手工作者')['total'], 1)
        self.assertEqual(activities.listing(self.period.pk, tags=[self.tag.pk])['total'], 1)
        return snap

    def test_approval_captures_manual_metadata_and_filterable_tags(self):
        self.pending()
        moderation.approve(self.admin, self.work.pk, '确认独立作品')
        self.assert_complete(self.work)

    def test_normal_submission_uses_same_complete_snapshot(self):
        self.work.review_status = 'clear'; self.work.save()
        activities.submit(self.period.pk, self.person, {'work_id': self.work.pk, 'title': self.work.display_title,
            'reason': '理由', 'confirmed_unmatched': False, 'copy_public_reason': False})
        self.assert_complete(self.work)

    def test_pending_merge_creates_complete_snapshot_without_target_snapshot(self):
        self.pending()
        primary = Work.objects.create(original_submitted_title='合并主作品')
        plan = merging.plan(primary.pk, self.work.pk)
        merging.merge(self.admin, primary.pk, self.work.pk,
            {row['key']: 'primary' for row in plan['conflicts']}, plan['version'], '确认同一作品')
        self.assert_complete(primary)

    def test_pending_merge_preserves_existing_target_snapshot(self):
        self.pending()
        primary = Work.objects.create(original_submitted_title='主作品')
        old = ActivityWork.objects.create(activity=self.period, work=primary, snapshot_title='历史标题',
            snapshot_author='历史作者', snapshot_tags=[], snapshot_description='历史简介', snapshot_cover_url='')
        plan = merging.plan(primary.pk, self.work.pk)
        merging.merge(self.admin, primary.pk, self.work.pk,
            {row['key']: 'primary' for row in plan['conflicts']}, plan['version'], '确认重复')
        old.refresh_from_db()
        self.assertEqual((old.snapshot_title, old.snapshot_author, old.snapshot_description, old.snapshot_tags),
            ('历史标题', '历史作者', '历史简介', []))

    def test_approval_into_ended_period_captures_metadata_without_rewriting_older_snapshots(self):
        self.period.status='ended'; self.period.ended_at=timezone.now(); self.period.save()
        self.pending()
        moderation.approve(self.admin, self.work.pk, '核查晚到的提交')
        snap=self.assert_complete(self.work)
        UserWorkMetadata.objects.filter(work=self.work).update(author='后来的作者', description='后来的简介')
        activities.ensure_snapshot(self.period, self.work)
        snap.refresh_from_db()
        self.assertEqual((snap.snapshot_author, snap.snapshot_description), ('手工作者','手工简介'))
