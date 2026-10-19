import json
from pathlib import Path
import socket
import subprocess
from unittest.mock import patch
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase, RequestFactory, override_settings
from django.utils import timezone
from booklist import catalog, services
from booklist.models import Work, WorkAlias, Tag, WorkTag, TagSuppression, ChangeEvent, PublicRecommendation


class CatalogTests(TestCase):
    def work(self, title='魔法学园的图书管理员', **kwargs):
        return Work.objects.create(original_submitted_title=title, **kwargs)

    def search(self, query, **extra):
        return self.client.get('/api/v1/catalog/search/', {'q': query, **extra})

    def test_exact_same_name_works_remain_distinct_and_no_writes_or_network(self):
        first, second = self.work(), self.work()
        before = ChangeEvent.objects.count()
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')):
            result = self.search(first.display_title).json()
        self.assertEqual([row['id'] for row in result['results']], [first.pk, second.pk])
        self.assertEqual(result['strategy'], 'exact')
        self.assertTrue(result['requires_confirmation'])
        self.assertFalse(result['ai_used'])
        self.assertEqual(ChangeEvent.objects.count(), before)
        self.assertEqual(Work.objects.count(), 2)

    def test_alias_exact_duplicate_and_archived_alias(self):
        first, second = self.work(), self.work('另一本书')
        for work in (first, second):
            WorkAlias.objects.create(work=work, alias='图书管理员', normalized_alias='deliberately-not-indexed')
        WorkAlias.objects.create(work=first, alias='旧称', normalized_alias='旧称', archived_at=timezone.now())
        self.assertEqual(len(self.search('图书管理员').json()['results']), 2)
        self.assertEqual(self.search('旧称').json()['results'], [])

    def test_nfkc_casefold_whitespace_and_punctuation(self):
        work = self.work('ＡＢＣ：魔法 学园')
        result = self.search('abc魔法学园').json()
        self.assertEqual(result['results'][0]['id'], work.pk)
        self.assertEqual(result['strategy'], 'normalized')
        self.assertNotEqual(catalog.search_key('C++'), catalog.search_key('C'))

    def test_typo_and_missing_character_generate_candidates(self):
        work = self.work()
        for query in ('魔法学园的图书管里员', '魔法学园图书管理员'):
            with self.subTest(query=query):
                result = self.search(query).json()
                self.assertEqual(result['results'][0]['id'], work.pk)
                self.assertEqual(result['results'][0]['match_kind'], 'fuzzy')

    def test_prefix_substring_and_empty_result(self):
        work = self.work()
        self.assertEqual(self.search('魔法学园').json()['results'][0]['match_kind'], 'prefix')
        self.assertEqual(self.search('图书管理').json()['results'][0]['match_kind'], 'substring')
        self.assertEqual(self.search('完全不同的宇宙').json()['results'], [])

    def test_hidden_pending_and_merged_are_excluded_from_every_read(self):
        main = self.work('正式作品')
        rows = [self.work('秘密作品', hidden_at=timezone.now()), self.work('秘密作品', review_status='pending'), self.work('秘密作品', merged_into=main)]
        for work in rows:
            WorkAlias.objects.create(work=work, alias='秘密别名', normalized_alias='秘密别名')
            self.assertEqual(self.client.get(f'/api/v1/catalog/works/{work.pk}/').status_code, 404)
            self.assertEqual(self.client.get(f'/works/{work.pk}/').status_code, 404)
        self.assertEqual(self.search('秘密作品').json()['results'], [])
        self.assertEqual(self.search('秘密别名').json()['results'], [])
        self.assertNotContains(self.client.get('/'), '秘密作品')
        self.assertEqual(self.client.get('/api/v1/catalog/works/999999/').status_code, 404)

    def test_governance_changes_are_visible_without_cache_invalidation(self):
        work = self.work()
        self.assertTrue(self.search(work.display_title).json()['results'])
        Work.objects.filter(pk=work.pk).update(hidden_at=timezone.now())
        self.assertEqual(self.search(work.display_title).json()['results'], [])

    def test_overrides_empty_values_and_original_input_not_an_alias(self):
        work = Work.objects.create(original_submitted_title='旧输入', wenku8_book_id=8, identity_status='matched',
            wenku8_title='来源标题', wenku8_author='来源作者', wenku8_description='来源简介', wenku8_cover_url='https://example.invalid/cover.jpg',
            title_overridden=True, title_override='纠正后的标题', author_overridden=True, author_override='',
            description_overridden=True, description_override='', cover_url_overridden=True, cover_url_override='')
        result = self.client.get(f'/api/v1/catalog/works/{work.pk}/').json()
        self.assertEqual((result['title'], result['author'], result['description'], result['cover_url']), ('纠正后的标题', '', '', None))
        self.assertEqual(self.search('旧输入').json()['results'], [])
        self.assertNotIn('original_submitted_title', result)
        self.assertNotIn('title_override', result)
        self.assertNotIn('source_metadata', result)

    def test_public_membership_distinct_from_catalog_visibility(self):
        imported = self.work('未收录作品')
        public = self.work('已收录作品', public_added_at=timezone.now())
        _, person = services.issue_anonymous()
        PublicRecommendation.objects.create(work=public, anonymous_client=person)
        self.assertFalse(catalog.work_detail(imported.pk)['is_public'])
        self.assertTrue(catalog.work_detail(public.pk)['is_public'])
        self.assertEqual(len(catalog.recent_works()), 2)

    def test_tag_visibility_and_source_suppression_do_not_hide_user_origin(self):
        work = self.work()
        admin = services.initialize_admin('owner', 'Local-Catalog!Verification-678')
        _, anon = services.issue_anonymous()
        source, banned, disabled = [Tag.objects.create(name=name, normalized_name=name) for name in ('来源抑制', '作品禁止', '停用')]
        disabled.is_active=False; disabled.save()
        for tag in (source, banned, disabled):
            WorkTag.objects.create(work=work, tag=tag, source_type='source')
        WorkTag.objects.create(work=work, tag=source, source_type='user', anonymous_client=anon)
        TagSuppression.objects.create(work=work, tag=source, scope='source', admin=admin, reason='test')
        TagSuppression.objects.create(work=work, tag=banned, scope='all', admin=admin, reason='test')
        self.assertEqual([tag['name'] for tag in catalog.work_detail(work.pk)['tags']], ['来源抑制'])
        WorkTag.objects.filter(tag=source, source_type='user').update(state='user_removed')
        self.assertEqual(catalog.work_detail(work.pk)['tags'], [])

    def test_cover_policy(self):
        for value in ('http://example.invalid/x', 'javascript:alert(1)', 'https://user:secret@example.invalid/x', 'https://[bad', 'https://example.invalid/has space'):
            self.assertIsNone(catalog.cover_url(value))
        self.assertEqual(catalog.cover_url('https://example.invalid/x'), 'https://example.invalid/x')

    def test_bad_query_duplicate_unknown_and_limit_parameters(self):
        for query in ('', ' ', '！', '长'*301):
            self.assertEqual(self.search(query).status_code, 400)
        for limit in ('0', '21', '01', '-1', '1.0', 'x'):
            self.assertEqual(self.search('书', limit=limit).status_code, 400)
        self.assertEqual(self.search('书', unexpected='x').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/catalog/search/?q=a&q=b').status_code, 400)
        self.assertEqual(self.client.post('/api/v1/catalog/search/').status_code, 405)
        self.assertEqual(self.client.get('/api/v1/catalog/works/1/?unexpected=x').status_code, 400)

    def test_300_unicode_codepoints_allowed(self):
        work = self.work('📚'*300)
        self.assertEqual(self.search('📚'*300).json()['results'][0]['id'], work.pk)
        with self.assertRaises(ValidationError):
            catalog.search('\ud800')

    def test_limit_stability_and_term_capacity_fails_explicitly(self):
        ids = [self.work('同名作品').pk for _ in range(4)]
        self.assertEqual([row['id'] for row in self.search('同名作品', limit=2).json()['results']], ids[:2])
        with patch.object(catalog, 'MAX_TERMS', 2):
            self.assertEqual(self.search('没有结果').status_code, 503)

    def test_rate_limit_is_shared_by_html_and_api_search(self):
        self.work()
        with patch('booklist.services.time.time', return_value=120):
            for _ in range(60):
                self.assertEqual(self.search('图书').status_code, 200)
            response = self.client.get('/catalog/', {'q': '图书'})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response['Retry-After'], '60')

    def test_html_escaping_ssr_fallback_and_csp(self):
        title='<img src=x onerror=alert(1)>'
        work=self.work(title, wenku8_description='<script>alert(1)</script>\n简介')
        home=self.client.get('/catalog/', {'q': title})
        self.assertContains(home, '&lt;img')
        self.assertNotContains(home, '<img src=x')
        self.assertContains(home, f'/works/{work.pk}/')
        self.assertIn("script-src 'self'", home['Content-Security-Policy'])
        detail=self.client.get(f'/works/{work.pk}/')
        self.assertContains(detail, '&lt;script&gt;')
        self.assertNotContains(detail, '<script>alert(1)')

    def test_page_copy_is_limited_to_functional_labels(self):
        self.work()
        page=self.client.get('/')
        for text in ('好故事', '故事的入口', '从一个书名', '把好故事留在一起', 'THE SHARED SHELF', '找到相似书名', '同名作品可能'):
            self.assertNotContains(page, text)
        self.assertNotContains(page, 'id="recommend-form"')
        self.assertContains(page, '>推荐</a>')
        self.assertNotContains(page, 'id="search-form"')

    def test_javascript_request_race_and_error_suite(self):
        result = subprocess.run(['node', str(Path(settings.BASE_DIR)/'scripts/test_catalog_ui.mjs')], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

class StaticAssetTests(SimpleTestCase):
    @override_settings(DEBUG=True)
    def test_static_modules_are_served_with_javascript_content_type(self):
        from django.contrib.staticfiles.views import serve
        for name in ('catalog.mjs', 'catalog-search.mjs', 'text.mjs'):
            response = serve(RequestFactory().get('/static/'+name), name)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertIn(response['Content-Type'].split(';')[0], ('text/javascript', 'application/javascript'))
            finally:
                response.close()
