import json
from datetime import timedelta
from django.conf import settings
from django.test import Client,TestCase,override_settings
from django.utils import timezone
from booklist import accounts,services,activities,public_booklist as books
from booklist.models import Account,Activity,Work,RecentWorkView,Recommendation,Vote,PublicRecommendation,PublicLike,Notification,ReasonLike


@override_settings(REQUIRE_ACCOUNT=True)
class PersonalDiscoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tokens={};cls.owners={}
        for name in ('alice','bob'):
            token,session=accounts.register(name,name,'Discovery-Reader!Strong-9341')
            cls.tokens[name]=token;cls.owners[name]=session.account
        cls.admin=services.initialize_admin('discovery-admin','Discovery-Admin!Strong-9341')
        cls.now=timezone.now()
        cls.period=Activity.objects.create(title='进行中的合成主题',status='active',sync_public=False,created_by=cls.admin,
            start_at=cls.now-timedelta(hours=1),started_at=cls.now-timedelta(hours=1),scheduled_end_at=cls.now+timedelta(days=1))
        cls.work=Work.objects.create(original_submitted_title='第一本合成作品')
        cls.second=Work.objects.create(original_submitted_title='第二本合成作品')

    def browser(self,who='alice',csrf=False):
        client=Client(enforce_csrf_checks=csrf)
        if who:client.cookies[settings.ACCOUNT_COOKIE]=self.tokens[who]
        return client

    def post(self,client,path,data):return client.post(path,json.dumps(data),content_type='application/json')

    def visit(self,work=None,period=None,who='alice',client=None):
        return self.post(client or self.browser(who),'/api/v1/account/recent/visit/',
            {'account_id':self.owners[who].pk,'work_id':(work or self.work).pk,'activity_id':period.pk if period else None})

    def submit(self,work=None,period=None,who='alice'):
        work=work or self.work;period=period or self.period
        return activities.submit(period.pk,self.owners[who].principal,{'work_id':work.pk,'title':work.display_title,
            'reason':'自己的理由','copy_public_reason':False})

    def manage(self,action,who='alice',**data):
        return self.post(self.browser(who),'/api/v1/account/recent/manage/',{'account_id':self.owners[who].pk,'action':action,**data})

    def test_history_syncs_across_sessions_and_latest_scope_replaces_one_work(self):
        self.assertEqual(self.visit().status_code,200)
        self.submit()
        self.assertEqual(self.visit(period=self.period).status_code,200)
        self.assertEqual(RecentWorkView.objects.count(),1)
        row=RecentWorkView.objects.get();self.assertEqual(row.activity_id,self.period.pk)
        token,_=accounts.session(self.owners['alice'])
        other_device=Client();other_device.cookies[settings.ACCOUNT_COOKIE]=token
        page=other_device.get('/account/?tab=recent')
        self.assertEqual(page.context['recent_total'],1)
        self.assertEqual(page.context['contributions'][0]['scope'],self.period.title)
        self.assertEqual(self.browser('bob').get('/account/?tab=recent').context['recent_total'],0)
        self.visit();row.refresh_from_db();self.assertIsNone(row.activity_id)

    def test_browsing_does_not_recommend_like_or_send_notifications(self):
        before=[m.objects.count() for m in (Recommendation,PublicRecommendation,Vote,PublicLike,ReasonLike,Notification)]
        self.visit();self.browser().get('/account/?tab=recent')
        self.assertEqual([m.objects.count() for m in (Recommendation,PublicRecommendation,Vote,PublicLike,ReasonLike,Notification)],before)
        self.work.refresh_from_db();self.assertIsNone(self.work.public_added_at)

    def test_history_management_is_account_scoped_and_disable_preserves_existing(self):
        self.visit(who='alice');self.visit(who='bob')
        self.manage('remove',work_id=self.work.pk)
        self.assertEqual(list(RecentWorkView.objects.values_list('account_id',flat=True)),[self.owners['bob'].pk])
        self.visit();self.manage('tracking',enabled=False)
        self.assertEqual(self.visit(self.second).json(),{'recorded':False})
        self.assertEqual(RecentWorkView.objects.filter(account=self.owners['alice']).count(),1)
        self.manage('clear');self.assertFalse(RecentWorkView.objects.filter(account=self.owners['alice']).exists())
        self.assertTrue(RecentWorkView.objects.filter(account=self.owners['bob']).exists())
        self.manage('tracking',enabled=True);self.visit(self.second)
        self.assertTrue(RecentWorkView.objects.filter(account=self.owners['alice'],work=self.second).exists())

    def test_owner_change_csrf_and_malformed_payloads_are_rejected(self):
        payload={'account_id':self.owners['alice'].pk,'work_id':self.work.pk,'activity_id':None}
        self.assertEqual(self.post(self.browser(None),'/api/v1/account/recent/visit/',payload).status_code,401)
        self.assertEqual(self.post(self.browser('bob'),'/api/v1/account/recent/visit/',payload).status_code,409)
        self.assertEqual(self.post(self.browser(csrf=True),'/api/v1/account/recent/visit/',payload).status_code,403)
        for bad in ({**payload,'work_id':True},{**payload,'activity_id':0},{**payload,'other':1}):
            self.assertEqual(self.post(self.browser(),'/api/v1/account/recent/visit/',bad).status_code,400)
        self.assertEqual(self.manage('clear',work_id=self.work.pk).status_code,400)
        self.assertFalse(RecentWorkView.objects.exists())

    def test_hidden_unrelated_or_draft_activity_cannot_be_recorded(self):
        self.assertEqual(self.visit(period=self.period).status_code,404)
        self.submit();self.assertEqual(self.visit(period=self.period).status_code,200)
        Work.objects.filter(pk=self.work.pk).update(hidden_at=self.now)
        self.assertEqual(self.visit().status_code,404)
        row=self.browser().get('/account/?tab=recent').context['contributions'][0]
        self.assertFalse(row['available']);self.assertNotIn('title',row['work'])
        Work.objects.filter(pk=self.work.pk).update(hidden_at=None)
        Activity.objects.filter(pk=self.period.pk).update(status='draft',started_at=None)
        self.assertEqual(self.visit(period=self.period).status_code,404)

    def test_retention_orders_before_pagination_and_discards_only_oldest_owner_rows(self):
        works=[Work.objects.create(original_submitted_title=f'浏览合成{i}') for i in range(101)]
        RecentWorkView.objects.bulk_create([RecentWorkView(account=self.owners['alice'],work=w,
            viewed_at=self.now-timedelta(minutes=102-i)) for i,w in enumerate(works[:100])])
        self.visit(works[-1]);self.assertEqual(RecentWorkView.objects.count(),50)
        self.assertFalse(RecentWorkView.objects.filter(work=works[0]).exists())
        first=self.browser().get('/account/?tab=recent').context
        second=self.browser().get('/account/?tab=recent&page=2').context
        self.assertEqual(first['total'],50);self.assertEqual(len(first['contributions']),12)
        self.assertEqual(first['contributions'][0]['work']['id'],works[-1].pk)
        self.assertEqual(len(second['contributions']),12)
        self.assertEqual(self.browser().get('/account/?tab=recent&page=99').url,'/account/?tab=recent&page=5')

    def test_history_honors_ended_snapshot_and_preserves_profile_return(self):
        self.submit()
        Activity.objects.filter(pk=self.period.pk).update(status='ended',ended_at=self.now)
        self.period.refresh_from_db();self.visit(period=self.period)
        Work.objects.filter(pk=self.work.pk).update(original_submitted_title='当前已变更名称',source_metadata={'manual_retired_at':self.now.isoformat()})
        entry=self.browser().get('/account/?tab=recent').context['contributions'][0]
        self.assertEqual(entry['work']['title'],'第一本合成作品')
        detail=self.browser().get(entry['url'])
        self.assertEqual(detail.context['return_url'],'/account/?tab=recent')

    def test_participation_counts_only_own_effective_activity_relations(self):
        self.submit();self.submit(self.second,who='bob')
        activities.like(self.period.pk,self.second.pk,self.owners['alice'].principal,True)
        books.set_relation(self.work,self.owners['alice'].principal,'recommend',True)
        books.set_relation(self.work,self.owners['alice'].principal,'like',True)
        groups=self.browser().get('/account/?tab=activities').context
        self.assertEqual(groups['activities_total'],1)
        group=groups['participated_activities'][0]
        self.assertEqual((group['recommended_count'],group['liked_count']),(1,1))
        details=self.browser().get(f'/account/?tab=activities&activity={self.period.pk}').context
        self.assertEqual(details['total'],2)
        flags={e['work']['id']:(e['recommended'],e['liked']) for e in details['contributions']}
        self.assertEqual(flags,{self.work.pk:(True,False),self.second.pk:(False,True)})
        activities.like(self.period.pk,self.second.pk,self.owners['alice'].principal,False)
        activities.withdraw(self.period.pk,self.work.pk,self.owners['alice'].principal)
        self.assertEqual(self.browser().get('/account/?tab=activities').context['activities_total'],0)
        self.assertEqual(self.browser().get(f'/account/?tab=activities&activity={self.period.pk}').status_code,404)

    def test_participation_includes_history_snapshots_but_not_hidden_or_invalid(self):
        self.submit();Activity.objects.filter(pk=self.period.pk).update(status='ended',ended_at=self.now)
        Work.objects.filter(pk=self.work.pk).update(original_submitted_title='现在的名称',source_metadata={'manual_retired_at':self.now.isoformat()})
        page=self.browser().get(f'/account/?tab=activities&activity={self.period.pk}')
        self.assertContains(page,'第一本合成作品');self.assertNotContains(page,'现在的名称')
        self.assertEqual(page.context['contributions'][0]['scope'],self.period.title)
        Recommendation.objects.filter(activity=self.period).update(invalidated_at=self.now)
        self.assertEqual(self.browser().get('/account/?tab=activities').context['activities_total'],0)

    def test_participation_rejects_other_accounts_or_cross_tab_parameters(self):
        self.submit(who='bob')
        self.assertEqual(self.browser().get(f'/account/?tab=activities&activity={self.period.pk}').status_code,404)
        self.assertEqual(self.browser().get(f'/account/?tab=recent&activity={self.period.pk}').status_code,400)
        self.assertEqual(self.browser().get('/account/?tab=activities&activity=0').status_code,400)
        self.assertEqual(self.browser().get('/account/?tab=activities&activity=1&activity=2').status_code,400)

    def test_ui_exposes_three_features_without_public_history_endpoints(self):
        self.assertTrue(Account.objects.get(pk=self.owners['alice'].pk).track_recent_views)
        self.submit();page=self.browser().get('/account/')
        for word in ('最近浏览','我参与的活动','正文大小','较大','更大'):self.assertContains(page,word)
        recent=self.browser().get('/account/?tab=recent')
        self.assertContains(recent,'最近50部作品');self.assertNotContains(recent,'随账户同步')
        self.assertContains(recent,'data-recent-tracking checked')
        self.assertEqual(self.browser(None).get('/account/?tab=recent').status_code,302)
        self.assertEqual(self.browser(None).get('/account/?tab=activities').status_code,302)
        self.assertContains(self.browser().get(f'/works/{self.work.pk}/?activity={self.period.pk}'),'data-recent-work')
        self.assertNotContains(self.browser(None).get(f'/works/{self.work.pk}/?activity={self.period.pk}'),'data-recent-work')
