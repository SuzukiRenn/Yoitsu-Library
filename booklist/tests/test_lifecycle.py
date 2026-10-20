from datetime import timedelta
import json
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from booklist import activities, catalog, public_booklist as books, services, user_metadata
from booklist.models import Activity, ActivityWork, Work, UserWorkMetadata, WorkTag, PublicLike, Recommendation, Vote


class LifecycleTests(TestCase):
    def setUp(self):
        self.admin = services.initialize_admin('cycle-owner', 'Lifecycle-Testing!Password-49')
        token, self.person = services.issue_anonymous()
        self.client.cookies[settings.ANONYMOUS_COOKIE] = token
        _, self.other = services.issue_anonymous()

    def manual(self, title='重填资料验收作品', author='初始作者', description='初始简介', tags=None):
        result = books.submit(self.person, {'work_id':None, 'title':title, 'reason':'公共旧理由',
            'confirmed_unmatched':True, 'metadata':{'author':author, 'description':description, 'tags':tags or ['初始标签']}})
        return Work.objects.get(pk=result['work']['id'])

    def period(self):
        now = timezone.now()
        return Activity.objects.create(title='生命周期活动', start_at=now-timedelta(hours=1),
            started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1), status='active', created_by=self.admin)

    def enroll(self, period, work, reason='活动旧理由'):
        return activities.submit(period.pk, self.person, {'work_id':work.pk, 'title':work.display_title,
            'reason':reason, 'confirmed_unmatched':False, 'copy_public_reason':False})

    def test_manual_author_roundtrip_validation_and_admin_precedence(self):
        work = self.manual()
        self.assertEqual(catalog.work_detail(work.pk)['author'], '初始作者')
        page = self.client.get(f'/works/{work.pk}/edit/')
        self.assertContains(page, 'id="manual-author"')
        self.assertContains(page, 'value="初始作者"')
        payload = {'author':'更新作者', 'description':'更新简介', 'tags':['更新标签'], 'revision':1}
        response = self.client.post(f'/api/v1/catalog/works/{work.pk}/metadata/', json.dumps(payload),
            content_type='application/json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(catalog.work_detail(work.pk)['author'],'更新作者')
        self.assertEqual(user_metadata.values(work,self.person)['author'],'更新作者')
        self.assertEqual(user_metadata.validate({'author':'作'*20,'description':'','tags':[]})[0], '作'*20)
        for author in [None, 7, '作'*21]:
            with self.assertRaises(ValidationError): user_metadata.validate({'author':author,'description':'','tags':[]})
        work.author_overridden=True;work.author_override='管理员作者';work.save()
        self.assertEqual(catalog.work_detail(work.pk)['author'],'管理员作者')

    def test_scopes_reset_independently_and_manual_retires_only_after_all_current_scopes(self):
        work = self.manual();period=self.period();self.enroll(period,work)
        books.set_relation(work,self.person,'like',True)
        activities.like(period.pk,work.pk,self.person,True)
        books.set_relation(work,self.person,'recommend',False)
        self.assertEqual(books.reasons().filter(work=work).count(),0)
        self.assertEqual(books.likes().filter(work=work).count(),0)
        self.assertEqual(activities.listing(period.pk)['results'][0]['like_count'],1)
        self.assertEqual(activities.listing(period.pk,self.person)['results'][0]['own_reason'],'活动旧理由')
        self.assertEqual(activities.recs(period.pk).get().reason,'活动旧理由')
        self.assertEqual(catalog.work_detail(work.pk)['author'],'初始作者')
        self.assertTrue(UserWorkMetadata.objects.filter(work=work,archived_at__isnull=True).exists())
        activities.withdraw(period.pk,work.pk,self.person)
        self.assertFalse(catalog.visible_works().filter(pk=work.pk).exists())
        self.assertEqual(activities.votes(period.pk).count(),0)
        self.assertEqual(Vote.objects.filter(work=work,archived_at__isnull=False).count(),1)
        self.assertTrue(UserWorkMetadata.objects.filter(work=work,archived_at__isnull=False).exists())
        self.assertEqual(WorkTag.objects.filter(work=work,state='active').count(),0)
        result = books.submit(self.other, {'work_id':None,'title':work.display_title,'reason':'新的理由',
            'confirmed_unmatched':False,'metadata':{'author':'第二位作者','description':'第二次简介','tags':['第二次标签']}})
        new = result['work']
        self.assertNotEqual(new['id'],work.pk)
        self.assertEqual((new['author'],new['recommendation_count'],new['like_count']),('第二位作者',1,0))
        self.assertEqual(catalog.work_detail(new['id'])['description'],'第二次简介')
        self.assertEqual(Recommendation.objects.filter(work=work,archived_at__isnull=False).count(),2)

    def test_source_work_keeps_metadata_but_each_scope_starts_clean(self):
        work=Work.objects.create(wenku8_book_id=100001,identity_status='matched',wenku8_title='来源作品',
            wenku8_author='来源作者',wenku8_description='来源简介')
        books.submit(self.person,{'work_id':work.pk,'title':work.display_title,'reason':'公共旧理由','confirmed_unmatched':False})
        period=self.period();self.enroll(period,work)
        books.set_relation(work,self.person,'like',True)
        activities.like(period.pk,work.pk,self.person,True)
        activities.withdraw(period.pk,work.pk,self.person)
        self.assertEqual(books.likes().filter(work=work).count(),1)
        self.enroll(period,work,reason='')
        row=activities.listing(period.pk,self.person)['results'][0]
        self.assertEqual((row['recommendation_count'],row['like_count'],row['own_reason']),(1,0,''))
        self.assertEqual(ActivityWork.objects.filter(work=work).count(),2)
        books.set_relation(work,self.person,'recommend',False)
        result=books.submit(self.other,{'work_id':work.pk,'title':work.display_title,'reason':'','confirmed_unmatched':False})
        self.assertEqual((result['work']['recommendation_count'],result['work']['like_count'],result['work']['own_reason']),(1,0,''))
        self.assertEqual(catalog.work_detail(work.pk)['description'],'来源简介')
        self.assertEqual(catalog.work_detail(work.pk)['author'],'来源作者')
        self.assertEqual(Work.objects.count(),1)

    def test_ended_activity_keeps_reason_author_tags_and_description_after_manual_resubmission(self):
        work=self.manual();period=self.period();self.enroll(period,work)
        now=timezone.now();period.ended_at=now;period.end_reason='manual'
        activities.transition(period,'ended',now,now,'manual',self.admin)
        user_metadata.update(work,self.person,{'author':'结束后作者','description':'结束后简介','tags':['结束后标签']},1)
        books.set_relation(work,self.person,'recommend',False)
        self.assertFalse(catalog.visible_works().filter(pk=work.pk).exists())
        self.assertEqual(activities.listing(period.pk)['total'],1)
        page=self.client.get(f'/works/{work.pk}/?activity={period.pk}')
        for text in ['初始作者','初始标签','初始简介','活动旧理由']:
            self.assertContains(page,text)
        self.assertNotContains(page,'结束后简介')
        self.assertNotContains(page,'编辑标签与简介')
        self.manual(author='新一轮作者',description='新一轮简介',tags=['新一轮标签'])
        page=self.client.get(f'/works/{work.pk}/?activity={period.pk}')
        self.assertContains(page,'初始简介');self.assertNotContains(page,'新一轮简介')
        stats=self.client.get(f'/api/v1/activities/{period.pk}/stats/',{'ids':work.pk}).json()['results'][0]
        self.assertTrue(stats['available']);self.assertEqual(stats['recommendation_count'],1)
        reasons=self.client.get(f'/api/v1/activities/{period.pk}/works/{work.pk}/reasons/').json()
        self.assertEqual([row['reason'] for row in reasons['results']],['活动旧理由'])

    def test_retirement_does_not_bypass_hidden_identity_governance(self):
        work=self.manual();books.set_relation(work,self.person,'recommend',False)
        work.hidden_at=timezone.now();work.save(update_fields=['hidden_at'])
        result=books.submit(self.other,{'work_id':None,'title':work.display_title,'reason':'',
            'confirmed_unmatched':True,'metadata':{'author':'新作者','description':'新简介','tags':[]}})
        self.assertEqual(result['status'],'pending')

    def test_explicit_upgrade_reconciliation_is_idempotent_and_keeps_history(self):
        from booklist.lifecycle import reconcile_inactive_cycles
        work=self.manual();period=self.period();self.enroll(period,work)
        activities.like(period.pk,work.pk,self.person,True)
        # Simulate records left at zero by the old implementation.
        books.recommendations().filter(work=work).update(withdrawn_at=timezone.now())
        activities.recs(period.pk).filter(work=work).update(withdrawn_at=timezone.now())
        plan=reconcile_inactive_cycles()
        self.assertIn(work.pk,plan['manual_ids'])
        self.assertTrue(catalog.visible_works().filter(pk=work.pk).exists())
        reconcile_inactive_cycles(apply=True)
        self.assertFalse(catalog.visible_works().filter(pk=work.pk).exists())
        self.assertEqual(activities.votes(period.pk).count(),0)
        self.assertEqual(reconcile_inactive_cycles(),{'activity_cycles':[],'public_cycles':[],'manual_ids':[]})
