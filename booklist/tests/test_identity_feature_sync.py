import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import timedelta

from django.conf import settings
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from booklist import accounts, activities, public_booklist as books, services
from booklist.models import (Account, AccountSession, Work, PublicRecommendation, PublicLike,
    Recommendation, Activity, Notification, WorkLikeEvent, Tag)
from scripts.import_catalog_increment import import_increment


@override_settings(REQUIRE_ACCOUNT=True)
class IdentityFeatureSyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tokens={};cls.people={}
        for name in ['syncalice','syncbob']:
            token,session=accounts.register(name,'读者'+name[-1],'Identity-Sync!Testing-2026')
            cls.tokens[name]=token;cls.people[name]=session.account
        cls.web=Work.objects.create(original_submitted_title='同步书目',source_metadata={'edition':'web'})
        cls.target=Work.objects.create(original_submitted_title='引用的作品')
        books.enroll(cls.web)
        books.set_relation(cls.web,cls.people['syncalice'].principal,'recommend',True)
        books.set_relation(cls.web,cls.people['syncbob'].principal,'like',True)
        cls.admin=services.initialize_admin('syncowner','Identity-Sync!Admin-2026')
        now=timezone.now()
        cls.activity=Activity.objects.create(title='身份版主题',created_by=cls.admin,status='active',
            start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))
        activities.submit(cls.activity.pk,cls.people['syncbob'].principal,{'work_id':cls.web.pk,
            'title':cls.web.display_title,'reason':'主题理由','confirmed_unmatched':False,'copy_public_reason':False})

    def browser(self,name):
        client=Client();client.cookies[settings.ACCOUNT_COOKIE]=self.tokens[name];return client

    def test_personal_filters_follow_account_across_devices_and_keep_scope(self):
        url='/api/v1/public/works/?recommended=1'
        for _ in range(2):
            rows=self.browser('syncalice').get(url).json()['results']
            self.assertEqual([r['id'] for r in rows],[self.web.pk])
        self.assertEqual(self.browser('syncalice').get('/api/v1/public/works/?liked=1').json()['results'],[])
        rows=self.browser('syncbob').get('/api/v1/public/works/?liked=1').json()['results']
        self.assertEqual([r['id'] for r in rows],[self.web.pk])
        self.assertEqual(self.browser('syncalice').get(f'/api/v1/activities/{self.activity.pk}/?recommended=1').json()['results'],[])
        self.assertContains(self.browser('syncalice').get('/'),'当前账户')

    def test_references_spoilers_and_attribution_coexist_without_numbering(self):
        text='推荐'+self.target.display_title
        refs=[{'work_id':self.target.pk,'start':2,'end':len(text)}]
        client=self.browser('syncalice')
        response=client.post(f'/api/v1/public/works/{self.web.pk}/reason/',
            json.dumps({'reason':text,'references':refs,'contains_spoilers':True}),
            content_type='application/json',HTTP_IDEMPOTENCY_KEY='sync-reference-write')
        self.assertEqual(response.status_code,200,response.content)
        row=client.get(f'/api/v1/public/works/{self.web.pk}/reasons/').json()['results'][0]
        self.assertEqual(row['author']['display_name'],self.people['syncalice'].display_name)
        self.assertTrue(row['contains_spoilers'])
        self.assertEqual(row['parts'][1]['url'],f'/works/{self.target.pk}/')
        page=client.get(f'/works/{self.web.pk}/')
        self.assertContains(page,'data-recommenders')
        self.assertContains(page,'reason-author')
        self.assertContains(page,'data-spoiler-reveal')
        self.assertNotContains(page,'reason-number')
        self.assertNotContains(page,'@syncalice')
        self.assertContains(client.get('/account/'),'class="edition-badge"')
        self.assertNotContains(page,'class="edition-badge"')

    def manifest(self,folder):
        row={'fields':{'original_submitted_title':'增补书目','title_override':'','title_overridden':False,
            'author_override':'新作者','author_overridden':True,'description_override':'新简介',
            'description_overridden':True,'cover_url_override':'https://example.test/cover.jpg','cover_url_overridden':True},
            'source_metadata':{'source_provider':'bilinovel','bili_id':987654,'edition':'web','approved_tags':['华文轻小说']},
            'aliases':[{'alias':'增补别名','normalized_alias':'增补别名'}],'tags':['华文轻小说']}
        path=Path(folder)/'manifest.json'
        path.write_text(json.dumps({'format':1,'count':1,'works':[row]}),encoding='utf-8')
        return path,hashlib.sha256(path.read_bytes()).hexdigest(),row

    @patch('scripts.sqlite_backup.backup',return_value={'status':'verified','restore_drill':True})
    def test_increment_preserves_accounts_interactions_and_is_idempotent(self,backup):
        before={model.__name__:list(model.objects.values().order_by('pk')) for model in
            [Account,AccountSession,PublicRecommendation,PublicLike,Recommendation,Notification,WorkLikeEvent]}
        with TemporaryDirectory() as folder:
            path,digest,row=self.manifest(folder)
            self.assertEqual(import_increment(path,digest,folder)['would_create'],1)
            receipt=import_increment(path,digest,folder,apply=True)
            self.assertEqual(receipt['created_works'],1)
            self.assertTrue(receipt['preexisting_rows_unchanged'])
            self.assertEqual(import_increment(path,digest,folder)['would_create'],0)
            work=Work.objects.get(source_metadata__bili_id=987654)
            self.assertIsNone(work.wenku8_book_id)
            self.assertEqual(work.source_metadata['edition'],'web')
            for model in [Account,AccountSession,PublicRecommendation,PublicLike,Recommendation,Notification,WorkLikeEvent]:
                self.assertEqual(list(model.objects.values().order_by('pk')),before[model.__name__])
            work.title_override='Edited after import';work.title_overridden=True;work.save()
            with self.assertRaisesRegex(AssertionError,'fields differ'):
                import_increment(path,digest,folder,apply=True)

    @patch('scripts.sqlite_backup.backup',return_value={'status':'verified'})
    def test_failed_increment_rolls_back_without_partial_metadata(self,backup):
        with TemporaryDirectory() as folder:
            path,digest,row=self.manifest(folder)
            Tag.objects.create(name='华文轻小说',normalized_name='华文轻小说',is_active=False)
            before=Work.objects.count()
            with self.assertRaisesRegex(AssertionError,'Ambiguous/inactive'):
                import_increment(path,digest,folder,apply=True)
            self.assertEqual(Work.objects.count(),before)
            with self.assertRaisesRegex(AssertionError,'checksum'):
                import_increment(path,'0'*64,folder,apply=True)
