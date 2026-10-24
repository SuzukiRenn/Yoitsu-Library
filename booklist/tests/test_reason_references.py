from datetime import timedelta
import json,uuid
from unittest.mock import patch
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from booklist import services,activities,moderation,merging,public_booklist as books
from booklist.reason_references import validate
from booklist.models import Work,Recommendation,Activity,PendingActivitySubmission


class ReasonReferenceTests(TestCase):
    def setUp(self):
        token,self.actor=services.issue_anonymous();self.client.cookies[settings.ANONYMOUS_COOKIE]=token
        self.admin=services.initialize_admin('referenceowner','References-Testing!Password-49')
        self.work=Work.objects.create(original_submitted_title='被推荐的作品')
        self.target=Work.objects.create(original_submitted_title='龙王的工作！')
        books.set_relation(self.work,self.actor,'recommend',True)
        self.text='喜欢'+self.target.display_title+'的话可以试试。'
        self.refs=[{'work_id':self.target.pk,'start':2,'end':2+len(self.target.display_title)}]
        now=timezone.now();self.activity=Activity.objects.create(title='引用主题',created_by=self.admin,status='active',start_at=now-timedelta(hours=1),started_at=now-timedelta(hours=1),scheduled_end_at=now+timedelta(days=1))

    def post(self,url,data):
        return self.client.post(url,json.dumps(data),content_type='application/json',HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))

    def test_public_reference_roundtrip_and_safe_html(self):
        url=f'/api/v1/public/works/{self.work.pk}/reason/'
        self.assertEqual(self.post(url,{'reason':self.text,'references':self.refs}).status_code,200)
        row=books.reasons().get();self.assertEqual(row.reason_references,self.refs);self.assertEqual(row.reason,self.text)
        html=self.client.get(f'/works/{self.work.pk}/').content.decode()
        self.assertIn(f'class="reason-reference" href="/works/{self.target.pk}/"',html)
        data=self.client.get(f'/api/v1/public/works/{self.work.pk}/reasons/').json()['results'][0]
        self.assertEqual(data['parts'][1]['url'],f'/works/{self.target.pk}/')
        edit=self.client.get(f'/recommend/?work={self.work.pk}&edit=1')
        self.assertContains(edit,'reason-reference-data')
        self.assertEqual(self.post(url,{'reason':self.text}).status_code,200)
        row.refresh_from_db();self.assertEqual(row.reason_references,self.refs)
        self.post(url,{'reason':'普通文字'});row.refresh_from_db();self.assertEqual(row.reason_references,[])

    def test_invalid_external_overlapping_and_false_label_rejected(self):
        url=f'/api/v1/public/works/{self.work.pk}/reason/'
        bad=[ [{'work_id':self.target.pk,'start':0,'end':1}], [dict(self.refs[0],href='https://evil.test')], self.refs*2, [{'work_id':True,'start':2,'end':8}], [{'work_id':999999,'start':2,'end':8}], self.refs*9 ]
        for refs in bad:self.assertEqual(self.post(url,{'reason':self.text,'references':refs}).status_code,400)
        self.assertFalse(books.reasons().exists())
        self.assertEqual(self.post(url,{'reason':'x'*201,'references':[]}).status_code,400)
        self.assertEqual(self.post(url,{'reason':'x'*200,'references':[]}).status_code,200)

    def test_unicode_positions_and_escaping(self):
        self.target.original_submitted_title='<书名😀>';self.target.save()
        text='😀'+self.target.display_title
        refs=[{'work_id':self.target.pk,'start':1,'end':len(text)}]
        books.set_reason(self.work,self.actor,text,references=refs)
        html=self.client.get(f'/works/{self.work.pk}/').content.decode()
        self.assertIn('&lt;书名😀&gt;</a>',html);self.assertNotIn('><书名😀>',html)

    def test_rename_hidden_merge_and_retirement_keep_original_text(self):
        books.set_reason(self.work,self.actor,self.text,references=self.refs)
        self.target.original_submitted_title='改名后的书';self.target.save()
        self.assertContains(self.client.get(f'/works/{self.work.pk}/'),self.text[2:8])
        self.target.hidden_at=timezone.now();self.target.save()
        html=self.client.get(f'/works/{self.work.pk}/').content.decode()
        self.assertNotIn(f'class="reason-reference" href="/works/{self.target.pk}/"',html)
        self.assertIn(self.text,html)
        self.assertEqual(self.post(f'/api/v1/public/works/{self.work.pk}/reason/',{'reason':self.text,'references':self.refs}).status_code,200)
        self.target.hidden_at=None;self.target.source_metadata={'manual_retired_at':'2026-10-24'};self.target.save()
        data=self.client.get(f'/api/v1/public/works/{self.work.pk}/reasons/').json()['results'][0]
        self.assertFalse(any(part.get('url') for part in data['parts']))

    def test_spoiler_reference_stays_inside_hidden_reason(self):
        books.set_reason(self.work,self.actor,self.text,True,self.refs)
        html=self.client.get('/').content.decode()
        self.assertIn('tabindex="-1" hidden>喜欢<a class="reason-reference"',html)
        self.assertNotIn('href="/works/'+str(self.target.pk)+'/"',html.split('data-spoiler-text')[0])

    def test_activity_copy_pending_and_history_preserve_references(self):
        data={'work_id':self.work.pk,'title':self.work.display_title,'reason':self.text,'references':self.refs,'contains_spoilers':True,'confirmed_unmatched':False,'copy_public_reason':True}
        self.assertEqual(self.post(f'/api/v1/activities/{self.activity.pk}/recommendations/',data).status_code,200)
        self.assertEqual(activities.recs(self.activity.pk).get().reason_references,self.refs)
        self.assertEqual(books.reasons().get().reason_references,self.refs)
        pending=Work.objects.create(original_submitted_title='待核引用',review_status='pending')
        with patch('booklist.public_booklist.resolve_submission',return_value=pending):
            activities.submit(self.activity.pk,self.actor,{**data,'work_id':pending.pk,'title':pending.display_title})
        self.assertEqual(PendingActivitySubmission.objects.get().reason_references,self.refs)
        moderation.approve(self.admin,pending.pk,'核查通过')
        self.assertEqual(activities.recs(self.activity.pk).get(work=pending).reason_references,self.refs)
        self.assertEqual(books.reasons().get(work=pending).reason_references,self.refs)
        self.activity.status='ended';self.activity.ended_at=timezone.now();self.activity.save()
        page=self.client.get(f'/works/{self.work.pk}/?activity={self.activity.pk}')
        self.assertContains(page,'class="reason-reference"')
        from booklist.activity_export import snapshot
        self.assertNotIn(self.target.display_title,json.dumps(snapshot(self.activity.pk),ensure_ascii=False))

    def test_merge_undo_preserves_reference_metadata(self):
        secondary=Work.objects.create(original_submitted_title='被合并作品')
        books.set_relation(secondary,self.actor,'recommend',True)
        books.set_reason(secondary,self.actor,self.text,references=self.refs)
        proposal=merging.plan(self.work.pk,secondary.pk)
        operation=merging.merge(self.admin,self.work.pk,secondary.pk,{},proposal['version'],'重复记录')
        self.assertEqual(books.reasons().get(work=self.work).reason_references,self.refs)
        merging.undo(self.admin,operation.pk,'撤销测试')
        self.assertEqual(books.reasons().get(work=secondary).reason_references,self.refs)

    def test_cannot_edit_reason_without_recommending_and_no_implicit_links(self):
        work=Work.objects.create(original_submitted_title='未推荐')
        self.assertEqual(self.post(f'/api/v1/public/works/{work.pk}/reason/',{'reason':self.text,'references':self.refs}).status_code,404)
        books.set_reason(self.work,self.actor,self.text)
        self.assertNotContains(self.client.get(f'/works/{self.work.pk}/'),'class="reason-reference"')
