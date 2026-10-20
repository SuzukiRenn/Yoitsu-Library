import json
import uuid
from datetime import timedelta
from django.conf import settings
from django.test import TestCase, Client
from django.utils import timezone
from booklist import services, catalog, public_booklist, user_metadata, merging
from booklist.models import Work, Tag, WorkTag, UserWorkMetadata, PublicRecommendation, Activity, TagSuppression


class UserMetadataTests(TestCase):
    def setUp(self):
        token, self.person = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE] = token

    def post(self, path, data, client=None, key=None):
        return (client or self.client).post(path, json.dumps(data), content_type='application/json',
            HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def submit(self, metadata=None, **extra):
        data = {'work_id': None, 'title': '独立星际航行笔记', 'reason': '', 'confirmed_unmatched': False, **extra}
        if metadata is not None:
            data['metadata'] = metadata
        return self.post('/api/v1/public/recommendations/', data)

    def new_work(self):
        response = self.submit({'description': '星际旅行的故事', 'tags': ['奇幻', '校园', '奇幻']})
        self.assertEqual(response.status_code, 200, response.content)
        return Work.objects.get(pk=response.json()['work']['id'])

    def update(self, work, **extra):
        return self.post(f'/api/v1/catalog/works/{work.pk}/metadata/',
            {'description': '更新后的简介', 'tags': ['校园'], 'revision': 1, **extra})

    def test_unmatched_submission_tags_description_and_filter_visible(self):
        source_tag = Tag.objects.create(name='奇幻', normalized_name='奇幻')
        work = self.new_work()
        self.assertEqual(catalog.work_detail(work.pk)['description'], '星际旅行的故事')
        self.assertEqual({t['name'] for t in catalog.work_detail(work.pk)['tags']}, {'奇幻', '校园'})
        self.assertEqual(WorkTag.objects.filter(work=work, tag=source_tag, source_type='user').count(), 1)
        self.assertEqual(public_booklist.listing(tags=[str(source_tag.pk)])['total'], 1)
        self.assertIsNone(work.wenku8_description)
        self.assertFalse(work.description_overridden)
        self.assertContains(self.client.get(f'/works/{work.pk}/'), '编辑标签与简介')

    def test_tag_suggestions_reuse_case_and_width_variants_without_new_vocabulary(self):
        tag = Tag.objects.create(name='ＳＦ', normalized_name='ＳＦ')
        Tag.objects.create(name='sf', normalized_name='sf')
        Tag.objects.create(name='SF失效', normalized_name='sf失效', is_active=False)
        merged = Tag.objects.create(name='SF旧名', normalized_name='sf旧名', merged_into=tag)
        response = self.client.get('/api/v1/catalog/tags/', {'q': 'sf'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'], [{'id': tag.pk, 'name': 'ＳＦ'}])
        count = Tag.objects.count()
        result = self.submit({'description': '', 'tags': ['sf', 'ＳＦ']})
        self.assertEqual(result.status_code, 200, result.content)
        work = Work.objects.get(pk=result.json()['work']['id'])
        self.assertEqual(list(WorkTag.objects.filter(work=work).values_list('tag_id', flat=True)), [tag.pk])
        self.assertEqual(Tag.objects.count(), count)

    def test_tag_suggestions_rank_match_and_limit_without_creating_tags(self):
        for index in range(12): Tag.objects.create(name=f'奇幻{index:02d}', normalized_name=f'奇幻{index:02d}')
        exact = Tag.objects.create(name='奇幻', normalized_name='奇幻')
        result = self.client.get('/api/v1/catalog/tags/', {'q': '奇幻'}).json()['results']
        self.assertEqual(result[0], {'id': exact.pk, 'name': '奇幻'})
        self.assertEqual(len(result), 8)
        self.assertEqual(Tag.objects.count(), 13)
        for query in ({'q': 'x'*31}, {'unknown': 'x'}, {'q': ['a', 'b']}, {'work': '01'}):
            self.assertEqual(self.client.get('/api/v1/catalog/tags/', query).status_code, 400)

    def test_edit_tag_suggestions_exclude_moderation_and_require_owner(self):
        work = self.new_work()
        admin = services.initialize_admin('owner', 'Tag-Suggestions!Testing-Password-49')
        tag = Tag.objects.get(name='奇幻')
        TagSuppression.objects.create(work=work, tag=tag, admin=admin, scope='all', reason='测试屏蔽')
        result = self.client.get('/api/v1/catalog/tags/', {'q': '奇', 'work': work.pk})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['results'], [])
        self.assertEqual(Client().get('/api/v1/catalog/tags/', {'q': '奇', 'work': work.pk}).status_code, 403)
        self.assertContains(self.client.get(f'/works/{work.pk}/edit/'), f'data-work="{work.pk}"')

    def test_tag_suggestions_pagination_excludes_before_counting(self):
        for index in range(19):
            Tag.objects.create(name=f'SF{index:02d}', normalized_name=f'sf{index:02d}')
        query = {'q': 'sf', 'exclude': json.dumps(['ＳＦ００', 'sf01'])}
        pages = [self.client.get('/api/v1/catalog/tags/', {**query, 'page': page}).json()
                 for page in (1, 2, 3)]
        self.assertEqual([len(page['results']) for page in pages], [8, 8, 1])
        self.assertEqual([page['page'] for page in pages], [1, 2, 3])
        for page in pages:
            self.assertEqual((page['total'], page['pages'], page['page_size']), (17, 3, 8))
        names = [tag['name'] for page in pages for tag in page['results']]
        self.assertEqual(len(set(names)), 17)
        self.assertNotIn('SF00', names)
        self.assertNotIn('SF01', names)
        last = self.client.get('/api/v1/catalog/tags/', {**query, 'page': 99}).json()
        self.assertEqual(last, pages[-1])
        empty = self.client.get('/api/v1/catalog/tags/', {'q': '不存在', 'page': 2}).json()
        self.assertEqual((empty['results'], empty['total'], empty['page'], empty['pages']), ([], 0, 1, 1))
        for invalid in ({'page': 0}, {'page': '01'}, {'page': ['1', '2']},
                        {'exclude': '{'}, {'exclude': '"sf"'}, {'exclude': '[1]'},
                        {'exclude': json.dumps(['x']*21)}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.get('/api/v1/catalog/tags/', invalid).status_code, 400)

    def test_owner_can_edit_remove_and_clear_with_stale_revision_protection(self):
        work = self.new_work()
        self.assertEqual(self.update(work).status_code, 200)
        self.assertEqual(self.update(work).status_code, 409)
        self.assertEqual(catalog.work_detail(work.pk)['description'], '更新后的简介')
        self.assertEqual(WorkTag.objects.filter(work=work, state='user_removed').count(), 1)
        self.assertEqual(self.update(work, revision=2, description='', tags=[]).status_code, 200)
        self.assertEqual(catalog.work_detail(work.pk)['description'], '')
        self.assertEqual(catalog.work_detail(work.pk)['tags'], [])

    def test_other_identity_and_reset_cannot_take_ownership(self):
        work = self.new_work()
        other = Client(); token, _ = services.issue_anonymous(); other.cookies[settings.ANONYMOUS_COOKIE] = token
        path = f'/api/v1/catalog/works/{work.pk}/metadata/'
        data = {'description': '恶意改写', 'tags': [], 'revision': 1}
        self.assertEqual(self.post(path, data, other).status_code, 403)
        self.assertEqual(other.get(f'/works/{work.pk}/edit/').status_code, 403)
        self.assertNotContains(other.get(f'/works/{work.pk}/'), '编辑标签与简介')
        self.assertEqual(self.post('/api/v1/identity/reset/', {}).status_code, 200)
        self.assertEqual(self.post(path, data).status_code, 403)

    def test_unused_manual_tags_and_metadata_are_archived_for_fresh_submission(self):
        work=self.new_work()
        public_booklist.set_relation(work,self.person,'like',True)
        public_booklist.set_relation(work,self.person,'recommend',False)
        self.assertEqual(public_booklist.listing()['total'],0)
        self.assertEqual(user_metadata.tag_suggestions('奇幻')['results'],[])
        self.assertEqual(WorkTag.objects.filter(work=work,state='active').count(),0)
        metadata=UserWorkMetadata.objects.get(work=work)
        self.assertEqual(metadata.description,'星际旅行的故事')
        self.assertIsNotNone(metadata.archived_at)
        self.assertEqual(public_booklist.likes().filter(work=work).count(),0)
        result=self.submit({'author':'新作者','description':'新简介','tags':['奇幻']})
        self.assertEqual(result.status_code,200)
        self.assertNotEqual(result.json()['work']['id'],work.pk)
        self.assertEqual(len(user_metadata.tag_suggestions('奇幻')['results']),1)

    def test_tag_used_by_another_shelf_work_or_source_remains_suggested(self):
        work = self.new_work()
        other = Work.objects.create(original_submitted_title='其他作品', public_added_at=timezone.now())
        PublicRecommendation.objects.create(work=other, anonymous_client=self.person)
        tag = Tag.objects.get(name='奇幻')
        WorkTag.objects.create(work=other, tag=tag, source_type='user', anonymous_client=self.person)
        source = Tag.objects.create(name='来源词汇', normalized_name='来源词汇')
        WorkTag.objects.create(work=other, tag=source, source_type='source')
        public_booklist.set_relation(work, self.person, 'recommend', False)
        self.assertEqual(len(user_metadata.tag_suggestions('奇幻')['results']), 1)
        public_booklist.set_relation(other, self.person, 'recommend', False)
        self.assertEqual(user_metadata.tag_suggestions('奇幻')['results'], [])
        self.assertEqual(len(user_metadata.tag_suggestions('来源词汇')['results']), 1)

    def test_activity_recommendation_also_supports_manual_vocabulary(self):
        from booklist import activities
        work = self.new_work()
        admin = services.initialize_admin('tags-owner', 'Tags-Support!Testing-Password-49')
        now = timezone.now()
        period = Activity.objects.create(title='标签活动', start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1), status='active', created_by=admin)
        activities.submit(period.pk, self.person, {'work_id':work.pk, 'title':work.display_title,
            'reason':'', 'confirmed_unmatched':False, 'copy_public_reason':False})
        public_booklist.set_relation(work, self.person, 'recommend', False)
        self.assertEqual(len(user_metadata.tag_suggestions('奇幻')['results']), 1)
        activities.withdraw(period.pk, work.pk, self.person)
        self.assertEqual(user_metadata.tag_suggestions('奇幻')['results'], [])

    def test_manual_description_2000_character_boundary_on_create_and_edit(self):
        response = self.submit({'description': '书'*2000, 'tags': []})
        self.assertEqual(response.status_code, 200, response.content)
        work = Work.objects.get(pk=response.json()['work']['id'])
        self.assertEqual(self.update(work, description='😀'*2000).status_code, 200)
        self.assertEqual(self.update(work, revision=2, description='书'*2001).status_code, 400)
        row = UserWorkMetadata.objects.get(work=work)
        self.assertEqual((row.description, row.revision), ('😀'*2000, 2))
        self.assertContains(self.client.get(f'/works/{work.pk}/edit/'), '最多 2000 字')

    def test_invalid_metadata_rolls_back_entire_recommendation(self):
        for data in ({}, {'description': 'x'*2001, 'tags': []}, {'description': '', 'tags': ['x'*31]},
                     {'description': '', 'tags': [1]}, {'description': '', 'tags': ['x']*21},
                     {'description': '', 'tags': [], 'owner': 2}):
            with self.subTest(data=str(data)[:100]):
                self.assertEqual(self.submit(data).status_code, 400)
                self.assertEqual(Work.objects.count(), 0)
                self.assertEqual(UserWorkMetadata.objects.count(), 0)

    def test_matched_and_admin_fields_are_protected(self):
        work = self.new_work()
        work.description_overridden = True; work.description_override = '管理员简介'; work.save()
        self.assertEqual(self.update(work).status_code, 200)
        self.assertEqual(catalog.work_detail(work.pk)['description'], '管理员简介')
        work.wenku8_book_id = 123; work.identity_status = 'matched'; work.wenku8_description = '文库简介'; work.save()
        self.assertEqual(self.update(work, revision=2).status_code, 403)
        self.assertEqual(self.submit({'description': '覆盖', 'tags': []}, work_id=work.pk).status_code, 409)
        work.description_overridden = False; work.save()
        self.assertEqual(catalog.work_detail(work.pk)['description'], '文库简介')

    def test_existing_candidate_with_no_selected_id_cannot_be_duplicated(self):
        self.new_work()
        self.assertEqual(self.submit({'description': '覆盖', 'tags': []}).status_code, 409)
        self.assertEqual(Work.objects.count(), 1)

    def test_partial_and_fuzzy_suggestions_can_be_explicitly_declined_with_metadata(self):
        Work.objects.create(wenku8_book_id=123, identity_status='matched', wenku8_title='我的美丽庭园')
        for title, kind in [('我的', 'prefix'), ('我的美丽庭院', 'fuzzy')]:
            with self.subTest(title=title):
                result = catalog.search(title)
                self.assertEqual(result['results'][0]['match_kind'], kind)
                metadata = {'description': '这是另一本作品的介绍', 'tags': ['冒险']}
                self.assertEqual(self.submit(metadata, title=title).status_code, 409)
                response = self.submit(metadata, title=title, confirmed_unmatched=True)
                self.assertEqual(response.status_code, 200, response.content)
                work = Work.objects.get(pk=response.json()['work']['id'])
                self.assertIsNone(work.wenku8_book_id)
                self.assertEqual(catalog.work_detail(work.pk)['description'], metadata['description'])
                self.assertEqual(catalog.work_detail(work.pk)['tags'][0]['name'], '冒险')
                self.assertEqual(self.submit(metadata, title=title, confirmed_unmatched=True).status_code, 409)
                self.assertContains(self.client.get(f'/works/{work.pk}/'), '编辑标签与简介')

    def test_idempotent_metadata_update_has_one_revision_and_no_duplicate_links(self):
        work = self.new_work(); key = str(uuid.uuid4())
        data = {'description': '重复提交', 'tags': ['新标签'], 'revision': 1}
        path = f'/api/v1/catalog/works/{work.pk}/metadata/'
        first = self.post(path, data, key=key); second = self.post(path, data, key=key)
        self.assertEqual(first.status_code, 200); self.assertEqual(first.json(), second.json())
        self.assertEqual(UserWorkMetadata.objects.get(work=work).revision, 2)
        self.assertEqual(WorkTag.objects.filter(work=work, state='active').count(), 1)

    def test_legacy_work_belongs_to_first_submitter_even_after_withdrawal(self):
        work = Work.objects.create(original_submitted_title='旧手工作品')
        PublicRecommendation.objects.create(work=work, anonymous_client=self.person, origin='submit', withdrawn_at=timezone.now())
        self.assertEqual(self.update(work, revision=0).status_code, 200)

    def test_activity_submission_and_readonly_rule(self):
        admin = services.initialize_admin('owner', 'Manual-Metadata!Testing-Password-49')
        now = timezone.now()
        activity = Activity.objects.create(title='一周推荐', start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1),
            scheduled_end_at=now+timedelta(days=1), status='active', created_by=admin)
        path = f'/api/v1/activities/{activity.pk}/recommendations/'
        data = {'work_id': None, 'title': '独立星际航行笔记', 'reason': '', 'confirmed_unmatched': False,
            'copy_public_reason': False, 'metadata': {'description': '活动作品介绍', 'tags': ['科幻']}}
        response = self.post(path, data)
        self.assertEqual(response.status_code, 200, response.content)
        work = Work.objects.get(pk=response.json()['work_id'])
        self.assertEqual(catalog.work_detail(work.pk)['description'], '活动作品介绍')
        self.assertContains(self.client.get(f'/works/{work.pk}/?activity={activity.pk}'), '编辑标签与简介')
        activity.status = 'paused'; activity.save()
        self.assertEqual(self.post(path, {**data, 'title': '第二本全新星际航行'}).status_code, 409)
        self.assertEqual(Work.objects.count(), 1)

    def test_suppressed_tag_cannot_be_recreated_by_owner(self):
        work = self.new_work()
        admin = services.initialize_admin('owner', 'Manual-Metadata!Testing-Password-49')
        tag = Tag.objects.get(name='奇幻')
        TagSuppression.objects.create(work=work, tag=tag, admin=admin, scope='all', reason='测试屏蔽')
        self.assertEqual(self.update(work, tags=['奇幻']).status_code, 409)
        self.assertEqual(UserWorkMetadata.objects.get(work=work).revision, 1)

    def test_merge_manual_metadata_and_undo_preserve_owner_and_description(self):
        work = self.new_work()
        other = Work.objects.create(original_submitted_title='另一册记录')
        user_metadata.initialize(other, self.person, {'description': '另一份简介', 'tags': []})
        admin = services.initialize_admin('owner', 'Manual-Metadata!Testing-Password-49')
        plan = merging.plan(work.pk, other.pk)
        self.assertIn('user_description', [c['key'] for c in plan['conflicts']])
        operation = merging.merge(admin, work.pk, other.pk, {'user_description': 'secondary'}, plan['version'], '确认重复')
        self.assertEqual(catalog.work_detail(work.pk)['description'], '另一份简介')
        merging.undo(admin, operation.pk, '撤销测试')
        self.assertEqual(catalog.work_detail(work.pk)['description'], '星际旅行的故事')
        self.assertEqual(catalog.work_detail(other.pk)['description'], '另一份简介')

    def test_merge_undo_refuses_later_metadata_change(self):
        work = self.new_work()
        other = Work.objects.create(original_submitted_title='另一册记录')
        admin = services.initialize_admin('owner', 'Manual-Metadata!Testing-Password-49')
        plan = merging.plan(work.pk, other.pk)
        operation = merging.merge(admin, work.pk, other.pk, {}, plan['version'], '确认重复')
        self.assertEqual(self.update(work).status_code, 200)
        with self.assertRaises(services.DomainError) as caught:
            merging.undo(admin, operation.pk, '撤销测试')
        self.assertEqual(caught.exception.code, 'merge_modified')
