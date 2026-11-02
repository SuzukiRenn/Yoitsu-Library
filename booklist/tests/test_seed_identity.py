import copy
from datetime import timedelta,timezone as datetime_timezone
from django.conf import settings
from django.test import Client,TestCase,override_settings
from django.utils import timezone
from booklist import accounts,activities,services,public_booklist as books,activity_export
from booklist.seed_identity import make_plan,replace,digest,fingerprints,PROTECTED,date
from booklist.models import (Work,Activity,Recommendation,PublicRecommendation,PublicLike,Vote,ReasonLike,
    Notification,ImportedPrincipal,AnonymousClient,Account)


@override_settings(REQUIRE_ACCOUNT=True)
class ContentSeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tokens={};cls.people={}
        for name in ('seedalice','seedbob'):
            token,session=accounts.register(name,name,'Seed-Identity!Testing2026')
            cls.tokens[name]=token;cls.people[name]=session.account
        cls.admin=services.initialize_admin('seedadmin','Seed-Identity!Admin2026')
        now=timezone.now();cls.now=now
        cls.work=Work.objects.create(wenku8_book_id=1001,identity_status='matched',wenku8_title='作品甲',wenku8_author='作者甲',public_added_at=now)
        cls.other=Work.objects.create(wenku8_book_id=1002,identity_status='matched',wenku8_title='作品乙',wenku8_author='作者乙')
        cls.target=Activity.objects.create(title='十一月份推荐',status='active',created_by=cls.admin,
            start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        for person in cls.people.values():books.set_relation(cls.work,person.principal,'recommend',True)
        books.set_reason(cls.work,cls.people['seedalice'].principal,'需要清除的旧理由')
        books.set_relation(cls.work,cls.people['seedbob'].principal,'like',True)
        ReasonLike.objects.create(recommendation=books.reasons().get(),anonymous_client=cls.people['seedbob'].principal)
        activities.submit(cls.target.pk,cls.people['seedalice'].principal,{'work_id':cls.work.pk,'title':'作品甲',
            'reason':'旧活动理由','confirmed_unmatched':False,'copy_public_reason':False})

    def manifest(self):
        def work(source_id,w):
            return {'source_id':source_id,'fields':{'wenku8_book_id':w.wenku8_book_id,
                'identity_status':'matched','wenku8_title':w.wenku8_title,'wenku8_author':w.wenku8_author,
                'original_submitted_title':None,'title_override':'','title_overridden':False,
                'author_override':'','author_overridden':False,'source_metadata':{'edition':'book'}},'tags':[],'aliases':[]}
        def reason(text,refs=None):return {'text':text,'spoilers':True,'references':refs or []}
        snap={'snapshot_title':'作品甲','snapshot_author':'作者甲','snapshot_cover_url':'','snapshot_description':'冻结简介'}
        def period(pk,title,status,start,end):
            return {'source_id':pk,'fields':{'title':title,'theme':'','status':status,'start_at':start.isoformat(),
                'scheduled_end_at':end.isoformat(),'started_at':start.isoformat(),'ended_at':end.isoformat() if status=='ended' else None,
                'cancelled_at':None,'end_reason':'deadline' if status=='ended' else '', 'cancel_reason':'','corrected_at':None},
                'entries':[{'work_id':9001,'snapshot':snap,'tags':[],'reasons':[reason('第一条'),reason('第二条')]}]}
        return {'format':1,'source':'anonymous_public_content','mapping':{'source_current_title':'最喜欢的三本',
            'target_current_title':'十一月份推荐','renamed_current_title':'最喜欢的三本'},
            'works':[work(9001,self.work),work(9002,self.other)],
            'public':[{'work_id':9001,'reasons':[reason('推荐作品乙',[{'work_id':9002,'start':2,'end':5}]),reason('另一个理由')]},
                      {'work_id':9002,'reasons':[]}],
            'activities':[period(99,'最喜欢的三本','active',self.now-timedelta(hours=2),self.now+timedelta(days=1)),
                          period(88,'十月份推荐','ended',self.now-timedelta(days=10),self.now-timedelta(days=9))]}

    def test_replace_all_scopes_preserves_accounts_and_multiple_reasons_use_existing_counts(self):
        before=fingerprints(PROTECTED);original_start=self.target.start_at
        result=replace(self.manifest(),'a'*64)
        self.assertTrue(result['success']);self.assertEqual(fingerprints(PROTECTED),before)
        self.target.refresh_from_db();self.assertEqual(self.target.title,'最喜欢的三本');self.assertEqual(self.target.start_at,original_start)
        self.assertEqual(PublicRecommendation.objects.count(),2)
        self.assertEqual(Recommendation.objects.filter(scope='public').count(),2)
        self.assertFalse(PublicLike.objects.exists());self.assertFalse(Vote.objects.exists());self.assertFalse(ReasonLike.objects.exists());self.assertFalse(Notification.objects.exists())
        self.assertFalse(Recommendation.objects.filter(reason__contains='旧').exists())
        self.assertEqual(books.serialize([self.work])[0]['recommendation_count'],1)
        self.assertIsNone(books.serialize([self.work])[0]['first_recommender'])
        self.assertTrue(all(r['author'] is None for r in books.serialize([self.work])[0]['reasons']))
        for aid in result['activity_map'].values():
            row=activities.listing(aid)['results'][0]
            self.assertEqual((row['recommendation_count'],row['like_count'],row['reason_count']),(2,0,2))
            self.assertEqual(activity_export.snapshot(aid)['works'][0]['reason_count'],2)
            self.assertTrue(all(r['author'] is None for r in row['reasons']))
        ref=books.serialize([self.work])[0]['reasons'][0]['parts'][1]
        self.assertEqual(ref['url'],f'/works/{self.other.pk}/')
        browser=Client();browser.cookies[settings.ACCOUNT_COOKIE]=self.tokens['seedalice']
        html=browser.get(f'/works/{self.work.pk}/')
        self.assertNotContains(html,'历史匿名推荐者');self.assertNotContains(html,'class="reason-author"')
        self.assertFalse(html.context['interaction']['recommended']);self.assertFalse(html.context['interaction']['liked'])
        self.assertFalse(Account.objects.filter(principal_id__in=ImportedPrincipal.objects.values('principal_id')).exists())
        books.set_relation(self.work,self.people['seedbob'].principal,'recommend',True)
        self.assertEqual(books.serialize([self.work])[0]['recommendation_count'],2)
        self.assertEqual(books.serialize([self.work])[0]['first_recommender']['display_name'],'seedbob')
        self.assertFalse(Notification.objects.exists())

    def test_preview_is_rolled_back_and_live_apply_is_checksum_bound(self):
        doc=self.manifest();models=(Recommendation,PublicRecommendation,PublicLike,ReasonLike,Notification,Activity,ImportedPrincipal,AnonymousClient)
        before=fingerprints(models)
        plan=make_plan(doc)
        result=replace(doc,'b'*64,expected_plan=digest(plan),preview=True)
        self.assertTrue(result['preview']);self.assertEqual(fingerprints(models),before)
        self.assertTrue(replace(doc,'b'*64,expected_plan=result['plan_sha256'])['success'])

    def test_stale_plan_and_repeated_batch_never_clear_again(self):
        doc=self.manifest();plan=make_plan(doc)
        PublicLike.objects.create(work=self.other,anonymous_client=self.people['seedalice'].principal)
        with self.assertRaisesRegex(ValueError,'changed since preview'):replace(doc,'c'*64,expected_plan=digest(plan))
        self.assertTrue(PublicLike.objects.exists())
        replace(doc,'c'*64)
        books.set_relation(self.work,self.people['seedbob'].principal,'recommend',True)
        with self.assertRaisesRegex(ValueError,'already applied'):replace(doc,'c'*64)
        self.assertEqual(books.recommendations().filter(work=self.work).count(),2)

    def test_invalid_reason_rolls_back_the_entire_clear(self):
        doc=self.manifest();doc['public'][0]['reasons'][0]['text']='x'*201
        before=fingerprints((Recommendation,PublicRecommendation,PublicLike,ReasonLike,Notification,Activity))
        with self.assertRaises(AssertionError):replace(doc,'d'*64)
        self.assertEqual(fingerprints((Recommendation,PublicRecommendation,PublicLike,ReasonLike,Notification,Activity)),before)

    def test_source_sqlite_utc_timestamps_do_not_shift_eight_hours(self):
        parsed=date('2026-10-21 01:00:00')
        self.assertEqual(parsed.isoformat(),'2026-10-21T01:00:00+00:00')

    def test_theme_without_reason_still_has_one_recommendation(self):
        doc=self.manifest()
        for period in doc['activities']:period['entries'][0]['reasons']=[]
        result=replace(doc,'e'*64)
        for aid in result['activity_map'].values():
            row=activities.listing(aid)['results'][0]
            self.assertEqual((row['recommendation_count'],row['reason_count']),(1,0))

    def test_missing_manual_catalog_work_is_created_without_source_owner(self):
        doc=self.manifest()
        f=doc['works'][1]['fields']
        f.update(wenku8_book_id=None,identity_status='unmatched',wenku8_title=None,
                 original_submitted_title='来源新增手工作品',author_override='来源作者',author_overridden=True)
        doc['works'][1]['tags']=['新标签'];doc['works'][1]['aliases']=['新增作品别名']
        original=self.other.wenku8_title
        replace(doc,'f'*64)
        row=Work.objects.get(original_submitted_title='来源新增手工作品')
        self.assertEqual(books.serialize([row])[0]['recommendation_count'],1)
        self.assertIsNone(books.serialize([row])[0]['first_recommender'])
        self.assertTrue(row.workalias_set.filter(alias='新增作品别名').exists())
        self.other.refresh_from_db();self.assertEqual(self.other.wenku8_title,original)
        ref=books.serialize([self.work])[0]['reasons'][0]['parts'][1]
        self.assertEqual(ref['url'],f'/works/{row.pk}/')
