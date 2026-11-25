import json
from io import BytesIO
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from booklist import accounts, public_booklist
from booklist.course_models import BookCandidate, BookQuestion, CuratedShelf, Nomination, ReadingRoute
from booklist.models import Work


class CourseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_token, owner_session = accounts.register('course_owner', '策展人', 'CourseDemo!owner92')
        cls.member_token, member_session = accounts.register('course_member', '协作者', 'CourseDemo!member92')
        cls.owner = owner_session.account
        cls.member = member_session.account
        cls.work = Work.objects.create(original_submitted_title='山间来信',
            wenku8_description='旅人在庭院学习了魔、法。随后前往北方。')
        cls.unlisted = Work.objects.create(original_submitted_title='未上架机密',
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

    def test_search_requires_evidence_and_filters_eligibility(self):
        self.assertContains(self.client.get('/'), '/find-books/')
        response = self.post(self.client, '/find-books/', mode='conditions', query='魔法和旅行', keywords='魔法,学院', tags='', exclude='恐怖')
        self.assertContains(response, '山间来信')
        self.assertContains(response, '旅人在庭院学习了魔、法。')
        self.assertContains(response, '未找到依据 学院')
        self.assertContains(response, '资料未提及的特征无法判断')
        self.assertNotContains(response, '未上架机密')
        self.assertNotContains(response, '隐藏机密')
        self.assertContains(response, '按调整后的条件查找')
        no_match = self.post(self.client, '/find-books/', mode='conditions', query='火星', keywords='火星', tags='', exclude='')
        self.assertContains(no_match, '暂无匹配作品。', count=1)
        self.assertNotContains(no_match, 'data-course-examples')
        self.assertNotContains(no_match, 'class="course-empty"')

    @override_settings(COURSE_DEEPSEEK_KEY='fake-test-only')
    def test_mocked_provider_success_blank_timeout_invalid_and_budget(self):
        def payload(content):
            data = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
            return BytesIO(json.dumps(data).encode())
        with patch('booklist.course_find.build_opener') as opener:
            opener.return_value.open.return_value = payload(json.dumps({'keywords':['魔法'], 'tags':[], 'exclude':[]}))
            response = self.post(self.client, '/find-books/', mode='ai', query='山间来信')
            self.assertContains(response, '简介片段')
            outbound = json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(outbound['messages'][1], {'role':'user','content':'山间来信'})
            self.assertEqual(outbound['response_format'], {'type':'json_object'})
            for content in ('   ', 'bad', '{"keywords":["非法字段"]}'):
                opener.return_value.open.return_value = payload(content)
                response = self.post(self.client, '/find-books/', mode='ai', query='山间来信')
                self.assertContains(response, '已执行普通检索')
                self.assertContains(response, '山间来信')
            opener.return_value.open.side_effect = TimeoutError
            self.assertContains(self.post(self.client, '/find-books/', mode='ai', query='山间来信'), '已执行普通检索')
            opener.reset_mock()
            with override_settings(COURSE_DEEPSEEK_DAILY_CALLS=1):
                self.assertContains(self.post(self.client, '/find-books/', mode='ai', query='山间来信'), '已执行普通检索')
                opener.assert_not_called()
        with override_settings(COURSE_DEEPSEEK_KEY=''):
            self.assertContains(self.post(self.client, '/find-books/', mode='ai', query='山间来信'), '已执行普通检索')
            fallback = self.post(self.client, '/find-books/', mode='ai', query='火星独白')
            self.assertContains(fallback, '智能解析暂不可用，已执行普通检索。 暂无匹配作品。', count=1)
            self.assertNotContains(fallback, '调整检索条件')

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
