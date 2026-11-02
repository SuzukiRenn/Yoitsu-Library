import json
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.conf import settings
from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from booklist import accounts, activities, public_booklist as books, services, merging
from booklist.models import Account, Activity, Notification, WorkLikeEvent, Work, ReasonLike, Recommendation


@override_settings(REQUIRE_ACCOUNT=True)
class InteractionNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.people={}; cls.tokens={}
        for name in ('alice','bob','carol'):
            token, session=accounts.register(name, name, 'Notification-Testing!2026')
            cls.people[name]=session.account; cls.tokens[name]=token
        cls.work=Work.objects.create(original_submitted_title='通知测试', public_added_at=timezone.now())
        admin=services.initialize_admin('noticeadmin','Notice-Admin!Testing2026')
        now=timezone.now()
        cls.activity=Activity.objects.create(title='独立主题', status='active', created_by=admin,
            start_at=now-timedelta(hours=1), started_at=now-timedelta(hours=1), scheduled_end_at=now+timedelta(days=1))
        cls.admin=admin

    def browser(self,name):
        client=Client(enforce_csrf_checks=True)
        client.cookies[settings.ACCOUNT_COOKIE]=self.tokens[name]
        return client

    def post(self,name,path,data,key=None):
        client=self.browser(name)
        csrf=client.get('/api/v1/csrf/').json()['csrf_token']
        return client.post(path,json.dumps(data),content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf,HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def recommend(self,name,activity=False):
        if activity:
            return activities.submit(self.activity.pk,self.people[name].principal,{'work_id':self.work.pk,
                'title':self.work.display_title,'reason':'秘密理由','contains_spoilers':True,
                'confirmed_unmatched':False,'copy_public_reason':False})
        return books.set_relation(self.work,self.people[name].principal,'recommend',True)

    def like_reason(self,name,reason,enabled=True,key=None):
        prefix=f'/api/v1/activities/{self.activity.pk}' if reason.activity_id else '/api/v1/public'
        return self.post(name,f'{prefix}/works/{self.work.pk}/reasons/{reason.pk}/like/',{'enabled':enabled},key)

    def test_co_recommendations_notify_existing_readers_and_dedup_rejoin(self):
        self.recommend('alice')
        self.assertFalse(Notification.objects.exists())
        self.recommend('bob')
        note=Notification.objects.get()
        self.assertEqual((note.recipient_id,note.event.actor_id,note.event.kind),
            (self.people['alice'].pk,self.people['bob'].pk,'co_recommendation'))
        books.set_reason(self.work,self.people['bob'].principal,'编辑理由')
        self.recommend('bob')
        books.set_relation(self.work,self.people['bob'].principal,'recommend',False)
        self.recommend('carol')
        self.recommend('bob')
        self.assertEqual(Notification.objects.count(),2)
        self.assertFalse(Notification.objects.filter(recipient=self.people['bob']).exists())
        api=self.browser('alice').get('/api/v1/notifications/').json()
        self.assertEqual(api['results'][0]['kind'],'co_recommendation')
        page=self.browser('alice').get('/notifications/')
        self.assertContains(page,'也推荐了你推荐的《通知测试》')

    def test_reason_author_only_self_like_cancel_repeat_read_and_relogin(self):
        self.recommend('alice'); self.recommend('carol')
        books.set_reason(self.work,self.people['alice'].principal,'秘密剧透正文',True)
        reason=books.reasons().get()
        key=str(uuid.uuid4())
        for enabled in (True,True,False,True):
            self.assertEqual(self.like_reason('bob',reason,enabled,key if enabled and not ReasonLike.objects.exists() else None).status_code,200)
        self.assertEqual(self.like_reason('alice',reason).status_code,200)
        query=Notification.objects.filter(event__kind='reason_like')
        self.assertEqual(query.count(),1)
        note=query.get()
        self.assertEqual(note.recipient_id,self.people['alice'].pk)
        self.assertEqual(ReasonLike.objects.filter(cancelled_at__isnull=True).count(),2)
        self.assertEqual(books.likes().count(),0)
        self.assertEqual(self.browser('alice').get(f'/notifications/{note.pk}/work/').url,f'/works/{self.work.pk}/#reasons')
        self.assertEqual(self.browser('carol').get(f'/notifications/{note.pk}/work/').status_code,404)
        self.assertNotContains(self.browser('alice').get('/notifications/'),'秘密剧透正文')
        before=list(ReasonLike.objects.order_by('pk').values())
        self.post('alice','/api/v1/notifications/read/',{'ids':[note.pk]})
        self.assertEqual(before,list(ReasonLike.objects.order_by('pk').values()))
        token,session=accounts.session(self.people['alice'])
        self.tokens['alice']=token
        self.assertTrue(self.browser('alice').get('/api/v1/notifications/').json()['results'][0]['read'])
        self.like_reason('bob',reason,False); self.like_reason('bob',reason)
        self.assertEqual(query.count(),1)

    def test_scope_separation_snapshot_title_and_activity_reason_target(self):
        self.recommend('alice',True); self.recommend('bob',True)
        notes=Notification.objects.filter(recipient=self.people['alice'],event__kind='co_recommendation')
        self.assertEqual(set(notes.values_list('event__scope_key',flat=True)),{'public',f'activity:{self.activity.pk}'})
        self.work.title_override='改后的书名'; self.work.title_overridden=True; self.work.save()
        reason=activities.recs(self.activity.pk).get(anonymous_client=self.people['alice'].principal)
        self.assertEqual(self.like_reason('carol',reason).status_code,200)
        note=Notification.objects.get(event__kind='reason_like')
        self.assertEqual(note.work_title,'通知测试')
        self.assertEqual(self.browser('alice').get(f'/notifications/{note.pk}/work/').url,
            f'/works/{self.work.pk}/?activity={self.activity.pk}#reasons')
        self.assertEqual(self.like_reason('alice',reason).status_code,200)
        self.assertEqual(Notification.objects.filter(event__kind='reason_like').count(),1)

    def test_disabled_withdrawn_revoked_and_legacy_recipients_excluded(self):
        self.recommend('alice'); self.recommend('bob')
        Notification.objects.all().delete()
        Account.objects.filter(pk=self.people['alice'].pk).update(is_active=False)
        self.people['bob'].principal.revoked_at=timezone.now(); self.people['bob'].principal.save()
        self.recommend('carol')
        self.assertFalse(Notification.objects.exists())
        _,legacy=services.issue_anonymous()
        books.set_relation(self.work,legacy,'recommend',True)
        self.assertFalse(Notification.objects.exists())

    def test_rejected_reason_requests_and_disabled_author_do_not_notify(self):
        self.recommend('alice')
        books.set_reason(self.work,self.people['alice'].principal,'待隐藏正文')
        reason=books.reasons().get()
        Account.objects.filter(pk=self.people['alice'].pk).update(is_active=False)
        self.assertEqual(self.like_reason('bob',reason).status_code,200)
        self.assertFalse(Notification.objects.exists())
        Recommendation.objects.filter(pk=reason.pk).update(text_hidden_at=timezone.now())
        self.assertEqual(self.like_reason('carol',reason).status_code,404)
        self.assertFalse(ReasonLike.objects.filter(anonymous_client=self.people['carol'].principal).exists())

    def test_both_new_notification_writes_roll_back_with_interaction(self):
        self.recommend('alice')
        path=f'/api/v1/public/works/{self.work.pk}/recommend/'
        with patch('booklist.notifications.co_recommended',side_effect=OperationalError('synthetic')):
            self.assertEqual(self.post('bob',path,{'enabled':True}).status_code,503)
        self.assertFalse(books.recommendations().filter(anonymous_client=self.people['bob'].principal).exists())
        books.set_reason(self.work,self.people['alice'].principal,'理由')
        reason=books.reasons().get()
        with patch('booklist.notifications.reason_liked',side_effect=OperationalError('synthetic')):
            self.assertEqual(self.like_reason('bob',reason).status_code,503)
        self.assertFalse(ReasonLike.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertEqual(self.like_reason('bob',reason).status_code,200)
        self.assertEqual(Notification.objects.count(),1)

    def test_co_dedup_survives_merge_and_undo(self):
        secondary=Work.objects.create(original_submitted_title='副作品',public_added_at=timezone.now())
        books.set_relation(secondary,self.people['alice'].principal,'recommend',True)
        books.set_relation(secondary,self.people['bob'].principal,'recommend',True)
        self.recommend('alice')
        proposal=merging.plan(self.work.pk,secondary.pk)
        operation=merging.merge(self.admin,self.work.pk,secondary.pk,{},proposal['version'],'通知测试合并')
        books.set_relation(self.work,self.people['bob'].principal,'recommend',False)
        self.recommend('bob')
        self.assertEqual(Notification.objects.count(),1)
        # Interaction changes correctly block merge undo; notification history stays original.
        self.assertEqual(Notification.objects.get().event.work_id,secondary.pk)

    def test_co_dedup_survives_unmodified_merge_undo(self):
        self.recommend('alice'); self.recommend('bob')
        secondary=Work.objects.create(original_submitted_title='撤销副作品',public_added_at=timezone.now())
        books.set_relation(secondary,self.people['alice'].principal,'recommend',True)
        proposal=merging.plan(self.work.pk,secondary.pk)
        operation=merging.merge(self.admin,self.work.pk,secondary.pk,{},proposal['version'],'通知合并')
        merging.undo(self.admin,operation.pk,'通知撤销')
        books.set_relation(self.work,self.people['bob'].principal,'recommend',False)
        self.recommend('bob')
        self.assertEqual(Notification.objects.count(),1)
