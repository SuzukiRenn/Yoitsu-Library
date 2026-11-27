import json
from datetime import timedelta
from html import escape
from io import BytesIO
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from booklist import accounts, activities, public_booklist
from booklist.course_models import BookCandidate, BookQuestion, CuratedShelf, Nomination, ReadingRoute
from booklist.models import Activity, Admin, Tag, TagSuppression, UserWorkMetadata, Work, WorkAlias, WorkTag


class CourseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_token, owner_session = accounts.register('course_owner', '策展人', 'CourseDemo!owner92')
        cls.member_token, member_session = accounts.register('course_member', '协作者', 'CourseDemo!member92')
        cls.owner = owner_session.account
        cls.member = member_session.account
        cls.work = Work.objects.create(original_submitted_title='山间来信',
            wenku8_description='旅人在庭院学习了魔、法。随后前往北方。')
        cls.unlisted = Work.objects.create(original_submitted_title='未推荐的目录作品',
            wenku8_description='魔法和旅行。')
        cls.hidden = Work.objects.create(original_submitted_title='隐藏机密', wenku8_description='魔法。',
            hidden_at=timezone.now())
        cls.second = Work.objects.create(original_submitted_title='沿河旅行')
        public_booklist.set_relation(cls.work, cls.owner.principal, 'recommend', True)

    def browser(self, token):
        browser = Client(enforce_csrf_checks=True)
        browser.cookies[settings.ACCOUNT_COOKIE] = token
        return browser

    def post(self, client, path, **data):
        page = client.get(path)
        self.assertEqual(page.status_code, 200)
        data['csrfmiddlewaretoken'] = client.cookies[settings.CSRF_COOKIE_NAME].value
        return client.post(path, data)

    def find(self, client, path='/find-books/', *, follow=True, **data):
        client.get(path)
        data['csrfmiddlewaretoken'] = client.cookies[settings.CSRF_COOKIE_NAME].value
        return client.post(path, data, follow=follow)

    def test_search_requires_evidence_and_filters_eligibility(self):
        self.assertContains(self.client.get('/'), '/find-books/')
        response = self.find(self.client, mode='conditions', query='魔法和旅行', keywords='魔法,学院', tags='', exclude='恐怖')
        self.assertContains(response, '山间来信')
        self.assertContains(response, '旅人在庭院学习了魔、法。')
        self.assertContains(response, '未找到依据 学院')
        self.assertContains(response, '资料未提及的特征无法判断')
        self.assertContains(response, '未推荐的目录作品')
        self.assertNotContains(response, '隐藏机密')
        self.assertContains(response, '按调整后的条件查找')
        no_match = self.find(self.client, mode='conditions', query='火星', keywords='火星', tags='', exclude='')
        self.assertContains(no_match, '暂无匹配作品。', count=1)
        self.assertNotContains(no_match, 'data-course-examples')
        self.assertNotContains(no_match, 'class="course-empty"')

    @override_settings(COURSE_DEEPSEEK_KEY='fake-test-only')
    def test_catalog_search_matches_displayed_fields_and_excludes_unavailable_records(self):
        work = self.unlisted
        work.wenku8_title = '已覆盖书名'
        work.title_override = '未推荐的目录作品'
        work.title_overridden = True
        work.wenku8_description = '已覆盖简介'
        work.save(update_fields=['wenku8_title', 'title_override', 'title_overridden', 'wenku8_description'])
        UserWorkMetadata.objects.create(work=work, anonymous_client=self.owner.principal,
            author='展示作者', description='船员循着蓝色信标回到港口。这里有范围界标。')
        WorkAlias.objects.create(work=work, alias='星航Ａ·１', normalized_alias='星航a1')
        WorkAlias.objects.create(work=work, alias='过期别名', normalized_alias='过期别名', archived_at=timezone.now())
        admin = Admin.objects.create(username='course_catalog_reviewer')
        for name in ('有效航海标签', '屏蔽标签', '停用标签'):
            tag = Tag.objects.create(name=name, normalized_name=name, is_active=name != '停用标签')
            WorkTag.objects.create(work=work, tag=tag, source_type='source')
            if name == '屏蔽标签':
                TagSuppression.objects.create(work=work, tag=tag, scope='all', admin=admin, reason='测试')
        for label, attributes in (
                ('隐藏', {'hidden_at': timezone.now()}), ('合并', {'merged_into': work}),
                ('待审', {'review_status': 'pending'}), ('退役', {'source_metadata': {'manual_retired_at': '2026-11-25'}})):
            Work.objects.create(original_submitted_title=f'{label}范围界标', **attributes)

        path = '/find-books/'
        work_url = f'/works/{work.pk}/?from={path}'
        browser = Client(enforce_csrf_checks=True)
        for word, evidence in (
                ('未推荐的目录', '书名「未推荐的目录作品」'), ('星航a1', '别名「星航Ａ·１」'),
                ('展示作者', '作者「展示作者」'), ('蓝色信标', '简介片段「船员循着蓝色信标回到港口。」'),
                ('航海标签', '标签「有效航海标签」'), ('范围界标', '简介片段「这里有范围界标。」')):
            with self.subTest(word=word):
                response = self.find(browser, mode='plain', query=word)
                result_location = urlencode({'from': response.context['search_url']})
                self.assertContains(response, f'href="/works/{work.pk}/?{result_location.replace("%2F", "/")}"')
                self.assertContains(response, evidence)
                self.assertEqual([row['id'] for row in response.context['results']], [work.pk])
        for word in ('过期别名', '已覆盖书名', '已覆盖简介', '屏蔽标签', '停用标签'):
            self.assertContains(self.find(browser, mode='plain', query=word), '暂无匹配作品。', count=1)
        edited = self.find(browser, mode='conditions', query='调整检索词', keywords='范围界标', tags='', exclude='')
        self.assertEqual([row['id'] for row in edited.context['results']], [work.pk])
        excluded = self.find(browser, mode='conditions', query='调整检索词', keywords='范围界标', tags='', exclude='星航a1')
        self.assertContains(excluded, '暂无匹配作品。', count=1)
        with patch('booklist.course_find.extract', return_value={'keywords':['范围界标'], 'tags':[], 'exclude':[]}) as parse:
            result = self.find(browser, mode='ai', query='有没有相似的海上故事')
            parse.assert_called_once_with('有没有相似的海上故事')
            self.assertEqual([row['id'] for row in result.context['results']], [work.pk])
        with patch('booklist.course_find.extract', side_effect=TimeoutError):
            fallback = self.find(browser, mode='ai', query='范围界标')
            self.assertContains(fallback, '已执行普通检索')
            self.assertEqual([row['id'] for row in fallback.context['results']], [work.pk])
        self.assertContains(browser.get(work_url), '船员循着蓝色信标回到港口。')
        shelf = browser.get('/api/v1/public/works/', {'q':'未推荐的目录作品'})
        self.assertEqual(shelf.status_code, 200)
        self.assertEqual(shelf.json()['results'], [])

    @override_settings(COURSE_DEEPSEEK_KEY='fake-test-only')
    def test_mocked_provider_success_blank_timeout_invalid_and_budget(self):
        def payload(content):
            data = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
            return BytesIO(json.dumps(data).encode())
        with patch('booklist.course_find.build_opener') as opener:
            opener.return_value.open.return_value = payload(json.dumps({'keywords':['魔法'], 'tags':[], 'exclude':[]}))
            response = self.find(self.client, mode='ai', query='山间来信')
            self.assertContains(response, '简介片段')
            outbound = json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(outbound['messages'][1], {'role':'user','content':'山间来信'})
            self.assertEqual(outbound['response_format'], {'type':'json_object'})
            for content in ('   ', 'bad', '{"keywords":["非法字段"]}'):
                opener.return_value.open.return_value = payload(content)
                response = self.find(self.client, mode='ai', query='山间来信')
                self.assertContains(response, '已执行普通检索')
                self.assertContains(response, '山间来信')
            opener.return_value.open.side_effect = TimeoutError
            self.assertContains(self.find(self.client, mode='ai', query='山间来信'), '已执行普通检索')
            opener.reset_mock()
            with override_settings(COURSE_DEEPSEEK_DAILY_CALLS=1):
                self.assertContains(self.find(self.client, mode='ai', query='山间来信'), '已执行普通检索')
                opener.assert_not_called()
        with override_settings(COURSE_DEEPSEEK_KEY=''):
            self.assertContains(self.find(self.client, mode='ai', query='山间来信'), '已执行普通检索')
            fallback = self.find(self.client, mode='ai', query='火星独白')
            self.assertContains(fallback, '智能解析暂不可用，已执行普通检索。 暂无匹配作品。', count=1)
            self.assertNotContains(fallback, '调整检索条件')

    @override_settings(COURSE_DEEPSEEK_KEY='fake-test-only')
    def test_pagination_return_conditions_and_clear(self):
        works = Work.objects.bulk_create([
            Work(original_submitted_title=f'船票测试 {number:02d}') for number in range(17)
        ])
        browser = Client(enforce_csrf_checks=True)
        with patch('booklist.course_find.extract', return_value={'keywords': ['船票测试'], 'tags': [], 'exclude': []}) as extract:
            post = self.find(browser, mode='ai', query='想找有船票的故事', follow=False)
            self.assertEqual(post.status_code, 303)
            self.assertRegex(post['Location'], r'^/find-books/\?search=[0-9a-f]{32}$')
            self.assertIn('no-store', post['Cache-Control'])
            first_url = post['Location']
            page = browser.get(first_url)
            self.assertEqual(page.context['query'], '想找有船票的故事')
            pks = [row['id'] for row in page.context['results']]
            for number in (2, 3, 4):
                page = browser.get(page.context['next_url'])
                self.assertEqual(page.context['paging'].number, number)
                pks.extend(row['id'] for row in page.context['results'])
            self.assertEqual(pks, [work.pk for work in works])
            self.assertContains(page, '4 / 4')
            self.assertEqual(page.context['next_url'], '')
            last_url = page.context['search_url']
            detail_url = f'/works/{works[-1].pk}/?' + urlencode({'from': last_url})
            self.assertContains(browser.get(detail_url), f'data-shelf-return href="{escape(last_url, quote=True)}"')
            self.assertEqual(len(browser.get(last_url).context['results']), 2)
            Work.objects.filter(pk=works[-1].pk).update(hidden_at=timezone.now())
            self.assertEqual(len(browser.get(last_url).context['results']), 1)
            adjusted = self.find(browser, last_url, mode='conditions', query='想找有船票的故事',
                                 keywords='船票测试 00', tags='', exclude='')
            self.assertNotContains(adjusted, '查找结果分页')
            self.assertEqual(adjusted.context['paging'].number, 1)
            self.assertEqual([row['id'] for row in adjusted.context['results']], [works[0].pk])

            other = Client()
            self.assertRedirects(other.get(last_url), '/find-books/')
            self.assertEqual(browser.post(last_url, {'mode': 'clear'}).status_code, 403)
            clear = self.find(browser, last_url, mode='clear')
            self.assertNotContains(clear, '想找有船票的故事')
            self.assertNotContains(clear, '调整检索条件')
            self.assertContains(clear, 'data-course-examples')
            self.assertRedirects(browser.get(first_url), '/find-books/')
            self.assertRedirects(browser.get(last_url), '/find-books/')
            self.assertContains(browser.get(adjusted.context['search_url']), '船票测试 00')
            extract.assert_called_once_with('想找有船票的故事')

    def test_signed_in_search_is_private_across_accounts(self):
        owner = self.browser(self.owner_token)
        saved = self.find(owner, mode='plain', query='山间来信').context['search_url']
        owner.cookies[settings.ACCOUNT_COOKIE] = self.member_token
        self.assertRedirects(owner.get(saved), '/find-books/')
        self.find(owner, saved, mode='clear')
        owner.cookies[settings.ACCOUNT_COOKIE] = self.owner_token
        self.assertContains(owner.get(saved), '山间来信')

    @override_settings(REQUIRE_ACCOUNT=True, QQ_EMAIL_LOGIN=False)
    def test_unlisted_detail_recommendation_login_and_submission(self):
        browser = Client(enforce_csrf_checks=True)
        found = self.find(browser, mode='plain', query=self.unlisted.display_title)
        detail = f'/works/{self.unlisted.pk}/?' + urlencode({'from': found.context['search_url']})
        destination = '/recommend/?' + urlencode({'work': self.unlisted.pk, 'from': detail})
        response = browser.get(detail)
        self.assertContains(response, f'<a class="primary" href="{escape(destination, quote=True)}">推荐到公共书单</a>')
        self.assertNotContains(browser.get(f'/works/{self.work.pk}/?from=/rankings/'), '推荐到公共书单')
        guest = browser.get(destination)
        self.assertRedirects(guest, '/account/login/?' + urlencode({'next': destination}))
        browser.cookies[settings.ACCOUNT_COOKIE] = self.owner_token
        form = browser.get(destination)
        self.assertContains(form, 'data-success="' + escape(detail, quote=True) + '"')
        payload = {'work_id': self.unlisted.pk, 'title': self.unlisted.display_title,
                   'reason': '', 'confirmed_unmatched': False}
        result = browser.post('/api/v1/public/recommendations/', payload, content_type='application/json',
                              HTTP_X_CSRFTOKEN=browser.cookies[settings.CSRF_COOKIE_NAME].value,
                              HTTP_IDEMPOTENCY_KEY=str(uuid4()))
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(public_booklist.recommendations().filter(work=self.unlisted).exists())
        self.assertNotContains(browser.get(detail), '推荐到公共书单')
        self.assertContains(browser.get(found.context['search_url']), self.unlisted.display_title)

    def test_activity_public_cta_respects_current_catalog_visibility(self):
        admin = Admin.objects.create(username='course_activity_reviewer')
        now = timezone.now()
        period = Activity.objects.create(title='只推荐到活动', sync_public=False, status='active',
            created_by=admin, start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1),
            scheduled_end_at=now+timedelta(hours=1))
        activities.submit(period.pk, self.owner.principal, {'work_id': self.unlisted.pk,
            'title': self.unlisted.display_title, 'reason': '独立活动理由', 'confirmed_unmatched': False})
        path = f'/works/{self.unlisted.pk}/?activity={period.pk}'
        self.assertContains(self.client.get(path), '推荐到公共书单')
        self.assertFalse(public_booklist.recommendations().filter(work=self.unlisted).exists())
        period.status, period.ended_at = 'ended', now
        period.save(update_fields=('status', 'ended_at'))
        self.unlisted.source_metadata = {'manual_retired_at': now.isoformat()}
        self.unlisted.save(update_fields=['source_metadata'])
        self.assertNotContains(self.client.get(path), '推荐到公共书单')

    def test_curated_permissions_versions_and_immutable_snapshots(self):
        owner, member = self.browser(self.owner_token), self.browser(self.member_token)
        self.assertEqual(owner.post('/curated/', {'title':'无令牌'}).status_code, 403)
        create = self.post(owner, '/curated/', title='书信与旅行')
        self.assertEqual(create.status_code, 302)
        path = create.url
        shelf = CuratedShelf.objects.get()
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertNotContains(self.client.get("/curated/"), shelf.title)
        self.assertEqual(self.post(owner, path, action='invite', version=1, reader_id=self.member.pk, role='member').status_code, 302)
        self.assertNotContains(owner.get(path), self.member.username)
        self.assertEqual(self.post(member, path, action='nominate', version=2, work=self.work.pk, reason='第一版理由').status_code, 302)
        item = Nomination.objects.get()
        self.assertEqual(self.post(member, path, action='publish', version=3).status_code, 403)
        self.assertEqual(self.post(owner, path, action='review', version=3, item=item.pk, position=1, approved='on').status_code, 302)
        self.assertEqual(self.post(member, path, action='edit_item', version=3, item=item.pk, position=2, reason='过期理由').status_code, 409)
        self.assertEqual(self.post(owner, path, action='publish', version=4).status_code, 302)
        self.assertContains(self.client.get(path), '第一版理由')
        shelf_index = self.client.get('/curated/')
        self.assertContains(shelf_index, self.work.display_title)
        self.assertContains(shelf_index, '1 部可见作品 · 第 1 版')
        self.assertNotContains(shelf_index, '第一版理由')
        self.assertNotContains(self.client.get(path), '协作草稿')
        self.assertEqual(self.post(member, path, action='edit_item', version=5, item=item.pk, position=2, reason='第二版理由').status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.approved)
        self.assertNotContains(self.client.get(path), '第二版理由')
        self.assertEqual(self.post(owner, path, action='review', version=6, item=item.pk, position=2, approved='on').status_code, 302)
        self.assertEqual(self.post(owner, path, action='publish', version=7).status_code, 302)
        self.assertContains(self.client.get(path), '第二版理由')
        self.assertContains(self.client.get(path+'?edition=1'), '第一版理由')
        self.assertNotContains(self.client.get(path+'?edition=1'), '第二版理由')
        self.work.hidden_at = timezone.now()
        self.work.save(update_fields=['hidden_at'])
        self.assertNotContains(self.client.get(path), '第二版理由')
        shelf_index = self.client.get('/curated/')
        self.assertContains(shelf_index, '0 部可见作品 · 第 2 版')
        self.assertNotContains(shelf_index, self.work.display_title)
        self.assertEqual(shelf.editions.count(), 2)

    def test_curated_add_review_publish_workflow(self):
        owner, member = self.browser(self.owner_token), self.browser(self.member_token)
        path = self.post(owner, '/curated/', title='冬日书单').url
        shelf = CuratedShelf.objects.get()

        def action(client, **data):
            shelf.refresh_from_db()
            response = self.post(client, path, version=shelf.version, **data)
            self.assertEqual(response.status_code, 302, response.content)
            return client.get(response.url)

        empty = owner.get(path)
        self.assertContains(empty, '添加作品')
        self.assertContains(empty, 'value="publish" disabled')
        self.assertContains(empty, '<input type="hidden" name="work" data-course-work>')
        self.assertNotContains(empty, '所选作品编号')
        self.assertNotContains(member.get('/curated/'), '冬日书单')

        invited = action(owner, action='invite', display_name=self.member.display_name, role='member')
        self.assertContains(invited, '协作者已添加或更新。')
        self.assertNotContains(invited, self.member.username)
        self.assertContains(member.get('/curated/'), '成员 · 草稿')
        self.assertContains(owner.get('/curated/'), '创建者 · 草稿')

        added = action(owner, action='nominate', work=self.work.pk, reason='创建者的收录理由')
        own = shelf.nominations.get(work=self.work)
        self.assertTrue(own.approved)
        self.assertEqual((added.context['ready_count'], added.context['pending_count']), (1, 0))
        self.assertContains(added, '作品已加入草稿。')
        self.assertNotContains(added, 'value="publish" disabled')
        proposed = action(member, action='nominate', work=self.second.pk, reason='成员的提议理由')
        item = shelf.nominations.get(work=self.second)
        self.assertFalse(item.approved)
        self.assertContains(proposed, '提议已提交，等待创建者审核。')
        self.assertNotContains(proposed, '发布书单')
        self.assertNotContains(proposed, '接纳作品')
        self.assertEqual((proposed.context['ready_count'], proposed.context['pending_count']), (1, 1))

        accepted = action(owner, action='review', item=item.pk, approved='on')
        self.assertContains(accepted, '已接纳这部作品。')
        self.assertContains(accepted, '退回待审核')
        self.assertEqual((accepted.context['ready_count'], accepted.context['pending_count']), (2, 0))
        returned = action(owner, action='review', item=item.pk)
        self.assertContains(returned, '作品已退回待审核。')
        item.refresh_from_db()
        self.assertFalse(item.approved)
        self.assertEqual(item.reason, '成员的提议理由')

        published = action(owner, action='publish')
        self.assertContains(published, '新版本已发布。')
        self.assertContains(published, '可发布内容与最新公开版一致。')
        self.assertContains(published, 'value="publish" disabled')
        self.assertEqual(shelf.editions.get(number=1).entries[0]['work_id'], self.work.pk)
        self.assertNotContains(self.client.get(path), '成员的提议理由')
        self.assertNotContains(owner.get(path+'?edition=1'), '添加作品')
        self.assertContains(owner.get(path+'?edition=1'), '创建者的收录理由')

        action(owner, action='review', item=item.pk, approved='on')
        edited = action(owner, action='edit_item', item=item.pk, order=1)
        self.assertEqual([row['item'].pk for row in edited.context['nominations']], [item.pk, own.pk])
        item.refresh_from_db()
        self.assertTrue(item.approved)
        item.reason = '应被隐藏的私有理由'
        item.save(update_fields=['reason'])
        Work.objects.filter(pk=item.work_id).update(hidden_at=timezone.now())
        hidden = owner.get(path)
        self.assertContains(hidden, '请先移除不可见的已接纳作品。')
        self.assertContains(hidden, 'value="publish" disabled')
        self.assertNotContains(hidden, item.reason)
        self.assertNotContains(hidden, self.second.display_title)
        shelf.refresh_from_db()
        self.assertEqual(self.post(owner, path, action='publish', version=shelf.version).status_code, 409)
        self.assertEqual(shelf.editions.count(), 1)

    def test_curated_invite_requires_an_unambiguous_public_nickname(self):
        accounts.register('another_curator', self.member.display_name, 'CourseDemo!another42')
        owner = self.browser(self.owner_token)
        path = self.post(owner, '/curated/', title='只邀请确认的读者').url
        shelf = CuratedShelf.objects.get()
        ambiguous = self.post(owner, path, action='invite', version=1, display_name=self.member.display_name, role='member')
        self.assertContains(ambiguous, '有多位读者使用这个昵称，请按读者编号邀请。', status_code=409)
        self.assertFalse(shelf.memberships.exists())
        self.assertEqual(self.post(owner, path, action='invite', version=1, reader_id=self.member.pk, role='member').status_code, 302)
        self.assertEqual(shelf.memberships.get().account_id, self.member.pk)

    def test_work_page_routes_own_edits_spoilers_and_reference_visibility(self):
        owner, member = self.browser(self.owner_token), self.browser(self.member_token)
        work_path = f'/works/{self.work.pk}/'
        path = work_path+'reading-route/'
        self.assertContains(owner.get(work_path), path)
        self.assertIn('/account/login/', self.client.get(path).url)
        text = '先读'+self.second.display_title
        refs = json.dumps([{'work_id': self.second.pk, 'start': 2, 'end': len(text)}])
        self.assertEqual(self.post(owner, path, action='save', version=0, reason=text, references=refs, spoilers='on').status_code, 302)
        self.assertEqual(self.post(member, path, action='save', version=0, reason='我建议反过来读。').status_code, 302)
        response = self.client.get(work_path)
        self.assertContains(response, 'data-spoiler-text')
        self.assertContains(response, f'href="/works/{self.second.pk}/"')
        self.assertContains(response, '我建议反过来读。')
        self.assertEqual(self.post(member, path, action='save', version=0, reason='过期内容').status_code, 409)
        self.second.hidden_at = timezone.now()
        self.second.save(update_fields=['hidden_at'])
        self.assertNotContains(self.client.get(work_path), f'href="/works/{self.second.pk}/"')
        self.assertEqual(self.post(owner, path, action='withdraw', version=1).status_code, 302)
        self.assertNotContains(self.client.get(work_path), self.second.display_title)
        self.assertEqual(ReadingRoute.objects.filter(work=self.work, withdrawn=False).count(), 1)
        self.assertEqual(public_booklist.recommendations().filter(work=self.work).count(), 1)

    def test_help_deduplicates_and_accepts_only_by_owner(self):
        owner, member = self.browser(self.owner_token), self.browser(self.member_token)
        path = self.post(owner, '/book-help/', title='找一本奇幻小说', clues='记得山间').url
        question = BookQuestion.objects.get()
        self.assertContains(self.client.get(path), '记得山间')
        self.assertEqual(self.post(owner, path, version=1, action='add_clue', clue='关键词：庭院').status_code, 302)
        self.assertEqual(self.post(member, path, version=2, action='candidate', work=self.work.pk, reason='场景相符').status_code, 302)
        candidate = BookCandidate.objects.get()
        self.assertEqual(self.post(member, path, version=3, action='candidate', work=self.work.pk, reason='重复').status_code, 409)
        self.assertEqual(self.post(member, path, version=3, action='accept', candidate=candidate.pk).status_code, 403)
        self.assertEqual(self.post(owner, path, version=3, action='accept', candidate=candidate.pk).status_code, 302)
        self.assertContains(self.client.get('/book-help/', {'status':'solved','q':'庭院'}), question.title)
        self.assertEqual(self.post(owner, path, version=3, action='unaccept').status_code, 409)
        self.assertEqual(self.post(owner, path, version=4, action='unaccept').status_code, 302)
        self.assertNotContains(self.client.get('/book-help/', {'status':'solved'}), question.title)
        self.assertContains(self.client.get(path), '场景相符')

        question.additions.create(text='庭院的另一条线索')
        BookCandidate.objects.create(question=question, work=self.second, author=self.member,
                                     reason='已隐藏的回复', hidden=True)
        BookCandidate.objects.create(question=question, work=self.hidden, author=self.member,
                                     reason='作品不可见')
        filtered = self.client.get('/book-help/', {'status':'open', 'q':'庭院'})
        self.assertContains(filtered, question.title, count=1)
        self.assertContains(filtered, '1 条回复', count=1)
        self.assertContains(filtered, 'q=%E5%BA%AD%E9%99%A2&amp;status=solved')
        detail = self.client.get(path)
        self.assertContains(detail, '1 条回复', count=1)
        self.assertNotContains(detail, '已隐藏的回复')
        self.assertNotContains(detail, '作品不可见')

        BookQuestion.objects.filter(pk=question.pk).update(accepted=candidate)
        self.work.hidden_at = timezone.now()
        self.work.save(update_fields=['hidden_at'])
        self.assertNotContains(self.client.get('/book-help/', {'status':'solved'}), question.title)
        self.assertContains(self.client.get('/book-help/', {'status':'open'}), '0 条回复', count=1)
