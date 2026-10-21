import json
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone
from booklist import services, moderation, merging, activities, activity_admin, public_booklist as books
from booklist.models import (Work, Recommendation, PublicRecommendation, PublicLike, Activity, ActivityWork,
    Vote, WorkAlias, WorkTag, Tag, TagSuppression, PendingActivitySubmission, AdminAuditLog, WorkMergeOperation)


class AdminTests(TestCase):
    def setUp(self):
        self.actor = services.initialize_admin('owner', 'Synthetic-Admin!Pass-123')
        token, _ = services.login('owner', 'Synthetic-Admin!Pass-123')
        self.client = Client(enforce_csrf_checks=True)
        self.client.cookies[settings.ADMIN_COOKIE] = token
        self.client.get('/api/v1/csrf/')
        _, self.person = services.issue_anonymous()
        self.first = Work.objects.create(original_submitted_title='主作品',public_added_at=timezone.now())
        self.second = Work.objects.create(original_submitted_title='待合并',public_added_at=timezone.now())
        now = timezone.now()
        self.period = Activity.objects.create(title='历史活动',status='ended',start_at=now-timedelta(days=2),
            scheduled_end_at=now-timedelta(days=1),started_at=now-timedelta(days=2),ended_at=now-timedelta(days=1),created_by=self.actor)

    def post(self, url, payload, client=None):
        browser = client or self.client
        return browser.post(url, json.dumps(payload),content_type='application/json',
            HTTP_X_CSRFTOKEN=browser.cookies.get('csrftoken').value if browser.cookies.get('csrftoken') else '')

    def merge(self, choices=None):
        plan = merging.plan(self.first.pk,self.second.pk)
        return merging.merge(self.actor,self.first.pk,self.second.pk,choices or {},plan['version'],'确认同一本书')

    def snap(self, work):
        return ActivityWork.objects.create(activity=self.period,work=work,snapshot_title='历史'+work.display_title)

    def test_pages_require_login_and_no_moderation_leak(self):
        browser = Client(enforce_csrf_checks=True)
        self.assertRedirects(browser.get('/manage/'),'/manage/login/',fetch_redirect_response=False)
        self.assertEqual(browser.get('/manage/works/1/').status_code,302)
        self.assertEqual(self.post('/api/v1/admin/works/1/action/',{'action':'hide','reason':'核查'},browser).status_code,403)
        browser.get('/api/v1/csrf/')
        self.assertEqual(self.post('/api/v1/admin/works/1/action/',{'action':'hide','reason':'核查'},browser).status_code,401)

    def test_all_pages_render_escaped_content(self):
        self.second.original_submitted_title='<script>alert(1)</script>';self.second.save()
        for url in ('/manage/','/manage/?section=pending','/manage/?section=audit','/manage/?section=activities','/manage/?section=merges',
                    f'/manage/works/{self.second.pk}/',f'/manage/activities/{self.period.pk}/',
                    f'/manage/merge/?primary={self.first.pk}&secondary={self.second.pk}'):
            response = self.client.get(url)
            self.assertEqual(response.status_code,200,url)
            self.assertNotContains(response,'<script>alert(1)</script>')

    def test_admin_navigation_has_distinct_destinations(self):
        response = self.client.get('/manage/')
        self.assertContains(response, '<strong class="admin-brand">书单管理</strong>', html=True)
        self.assertContains(response, '<a href="/manage/" aria-current="page">作品库</a>', html=True)
        self.assertContains(response, '<a href="/manage/?section=public">公共书单</a>', html=True)
        self.assertContains(response, '<a href="/">查看前台</a>', html=True)
        self.assertNotContains(response, '>管理</a>')
        response = self.client.get(f'/manage/activities/{self.period.pk}/')
        self.assertContains(response, '<a href="/manage/?section=activities" aria-current="page">推荐活动</a>', html=True)

    def test_admin_public_shelf_filters_and_counts_active_records(self):
        PublicRecommendation.objects.create(work=self.first, anonymous_client=self.person)
        PublicLike.objects.create(work=self.first, anonymous_client=self.person)
        PublicLike.objects.create(work=self.second, anonymous_client=self.person)
        response = self.client.get('/manage/?section=public')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '主作品')
        self.assertNotContains(response, '待合并')
        self.assertContains(response, '推荐 1 · 点赞 1')
        self.assertContains(response, '管理作品与理由')
        self.assertContains(response, '<a href="/manage/?section=public" aria-current="page">公共书单</a>', html=True)
        self.assertNotContains(self.client.get('/manage/?section=public&q=不存在'), '主作品')
        moderation.visibility(self.actor, self.first.pk, True, '测试隐藏')
        self.assertNotContains(self.client.get('/manage/?section=public'), '主作品')
        browser = Client()
        self.assertRedirects(browser.get('/manage/?section=public'), '/manage/login/', fetch_redirect_response=False)

    def test_public_pages_keep_admin_return_link_private(self):
        for url in ('/', '/activities/', f'/activities/{self.period.pk}/', f'/works/{self.first.pk}/'):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), '<a class="admin-return" href="/manage/">返回管理后台</a>', html=True)
                self.assertNotContains(Client().get(url), '返回管理后台')

    def test_visibility_and_reason_hiding_are_independent_from_counts(self):
        snap = self.snap(self.first)
        row = Recommendation.objects.create(scope='activity',activity=self.period,activity_work=snap,work=self.first,anonymous_client=self.person,reason='保留原文')
        moderation.moderate_reason(self.actor,row.pk,'hide','文字违规')
        self.assertEqual(activities.recs(self.period.pk).count(),1)
        row.refresh_from_db();self.assertEqual(row.reason,'保留原文');self.assertIsNotNone(row.text_hidden_at)
        moderation.moderate_reason(self.actor,row.pk,'restore','复核')
        moderation.visibility(self.actor,self.first.pk,True,'隐藏作品')
        self.assertEqual(activities.listing(self.period.pk)['total'],0)
        moderation.visibility(self.actor,self.first.pk,False,'恢复作品')
        self.assertEqual(activities.listing(self.period.pk)['total'],1)
        self.period.refresh_from_db();self.assertIsNotNone(self.period.corrected_at)
        self.assertTrue(AdminAuditLog.objects.filter(action='reason.hide',before_data__reason='保留原文').exists())

    def test_invalidation_and_empty_reason_rejected(self):
        rec = PublicRecommendation.objects.create(work=self.first,anonymous_client=self.person)
        with self.assertRaises(ValidationError): moderation.visibility(self.actor,self.first.pk,True,' ')
        moderation.invalidate_relation(self.actor,'recommendation',rec.pk,'取消资格')
        with self.assertRaises(services.DomainError): books.set_relation(self.first,self.person,'recommend',True)

    def test_approve_pending_monthly_preserves_scope_and_history(self):
        self.second.review_status='pending';self.second.public_added_at=None;self.second.save()
        PendingActivitySubmission.objects.create(work=self.second,activity=self.period,anonymous_client=self.person,reason='待核查理由',copy_public_reason=False)
        moderation.approve(self.actor,self.second.pk,'核实为独立作品')
        self.assertEqual(activities.recs(self.period.pk).count(),1)
        self.assertEqual(PublicRecommendation.objects.count(),1);self.assertFalse(PublicLike.objects.exists())
        self.assertFalse(books.reasons().exists());self.assertEqual(PendingActivitySubmission.objects.count(),1)
        with self.assertRaises(services.DomainError): moderation.approve(self.actor,self.second.pk,'重复批准')

    def test_match_pending_monthly_to_existing_work_and_undo(self):
        self.second.review_status='pending';self.second.public_added_at=None;self.second.save()
        claim=PendingActivitySubmission.objects.create(work=self.second,activity=self.period,anonymous_client=self.person,
            reason='本期理由',copy_public_reason=True)
        public=Recommendation.objects.create(work=self.first,scope='public',anonymous_client=self.person,reason='已有公共理由')
        operation=self.merge()
        rec=activities.recs(self.period.pk).get();self.assertEqual(rec.work_id,self.first.pk)
        self.assertEqual(rec.reason,'本期理由');self.assertEqual(PublicRecommendation.objects.filter(work=self.first).count(),1)
        public.refresh_from_db();self.assertEqual(public.reason,'已有公共理由')
        merging.undo(self.actor,operation.pk,'撤销核查')
        self.second.refresh_from_db();self.assertEqual(self.second.review_status,'pending')
        self.assertFalse(activities.recs(self.period.pk).exists());self.assertFalse(ActivityWork.objects.exists())
        self.assertTrue(PendingActivitySubmission.objects.filter(pk=claim.pk).exists())

    def test_merge_deduplicates_public_relations_and_undo_restores(self):
        for work in (self.first,self.second):
            PublicRecommendation.objects.create(work=work,anonymous_client=self.person)
            PublicLike.objects.create(work=work,anonymous_client=self.person)
        operation=self.merge()
        self.assertEqual(books.recommendations().filter(work=self.first).count(),1)
        self.assertEqual(books.likes().filter(work=self.first).count(),1)
        self.second.refresh_from_db();self.assertEqual(self.second.merged_into_id,self.first.pk)
        merging.undo(self.actor,operation.pk,'撤销误合并')
        self.assertEqual(books.recommendations().count(),2);self.assertEqual(books.likes().count(),2)
        self.second.refresh_from_db();self.assertIsNone(self.second.merged_into_id)

    def test_merge_snapshot_conflicts_keep_newer_rec_and_existing_snapshot(self):
        one,two=self.snap(self.first),self.snap(self.second)
        old=Recommendation.objects.create(scope='activity',activity=self.period,activity_work=one,work=self.first,anonymous_client=self.person,reason='旧理由')
        new=Recommendation.objects.create(scope='activity',activity=self.period,activity_work=two,work=self.second,anonymous_client=self.person,reason='新理由',text_hidden_at=timezone.now())
        Vote.objects.create(scope='activity',activity=self.period,activity_work=two,work=self.second,anonymous_client=self.person)
        operation=self.merge()
        current=activities.recs(self.period.pk).get();self.assertEqual(current.pk,new.pk);self.assertEqual(current.activity_work_id,one.pk)
        self.assertIsNotNone(current.text_hidden_at);one.refresh_from_db();self.assertEqual(one.snapshot_title,'历史主作品')
        self.assertEqual(activities.votes(self.period.pk).count(),1)
        merging.undo(self.actor,operation.pk,'核查撤销')
        self.assertEqual(activities.recs(self.period.pk).count(),2)
        old.refresh_from_db();new.refresh_from_db();self.assertEqual(new.activity_work_id,two.pk);self.assertIsNone(old.text_hidden_at)

    def test_snapshot_without_target_can_move_and_undo(self):
        snap=self.snap(self.second)
        Recommendation.objects.create(scope='activity',activity=self.period,activity_work=snap,work=self.second,anonymous_client=self.person)
        operation=self.merge();self.assertEqual(activities.recs(self.period.pk).get().work_id,self.first.pk)
        merging.undo(self.actor,operation.pk,'撤销');snap.refresh_from_db();self.assertEqual(snap.work_id,self.second.pk)

    def test_override_conflicts_require_all_choices_atomic(self):
        for row,value in ((self.first,'甲'),(self.second,'乙')):
            row.title_overridden=True;row.title_override=value;row.save()
        with self.assertRaises(services.DomainError):self.merge()
        self.assertEqual(WorkMergeOperation.objects.count(),0)
        operation=self.merge({'title':'secondary'});self.first.refresh_from_db();self.assertEqual(self.first.display_title,'乙')
        merging.undo(self.actor,operation.pk,'撤销');self.first.refresh_from_db();self.assertEqual(self.first.display_title,'甲')

    def test_equal_timestamp_reason_conflict_requires_choice(self):
        a=Recommendation.objects.create(work=self.first,anonymous_client=self.person,scope='public',reason='甲')
        b=Recommendation.objects.create(work=self.second,anonymous_client=self.person,scope='public',reason='乙')
        Recommendation.objects.filter(pk=b.pk).update(updated_at=a.updated_at)
        with self.assertRaises(services.DomainError):self.merge()
        self.merge({'reason:'+str(b.pk):'primary'})
        self.assertEqual(books.reasons().filter(work=self.first).get().reason,'甲')

    def test_merge_rejects_stale_preview_and_source_mismatch(self):
        version=merging.plan(self.first.pk,self.second.pk)['version']
        PublicLike.objects.create(work=self.first,anonymous_client=self.person)
        with self.assertRaises(services.DomainError):merging.merge(self.actor,self.first.pk,self.second.pk,{},version,'过期')
        self.second.wenku8_book_id=123;self.second.identity_status='matched';self.second.save()
        with self.assertRaises(services.DomainError):self.merge()

    def test_undo_rejects_later_new_content(self):
        operation=self.merge()
        PublicLike.objects.create(work=self.first,anonymous_client=self.person)
        with self.assertRaises(services.DomainError):merging.undo(self.actor,operation.pk,'不能覆盖新赞')
        operation.refresh_from_db();self.assertEqual(operation.status,'completed')

    def test_merge_preserves_submillisecond_reason_order(self):
        a=Recommendation.objects.create(work=self.first,anonymous_client=self.person,scope='public',reason='旧')
        b=Recommendation.objects.create(work=self.second,anonymous_client=self.person,scope='public',reason='新')
        moment=timezone.now().replace(microsecond=123001)
        Recommendation.objects.filter(pk=a.pk).update(updated_at=moment)
        Recommendation.objects.filter(pk=b.pk).update(updated_at=moment.replace(microsecond=123002))
        operation=self.merge()
        self.assertEqual(books.reasons().filter(work=self.first).get().reason,'新')
        merging.undo(self.actor,operation.pk,'撤销')
        a.refresh_from_db();b.refresh_from_db();self.assertEqual(a.updated_at,moment)
        self.assertEqual(b.updated_at,moment.replace(microsecond=123002))

    def test_archived_moderation_history_follows_merge(self):
        rec=PublicRecommendation.objects.create(work=self.second,anonymous_client=self.person,archived_at=timezone.now(),invalidated_at=timezone.now())
        operation=self.merge();rec.refresh_from_db();self.assertEqual(rec.work_id,self.first.pk)
        with self.assertRaises(services.DomainError):books.set_relation(self.first,self.person,'recommend',True)
        merging.undo(self.actor,operation.pk,'撤销');rec.refresh_from_db();self.assertEqual(rec.work_id,self.second.pk)

    def test_archived_hidden_reason_cannot_be_unhidden_by_merge(self):
        current=Recommendation.objects.create(work=self.first,anonymous_client=self.person,scope='public',reason='公开理由')
        Recommendation.objects.create(work=self.second,anonymous_client=self.person,scope='public',reason='治理原文',
            text_hidden_at=timezone.now(),archived_at=timezone.now())
        operation=self.merge();current.refresh_from_db();self.assertIsNotNone(current.text_hidden_at)
        merging.undo(self.actor,operation.pk,'撤销');current.refresh_from_db();self.assertIsNone(current.text_hidden_at)

    def test_invalidated_history_cannot_gain_valid_relation_through_merge(self):
        PublicRecommendation.objects.create(work=self.first,anonymous_client=self.person,invalidated_at=timezone.now(),archived_at=timezone.now())
        active=PublicRecommendation.objects.create(work=self.second,anonymous_client=self.person)
        operation=self.merge();self.assertFalse(books.recommendations().exists())
        merging.undo(self.actor,operation.pk,'撤销');active.refresh_from_db();self.assertIsNone(active.invalidated_at)
        self.assertEqual(books.recommendations().get().pk,active.pk)

    def test_pending_tied_reason_requires_explicit_choice(self):
        self.second.review_status='pending';self.second.save()
        primary_snap=self.snap(self.first)
        rec=Recommendation.objects.create(work=self.first,activity=self.period,activity_work=primary_snap,
            anonymous_client=self.person,scope='activity',reason='主作品理由')
        claim=PendingActivitySubmission.objects.create(work=self.second,activity=self.period,anonymous_client=self.person,reason='申请理由')
        PendingActivitySubmission.objects.filter(pk=claim.pk).update(created_at=rec.updated_at)
        with self.assertRaises(services.DomainError):self.merge()
        operation=self.merge({'pending:'+str(claim.pk):'secondary'})
        self.assertEqual(activities.recs(self.period.pk).get().reason,'申请理由')
        merging.undo(self.actor,operation.pk,'撤销')
        self.assertEqual(activities.recs(self.period.pk).get().reason,'主作品理由')

    def test_csrf_and_disabled_admin_block_mutation(self):
        response=self.client.post(f'/api/v1/admin/works/{self.first.pk}/action/',json.dumps({'action':'hide','reason':'无凭证'}),content_type='application/json')
        self.assertEqual(response.status_code,403)
        services.create_admin(self.actor,'other','Another-Synthetic!Pass-123')
        services.disable_admin(self.actor,self.actor.pk)
        self.assertEqual(self.post(f'/api/v1/admin/works/{self.first.pk}/action/',{'action':'hide','reason':'停用后'}).status_code,401)
        self.first.refresh_from_db();self.assertIsNone(self.first.hidden_at)

    def test_alias_tag_ban_union_and_undo(self):
        tag=Tag.objects.create(name='标签',normalized_name='标签')
        for work in (self.first,self.second):
            WorkAlias.objects.create(work=work,alias='简称',normalized_alias='简称')
            WorkTag.objects.create(work=work,tag=tag,source_type='source')
        TagSuppression.objects.create(work=self.second,tag=tag,scope='all',admin=self.actor,reason='禁止')
        operation=self.merge()
        self.assertEqual(WorkAlias.objects.filter(work=self.first,alias='简称',archived_at__isnull=True).count(),1)
        self.assertEqual(books.effective_tags().filter(work=self.first).count(),0)
        merging.undo(self.actor,operation.pk,'撤销')
        self.assertEqual(WorkTag.objects.filter(state='active').count(),2)
        self.assertEqual(TagSuppression.objects.get().work_id,self.second.pk)

    def test_activity_create_and_transition_from_admin_ui_api(self):
        start=timezone.now()+timedelta(days=1);end=start+timedelta(days=2)
        response=self.post('/api/v1/admin/activities/',{'title':'下月','theme':'主题','start_at':start.isoformat(),'end_at':end.isoformat()})
        self.assertEqual(response.status_code,201)
        pk=response.json()['id']
        self.assertEqual(self.post(f'/api/v1/admin/activities/{pk}/action/',{'action':'schedule','reason':'排期'}).status_code,200)
        self.assertEqual(Activity.objects.get(pk=pk).status,'scheduled')

    def test_active_update_preserves_precise_locked_start_and_end(self):
        now=timezone.now().replace(microsecond=123456)
        period=Activity.objects.create(title='进行中',start_at=now,started_at=now,status='active',
            scheduled_end_at=now+timedelta(days=1),created_by=self.actor)
        data={'title':'已改主题','theme':'新主题','start_at':now.isoformat(),
            'end_at':period.scheduled_end_at.isoformat(),'reason':'修改主题'}
        activity_admin.update(self.actor,period.pk,data)
        data['start_at']=now.replace(microsecond=123000).isoformat()
        data['end_at']=period.scheduled_end_at.replace(microsecond=123000).isoformat()
        activity_admin.update(self.actor,period.pk,data)
        period.refresh_from_db();self.assertEqual(period.start_at,now)
        self.assertEqual(period.scheduled_end_at,now+timedelta(days=1))
        data['start_at']=(now+timedelta(seconds=1)).isoformat()
        with self.assertRaises(services.DomainError):activity_admin.update(self.actor,period.pk,data)

    def test_reviewed_admin_creation_has_no_personal_recommendation(self):
        response=self.post('/api/v1/admin/works/',{'title':'主作品','author':'同名不同作者','reason':'核查为不同作品'})
        self.assertEqual(response.status_code,201)
        work=Work.objects.get(pk=response.json()['id'])
        self.assertIsNotNone(work.public_added_at);self.assertEqual(work.author_override,'同名不同作者')
        self.assertFalse(PublicRecommendation.objects.filter(work=work).exists())
