import json
from django.conf import settings
from django.test import TestCase, Client
from django.utils import timezone
from booklist import catalog, services, source_review, user_metadata, moderation
from booklist.models import Work, WorkAlias, SourceIdentityReview, PublicRecommendation, Recommendation, AdminAuditLog
from .test_importing import snapshot


class SourceReviewTests(TestCase):
    def setUp(self):
        self.actor = services.initialize_admin('owner', 'Source-Identity!Testing-Password-49')
        _, self.person = services.issue_anonymous()
        self.snapshot = snapshot()
        self.manual = Work.objects.create(original_submitted_title=self.snapshot['fields']['title']['value'], public_added_at=timezone.now())
        user_metadata.initialize(self.manual, self.person, {'description': '用户填写的简介', 'tags': ['校园']})
        PublicRecommendation.objects.create(work=self.manual, anonymous_client=self.person)
        Recommendation.objects.create(work=self.manual, anonymous_client=self.person, scope='public', reason='保留的推荐理由')

    def pending(self):
        source, _, _ = source_review.import_with_review(self.snapshot)
        return source, SourceIdentityReview.objects.get(source_work=source)

    def test_possible_duplicate_is_held_and_unrelated_import_continues(self):
        source, review = self.pending()
        self.manual.refresh_from_db()
        self.assertIsNone(self.manual.wenku8_book_id)
        self.assertEqual(source.review_status, 'pending')
        self.assertFalse(catalog.visible_works().filter(pk=source.pk).exists())
        self.assertEqual(review.candidates[0]['work_id'], self.manual.pk)
        other, _, _ = source_review.import_with_review(snapshot(2951))
        self.assertEqual(other.review_status, 'clear')
        self.assertEqual(SourceIdentityReview.objects.count(), 1)
        source_review.import_with_review(self.snapshot)
        self.assertEqual(SourceIdentityReview.objects.count(), 1)
        with self.assertRaises(services.DomainError): moderation.approve(self.actor, source.pk, '不可绕过身份核对')

    def test_link_preserves_original_id_contributions_and_user_content(self):
        source, review = self.pending()
        target = source_review.resolve(self.actor, review.pk, 'link', self.manual.pk, review.snapshot_hash, '核实书名作者及简介一致')
        self.assertEqual(target.pk, self.manual.pk)
        self.manual.refresh_from_db(); source.refresh_from_db(); review.refresh_from_db()
        self.assertEqual(self.manual.wenku8_book_id, self.snapshot['book_id'])
        self.assertEqual(source.merged_into_id, self.manual.pk)
        self.assertIsNone(source.wenku8_book_id)
        self.assertEqual(review.status, 'linked')
        self.assertEqual(Recommendation.objects.get().reason, '保留的推荐理由')
        self.assertEqual(PublicRecommendation.objects.get().work_id, self.manual.pk)
        details = catalog.work_detail(self.manual.pk)
        self.assertEqual(details['description'], '用户填写的简介')
        self.assertIn('校园', [t['name'] for t in details['tags']])
        self.assertTrue(self.manual.wenku8_cover_url)
        self.assertTrue(AdminAuditLog.objects.filter(action='source.identity.link').exists())
        with self.assertRaises(services.DomainError):
            source_review.resolve(self.actor, review.pk, 'link', self.manual.pk, review.snapshot_hash, '重复确认')

    def test_separate_releases_source_without_altering_original(self):
        source, review = self.pending()
        source_review.resolve(self.actor, review.pk, 'separate', 0, review.snapshot_hash, '作者与作品内容不同')
        source.refresh_from_db(); self.manual.refresh_from_db()
        self.assertEqual(source.review_status, 'clear')
        self.assertIsNone(self.manual.wenku8_book_id)
        self.assertEqual(catalog.search(self.manual.display_title)['strategy'], 'exact')

    def test_similar_name_only_creates_review_and_never_links_automatically(self):
        self.manual.original_submitted_title += '新版'; self.manual.save()
        source, review = self.pending()
        self.assertEqual(review.candidates[0]['match'], '名称相近')
        self.assertIsNone(self.manual.wenku8_book_id)
        self.assertEqual(source.review_status, 'pending')

    def test_alias_similarity_and_stale_target_are_not_identity_assertions(self):
        self.manual.original_submitted_title = '手工别名'; self.manual.save()
        WorkAlias.objects.create(work=self.manual, alias=self.snapshot['fields']['title']['value'], normalized_alias='unused')
        source, review = self.pending()
        self.manual.hidden_at = timezone.now(); self.manual.save()
        with self.assertRaises(services.DomainError):
            source_review.resolve(self.actor, review.pk, 'link', self.manual.pk, review.snapshot_hash, '陈旧目标')
        with self.assertRaises(services.DomainError):
            source_review.resolve(self.actor, review.pk, 'separate', 0, 'invalid', '陈旧快照')
        review.refresh_from_db(); self.assertEqual(review.status, 'pending')

    def test_admin_interface_is_authenticated_and_csrf_protected(self):
        source, review = self.pending()
        path = f'/manage/source-reviews/{review.pk}/'
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(browser.get(path).status_code, 302)
        api = f'/api/v1/admin/source-reviews/{review.pk}/resolve/'
        payload = {'action': 'separate', 'work_id': 0, 'snapshot_hash': review.snapshot_hash, 'reason': '确认为不同作品'}
        self.assertEqual(browser.post(api, json.dumps(payload), content_type='application/json').status_code, 403)
        token, _ = services.login('owner', 'Source-Identity!Testing-Password-49')
        browser.cookies[settings.ADMIN_COOKIE] = token
        self.assertContains(browser.get('/manage/?section=source_reviews'), '来源待核对')
        self.assertContains(browser.get(path), '确认同一本，关联资料')
        browser.get('/api/v1/csrf/')
        response = browser.post(api, json.dumps(payload), content_type='application/json', HTTP_X_CSRFTOKEN=browser.cookies[settings.CSRF_COOKIE_NAME].value)
        self.assertEqual(response.status_code, 200, response.content)
