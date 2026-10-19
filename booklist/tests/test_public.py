import importlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.conf import settings
from django.db import IntegrityError, connection, connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from unittest.mock import patch
from booklist import services, public_booklist as books
from booklist.models import Work, WorkAlias, PublicRecommendation, PublicLike, Recommendation, Vote, ChangeEvent, Tag, WorkTag, TagSuppression


class PublicTests(TestCase):
    def setUp(self):
        self.person = self.actor(self.client)
        self.work = Work.objects.create(original_submitted_title='测试作品', public_added_at=timezone.now())

    def actor(self, browser):
        token, person = services.issue_anonymous()
        browser.cookies[settings.ANONYMOUS_COOKIE] = token
        return person

    def post(self, path, data, browser=None, key=None):
        return (browser or self.client).post(path, json.dumps(data), content_type='application/json', HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def submit(self, work_id=None, title='新作品', reason='', confirmed_unmatched=False, **kwargs):
        return self.post('/api/v1/public/recommendations/', dict(work_id=work_id, title=title, reason=reason, confirmed_unmatched=confirmed_unmatched), **kwargs)

    def action(self, kind, enabled=True, **kwargs):
        return self.post(f'/api/v1/public/works/{self.work.pk}/{kind}/', {'enabled': enabled}, **kwargs)

    def current(self):
        return books.serialize([self.work], self.person)[0]

    def test_first_empty_reason_is_one_recommender_and_no_like_or_empty_reason(self):
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        row = response.json()['work']
        self.assertEqual((row['recommendation_count'], row['like_count'], row['own_reason']), (1, 0, ''))
        self.assertEqual(Recommendation.objects.count(), 0)
        self.assertEqual(Work.objects.count(), 2)
        self.assertTrue(row['recommended'])

    def test_other_person_plus_one_same_work_and_same_person_deduplicated(self):
        self.submit(work_id=self.work.pk)
        other = Client(); self.actor(other)
        for _ in range(2):
            self.assertEqual(self.action('recommend', browser=other).status_code, 200)
        self.assertEqual((self.current()['recommendation_count'], Work.objects.count()), (2, 1))
        self.assertEqual(PublicLike.objects.count(), 0)

    def test_likes_and_recommendation_and_reason_lifecycles_are_independent(self):
        self.action('like')
        self.assertEqual((self.current()['recommendation_count'], self.current()['like_count']), (0, 1))
        self.submit(work_id=self.work.pk, reason='推荐理由')
        self.action('recommend', False)
        self.assertEqual((self.current()['recommendation_count'], self.current()['like_count'], self.current()['own_reason']), (0, 1, '推荐理由'))
        self.action('like', False)
        self.work.refresh_from_db()
        self.assertIsNotNone(self.work.public_added_at)
        self.assertEqual(self.current()['like_count'], 0)
        self.action('recommend')
        response = self.post(f'/api/v1/public/works/{self.work.pk}/reason/', {'reason': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.current()['recommendation_count'], 1)
        self.assertFalse(Recommendation.objects.exists())

    def test_idempotency_replay_conflict_and_missing_key(self):
        key = str(uuid.uuid4())
        first, repeat = self.submit(key=key), self.submit(key=key)
        self.assertEqual(first.json(), repeat.json())
        self.assertEqual(self.submit(key=key, title='不同').status_code, 409)
        self.assertEqual(self.action('like', key='short').status_code, 400)
        self.assertEqual(PublicRecommendation.objects.count(), 1)

    def test_exact_title_or_alias_cannot_be_overridden_by_legacy_flag(self):
        self.assertEqual(self.submit(title=self.work.display_title).status_code, 409)
        WorkAlias.objects.create(work=self.work, alias='别名', normalized_alias='别名')
        self.assertEqual(self.submit(title='别名').status_code, 409)
        self.assertEqual(self.submit(title=self.work.display_title, confirmed_unmatched=True).status_code, 409)
        self.assertEqual(self.submit(title='别名', confirmed_unmatched=True).status_code, 409)
        self.assertEqual(Work.objects.count(), 1)

    def test_normalized_and_partial_candidates_cannot_create_duplicate_without_selection(self):
        self.work.title_overridden=True; self.work.title_override='ＡＢＣ：魔法 学园';self.work.save()
        for title in ('abc魔法学园','魔法 学园'):
            self.assertEqual(self.submit(title=title,confirmed_unmatched=True).status_code,409)
        self.assertEqual(Work.objects.count(),1)
        self.assertEqual(self.submit(work_id=self.work.pk,title='abc魔法学园').status_code,200)
        self.assertEqual(Work.objects.count(),1)

    def test_recommend_page_has_no_duplicate_creation_override(self):
        page=self.client.get('/recommend/')
        self.assertNotContains(page,'distinct-work')
        self.assertNotContains(page,'作为新作品提交')

    def test_search_failure_does_not_become_permission_to_create(self):
        with patch('booklist.catalog.search',side_effect=services.DomainError('search_capacity','检索暂不可用',503)):
            self.assertEqual(self.submit(title='未确认作品').status_code,503)
        self.assertEqual(Work.objects.count(),1)
        self.assertFalse(PublicRecommendation.objects.exists())

    def test_unknown_same_title_hidden_or_pending_is_not_published(self):
        for state in ('hidden', 'pending'):
            Work.objects.create(original_submitted_title=state, **({'hidden_at': timezone.now()} if state == 'hidden' else {'review_status': 'pending'}))
            response = self.submit(title=state, confirmed_unmatched=True, reason='待核查理由')
            self.assertEqual(response.json(), {'status': 'pending'})
            new = Work.objects.filter(original_submitted_title=state).latest('id')
            self.assertEqual(new.review_status, 'pending')
            self.assertIsNone(new.public_added_at)
            self.assertNotIn(new.pk, [row['id'] for row in books.listing()['results']])

    def test_hidden_merged_and_non_public_ids_cannot_bypass_visibility(self):
        for extra in ({'hidden_at': timezone.now()}, {'review_status': 'pending'}, {'merged_into': self.work}):
            work = Work.objects.create(original_submitted_title='不可写', **extra)
            self.assertEqual(self.submit(work_id=work.pk).status_code, 404)
        imported = Work.objects.create(original_submitted_title='仅目录')
        self.assertEqual(self.post(f'/api/v1/public/works/{imported.pk}/like/', {'enabled': True}).status_code, 404)
        self.assertEqual(self.submit(work_id=imported.pk).json()['work']['recommendation_count'], 1)

    def test_governance_rechecked_and_reason_edit_does_not_unhide(self):
        self.submit(work_id=self.work.pk, reason='原文')
        Recommendation.objects.update(text_hidden_at=timezone.now())
        path = f'/api/v1/public/works/{self.work.pk}/reason/'
        for text in ('修改', '', '重新填写'):
            self.assertEqual(self.post(path, {'reason': text}).status_code, 200)
            self.assertEqual(self.current()['reasons'], [])
            self.assertEqual(self.current()['recommendation_count'], 1)
        self.assertTrue(Recommendation.objects.filter(reason='原文').exists())
        self.assertTrue(Recommendation.objects.filter(archived_at__isnull=False, text_hidden_at__isnull=False).exists())
        Work.objects.filter(pk=self.work.pk).update(hidden_at=timezone.now())
        self.assertEqual(self.action('like').status_code, 404)
        self.assertEqual(self.post(path, {'reason': '绕过'}).status_code, 404)

    def test_invalidated_relationship_cannot_be_reactivated_by_user(self):
        self.action('recommend'); PublicRecommendation.objects.update(invalidated_at=timezone.now())
        self.assertEqual(self.action('recommend').status_code, 409)
        self.assertEqual(self.current()['recommendation_count'], 0)

    def test_identity_reset_preserves_contributions_and_does_not_inherit_controls(self):
        self.submit(work_id=self.work.pk, reason='旧身份')
        self.action('like')
        self.assertEqual(self.client.post('/api/v1/identity/reset/', '{}', content_type='application/json').status_code, 200)
        row = self.client.get('/api/v1/public/works/').json()['results'][0]
        self.assertEqual((row['recommended'], row['liked'], row['own_reason']), (False, False, ''))
        self.assertEqual((row['recommendation_count'], row['like_count']), (1, 1))
        self.assertEqual(self.action('recommend').status_code, 200)
        self.assertEqual(self.current()['recommendation_count'], 2)

    def test_auth_csrf_origin_strict_types_and_unknown_actor_field(self):
        self.assertEqual(self.action('recommend', browser=Client()).status_code, 401)
        secure = Client(enforce_csrf_checks=True); self.actor(secure)
        self.assertEqual(self.action('recommend', browser=secure).status_code, 403)
        token = secure.get('/api/v1/csrf/').json()['csrf_token']
        path = f'/api/v1/public/works/{self.work.pk}/like/'
        headers = {'HTTP_X_CSRFTOKEN': token, 'HTTP_IDEMPOTENCY_KEY': str(uuid.uuid4())}
        self.assertEqual(secure.post(path, '{"enabled":true}', content_type='application/json', HTTP_ORIGIN='https://evil.invalid', **headers).status_code, 403)
        self.assertEqual(secure.post(path, '{"enabled":true}', content_type='application/json', **headers).status_code, 200)
        for data in ({'enabled': 1}, {'enabled': 'true'}, {'enabled': True, 'anonymous_client': self.person.pk}):
            self.assertEqual(self.post(path, data).status_code, 400)
        self.assertEqual(self.submit(work_id=True).status_code, 400)

    def test_codepoint_normalization_and_reason_limit(self):
        row = self.submit(title='📚'*300, reason=' \r\n'+'📚'*200+'\r\n ').json()['work']
        self.assertEqual(row['own_reason'], '📚'*200)
        self.assertEqual(self.submit(title='长'*301).status_code, 400)
        self.assertEqual(self.submit(reason='📚'*201).status_code, 400)

    def test_write_rate_limit_and_read_parameters(self):
        with patch('booklist.services.time.time', return_value=120):
            for _ in range(60):
                self.assertEqual(self.action('like').status_code, 200)
            response = self.action('like')
            self.assertEqual((response.status_code, response['Retry-After']), (429, '60'))
        for query in ('page=0', 'page=01', 'page_size=101', 'tag=-1', 'tag=01', 'page=1&page=2', 'unknown=x'):
            self.assertEqual(self.client.get('/api/v1/public/works/?'+query).status_code, 400)

    def test_ip_write_limit_is_shared_across_rotating_identities(self):
        second, third = Client(), Client()
        self.actor(second); self.actor(third)
        with patch('booklist.services.time.time', return_value=120):
            for browser in (self.client, second):
                for _ in range(60):
                    self.assertEqual(self.action('like', browser=browser).status_code, 200)
            self.assertEqual(self.action('like', browser=third).status_code, 429)

    def test_public_sort_pagination_and_no_internal_actor_ids(self):
        self.action('recommend')
        newer = Work.objects.create(original_submitted_title='<script>作品</script>', public_added_at=timezone.now())
        self.submit(work_id=newer.pk)
        other = Client(); self.actor(other); self.action('recommend', browser=other)
        self.assertEqual(books.listing(page_size=1)['results'][0]['id'], self.work.pk)
        self.assertEqual(books.listing(page=2, page_size=1)['results'][0]['id'], newer.pk)
        response = self.client.get('/')
        self.assertContains(response, '&lt;script&gt;作品&lt;/script&gt;')
        self.assertNotContains(response, '<script>作品</script>')
        payload = self.client.get('/api/v1/public/works/').content.decode()
        for field in ('anonymous_client', 'token_hash', 'invalidated_at', 'hidden_at'):
            self.assertNotIn(field, payload)

    def test_tags_and_governance_counts_only_public_visible_formal_works(self):
        self.submit(work_id=self.work.pk)
        a, b = [Tag.objects.create(name=x, normalized_name=x) for x in ('甲', '乙')]
        _, other = services.issue_anonymous()
        imported = Work.objects.create(original_submitted_title='未收录')
        hidden = Work.objects.create(original_submitted_title='隐藏', public_added_at=timezone.now(), hidden_at=timezone.now())
        for work, tag in ((self.work, a), (self.work, b), (imported, b), (hidden, b)):
            WorkTag.objects.create(work=work, tag=tag, source_type='source')
        WorkTag.objects.create(work=self.work, tag=a, source_type='user', anonymous_client=other)
        admin = services.initialize_admin('owner', 'Public-Booklist!Stable-Password-42')
        TagSuppression.objects.create(work=self.work, tag=a, scope='source', admin=admin, reason='test')
        result = books.listing(tags=[str(a.pk), str(b.pk)])
        self.assertEqual(len(result['results']), 1)
        self.assertEqual([row['count'] for row in result['tags']], [1, 1])
        TagSuppression.objects.create(work=self.work, tag=a, scope='all', admin=admin, reason='test')
        self.assertEqual(books.listing(tags=[str(a.pk)])['total'], 0)
        b.is_active=False; b.save()
        self.assertEqual(books.listing()['tags'], [])

    def test_zero_contributions_leave_list_but_keep_catalog_reason_and_original_record(self):
        self.submit(work_id=self.work.pk, reason='保留理由')
        self.work.refresh_from_db(); first_added = self.work.public_added_at
        self.assertEqual(books.listing()['total'], 1)
        self.assertEqual(self.action('recommend', False).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/public/works/').json()['total'], 0)
        self.assertNotContains(self.client.get('/'), self.work.display_title)
        self.assertEqual(Work.objects.count(), 1)
        self.assertEqual(Recommendation.objects.get().reason, '保留理由')
        catalog = self.client.get(f'/api/v1/catalog/works/{self.work.pk}/').json()
        self.assertFalse(catalog['is_public'])
        candidate = self.client.get('/api/v1/recommend/candidates/', {'q': self.work.display_title}).json()['results'][0]
        self.assertEqual((candidate['id'], candidate['in_scope'], candidate['recommendation_count']), (self.work.pk, False, 0))
        self.assertEqual(self.submit(title=self.work.display_title).status_code, 409)
        self.assertEqual(self.submit(work_id=self.work.pk).status_code, 200)
        self.assertEqual(Work.objects.count(), 1)
        self.work.refresh_from_db(); self.assertEqual(self.work.public_added_at, first_added)
        self.assertEqual((books.listing()['total'], self.current()['recommendation_count'], self.current()['like_count']), (1, 1, 0))
        self.assertEqual(self.current()['own_reason'], '保留理由')

    def test_either_valid_recommendation_or_like_keeps_work_listed(self):
        self.submit(work_id=self.work.pk)
        self.action('like')
        self.action('recommend', False)
        self.assertEqual((books.listing()['total'], self.current()['recommendation_count'], self.current()['like_count']), (1, 0, 1))
        self.assertTrue(self.client.get(f'/api/v1/catalog/works/{self.work.pk}/').json()['is_public'])
        self.action('like', False)
        self.assertEqual(books.listing()['total'], 0)
        self.submit(work_id=self.work.pk)
        self.action('like'); self.action('like', False)
        self.assertEqual((books.listing()['total'], self.current()['recommendation_count'], self.current()['like_count']), (1, 1, 0))

    def test_withdrawn_cancelled_invalidated_and_archived_history_does_not_keep_list_membership(self):
        for model, fields in ((PublicRecommendation, ('withdrawn_at', 'invalidated_at', 'archived_at')),
                              (PublicLike, ('cancelled_at', 'invalidated_at', 'archived_at'))):
            for field in fields:
                with self.subTest(model=model.__name__, field=field):
                    record = model.objects.create(work=self.work, anonymous_client=self.person)
                    self.assertEqual(books.listing()['total'], 1)
                    setattr(record, field, timezone.now()); record.save(update_fields=[field])
                    self.assertEqual(books.listing()['total'], 0)

    def test_zero_contribution_works_are_excluded_from_pagination_and_tag_counts(self):
        tag = Tag.objects.create(name='仅零贡献', normalized_name='仅零贡献')
        WorkTag.objects.create(work=self.work, tag=tag, source_type='source')
        self.submit(work_id=self.work.pk)
        other = Work.objects.create(original_submitted_title='保留作品')
        self.submit(work_id=other.pk)
        self.action('recommend', False)
        result = books.listing(page_size=1)
        self.assertEqual((result['total'], [row['id'] for row in result['results']], result['tags']), (1, [other.pk], []))
        self.assertEqual(books.listing(tags=[str(tag.pk)])['total'], 0)
        self.assertEqual(books.listing(page=2, page_size=1)['results'], [])

    def test_reasons_bounded_and_paginated_hidden_does_not_reduce_recommendations(self):
        for index in range(4):
            browser = Client(); self.actor(browser)
            self.submit(work_id=self.work.pk, reason=f'理由{index}', browser=browser)
        Recommendation.objects.filter(reason='理由1').update(text_hidden_at=timezone.now())
        row = self.current()
        self.assertEqual((row['recommendation_count'], row['reason_count'], len(row['reasons'])), (4, 3, 2))
        response = self.client.get(f'/api/v1/public/works/{self.work.pk}/reasons/?page=2&page_size=2')
        self.assertEqual(response.json(), {'total': 3, 'results': [{'reason': '理由3'}]})

    def test_new_table_uniqueness_and_raw_sql_tracking(self):
        for model in (PublicRecommendation, PublicLike):
            row = model.objects.create(work=self.work, anonymous_client=self.person)
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(work=self.work, anonymous_client=self.person)
            with connection.cursor() as cursor:
                cursor.execute(f'UPDATE {model._meta.db_table} SET updated_at=updated_at WHERE id=%s', [row.pk])
            self.assertTrue(ChangeEvent.objects.filter(table_name=model._meta.db_table, record_id=row.pk, action='update').exists())

    def test_legacy_data_union_not_likes(self):
        Vote.objects.create(work=self.work, anonymous_client=self.person, scope='public')
        Recommendation.objects.create(work=self.work, anonymous_client=self.person, scope='public', reason='旧理由')
        _, other = services.issue_anonymous()
        Vote.objects.create(work=self.work, anonymous_client=other, scope='public')
        migration = importlib.import_module('booklist.migrations.0005_public_recommendations_and_likes')
        from django.apps import apps
        from types import SimpleNamespace
        migration.migrate_recommenders(apps, SimpleNamespace(connection=connection))
        self.assertEqual(PublicRecommendation.objects.count(), 2)
        self.assertEqual(PublicLike.objects.count(), 0)
        self.assertEqual(Vote.objects.count(), 2)


class PublicConcurrentTests(TransactionTestCase):
    # Reuse the foundation's isolated-trigger teardown for append-only databases.
    def _fixture_teardown(self):
        with connection.cursor() as cursor:
            triggers = cursor.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            for name, _ in triggers:
                cursor.execute('DROP TRIGGER "'+name.replace('"', '""')+'"')
        try:
            super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for _, sql in triggers:
                    cursor.execute(sql)

    def test_concurrent_same_title_submission_requires_second_person_to_select(self):
        from booklist.models import AnonymousClient
        people = [services.issue_anonymous()[1].pk for _ in range(2)]
        barrier = Barrier(2)
        def run(person_id):
            connections.close_all(); barrier.wait(timeout=10)
            try:
                person = AnonymousClient.objects.get(pk=person_id)
                data = {'work_id': None, 'title': 'Concurrent title', 'reason': '', 'confirmed_unmatched': False}
                return services.idempotent(person, 'public.submit', str(uuid.uuid4()), data, lambda: books.submit(person, data))['status']
            except services.DomainError as exc:
                return exc.code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(run, people)), ['existing_candidate', 'saved'])
        self.assertEqual(Work.objects.count(), 1)
        self.assertEqual(PublicRecommendation.objects.count(), 1)

    def test_public_same_actor_and_different_actor_concurrent_recommendations(self):
        work = Work.objects.create(original_submitted_title='并发', public_added_at=timezone.now())
        _, first = services.issue_anonymous(); _, second = services.issue_anonymous()
        for people in ((first.pk, first.pk), (first.pk, second.pk)):
            barrier = Barrier(2)
            def run(person_id):
                connections.close_all(); barrier.wait(timeout=10)
                try:
                    from booklist.models import AnonymousClient
                    person = AnonymousClient.objects.get(pk=person_id)
                    return services.idempotent(person, 'concurrent', str(uuid.uuid4()), {'work': work.pk},
                        lambda: (books.set_relation(Work.objects.get(pk=work.pk), person, 'recommend', True) or {'ok': True}))
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(run, people)), [{'ok': True}, {'ok': True}])
        self.assertEqual(books.recommendations().filter(work=work).count(), 2)
