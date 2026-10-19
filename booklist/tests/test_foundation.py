from datetime import timedelta
import importlib
import io
import json
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import F
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from booklist import services
from booklist.models import *
from booklist.text import normalize
from booklist.protocol import pagination, utc_milliseconds

PASSWORD = 'Stable-Horse!Password-7-Foundation'


class SchemaTests(TestCase):
    def setUp(self):
        self.admin = services.initialize_admin('owner', PASSWORD)
        _, self.anon = services.issue_anonymous()
        self.work = Work.objects.create(original_submitted_title='作品', public_added_at=timezone.now())
        self.activity = Activity.objects.create(title='活动', start_at=timezone.now(), scheduled_end_at=timezone.now()+timedelta(days=1), created_by=self.admin)
        self.aw = ActivityWork.objects.create(activity=self.activity, work=self.work, snapshot_title='作品')

    def reject(self, fn):
        with self.assertRaises(IntegrityError), transaction.atomic():
            fn()
            # Django creates deferrable SQLite FKs: verify inside this savepoint
            # so a bad row rolls back, rather than leaking to TestCase teardown.
            connection.check_constraints()

    def test_public_and_activity_null_scopes_and_unique(self):
        Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='public')
        self.reject(lambda: Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='public'))
        self.reject(lambda: Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='public', activity=self.activity))
        self.reject(lambda: Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='activity'))
        v = Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='activity', activity=self.activity, activity_work=self.aw)
        self.reject(lambda: Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='activity', activity=self.activity, activity_work=self.aw))
        v.cancelled_at = timezone.now()
        v.save()
        Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='activity', activity=self.activity, activity_work=self.aw)

    def test_snapshot_ownership_and_archive(self):
        other = Work.objects.create(original_submitted_title='另一作品')
        self.reject(lambda: Vote.objects.create(work=other, anonymous_client=self.anon, scope='activity', activity=self.activity, activity_work=self.aw))
        Vote.objects.create(work=self.work, anonymous_client=self.anon, scope='activity', activity=self.activity, activity_work=self.aw)
        self.reject(lambda: ActivityWork.objects.filter(pk=self.aw.pk).update(archived_at=timezone.now()))

    def test_recommendation_history_and_governance(self):
        self.reject(lambda: Recommendation.objects.create(work=self.work, anonymous_client=self.anon, scope='public'))
        r = Recommendation.objects.create(work=self.work, anonymous_client=self.anon, scope='public', reason='理由', text_hidden_at=timezone.now())
        self.reject(lambda: Recommendation.objects.create(work=self.work, anonymous_client=self.anon, scope='public', reason='重复'))
        r.invalidated_at = timezone.now()
        r.save()
        Recommendation.objects.create(work=self.work, anonymous_client=self.anon, scope='public', reason='新的')
        r = Recommendation.objects.create(work=self.work, anonymous_client=self.anon, scope='activity', activity=self.activity, activity_work=self.aw)
        r.withdrawn_at = timezone.now()
        r.save()
        Recommendation.objects.create(work=self.work, anonymous_client=self.anon, scope='activity', activity=self.activity, activity_work=self.aw)
        self.reject(lambda: Recommendation.objects.create(work=self.work, anonymous_client=self.anon, scope='public', reason='长'*201))

    def test_tag_actor_unique_and_history(self):
        tag = Tag.objects.create(name='奇幻', normalized_name='奇幻')
        WorkTag.objects.create(work=self.work, tag=tag, source_type='source')
        self.reject(lambda: WorkTag.objects.create(work=self.work, tag=tag, source_type='source'))
        self.reject(lambda: WorkTag.objects.create(work=self.work, tag=tag, source_type='user'))
        WorkTag.objects.create(work=self.work, tag=tag, source_type='user', anonymous_client=self.anon)
        self.reject(lambda: WorkTag.objects.create(work=self.work, tag=tag, source_type='source', admin=self.admin))
        self.assertEqual(WorkTag.objects.count(), 2)

    def test_activity_end_cannot_precede_start_or_exceed_deadline(self):
        self.reject(lambda: Activity.objects.filter(pk=self.activity.pk).update(status='ended', started_at=self.activity.start_at, ended_at=self.activity.start_at-timedelta(seconds=1)))
        self.reject(lambda: Activity.objects.filter(pk=self.activity.pk).update(status='active', started_at=self.activity.scheduled_end_at))
        self.reject(lambda: Activity.objects.filter(pk=self.activity.pk).update(status='ended', started_at=self.activity.start_at, ended_at=self.activity.scheduled_end_at+timedelta(seconds=1)))

    def test_identity_override_foreign_keys_and_cycles(self):
        self.reject(lambda: Work.objects.create(wenku8_book_id=4, original_submitted_title='错绑'))
        self.reject(lambda: Work.objects.create(original_submitted_title='有名', title_overridden=True))
        self.reject(lambda: WorkAlias.objects.create(work_id=99999, alias='外键', normalized_alias='外键'))
        with connection.cursor() as cursor:
            self.assertEqual(cursor.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        other = Work.objects.create(original_submitted_title='另一作品')
        Work.objects.filter(pk=self.work.pk).update(merged_into=other)
        self.reject(lambda: Work.objects.filter(pk=other.pk).update(merged_into=self.work))
        self.work.author_overridden = True
        self.work.author_override = ''
        self.work.save(update_fields=['author_overridden', 'author_override'])
        self.assertTrue(Work.objects.get(pk=self.work.pk).author_overridden)

    def test_raw_sql_tracking_tombstone_and_append_only(self):
        pk = self.work.pk
        with connection.cursor() as cursor:
            cursor.execute('UPDATE booklist_work SET original_submitted_title=%s WHERE id=%s', ['变更', pk])
        self.assertTrue(ChangeEvent.objects.filter(table_name='booklist_work', record_id=pk, action='update').exists())
        alias = WorkAlias.objects.create(work=self.work, alias='别名', normalized_alias='别名')
        alias_pk = alias.pk
        alias.delete()
        event = ChangeEvent.objects.get(table_name='booklist_workalias', record_id=alias_pk, action='delete')
        self.assertEqual(event.work_id, pk)
        self.reject(lambda: ChangeEvent.objects.filter(pk=event.pk).delete())
        self.reject(lambda: AdminAuditLog.objects.update(reason='篡改'))

    def test_last_admin_and_idempotent_initialization(self):
        self.assertIsNone(services.initialize_admin('ignored', PASSWORD))
        self.assertEqual(Admin.objects.count(), 1)
        with self.assertRaises(services.DomainError):
            services.disable_admin(self.admin, self.admin.pk)
        self.reject(lambda: Admin.objects.filter(pk=self.admin.pk).update(is_active=False))

    def test_idempotency_and_rollback(self):
        payload = {'title': '新书'}
        def operation():
            w = Work.objects.create(original_submitted_title=payload['title'])
            return {'id': w.pk}
        first = services.idempotent(self.anon, 'submit', 'a'*24, payload, operation)
        self.assertEqual(first, services.idempotent(self.anon, 'submit', 'a'*24, payload, operation))
        with self.assertRaises(services.DomainError):
            services.idempotent(self.anon, 'submit', 'a'*24, {'title': '不同'}, operation)
        def fail():
            Work.objects.create(original_submitted_title='应回滚')
            raise ValueError('failure')
        before = ChangeEvent.objects.count()
        with self.assertRaises(ValueError):
            services.idempotent(self.anon, 'submit', 'b'*24, payload, fail)
        self.assertFalse(Work.objects.filter(original_submitted_title='应回滚').exists())
        self.assertEqual(ChangeEvent.objects.count(), before)

    def test_backup_can_be_read(self):
        # A read inside this transaction is not a production backup rehearsal.
        with connection.cursor() as cursor:
            self.assertEqual(cursor.execute('PRAGMA foreign_key_check').fetchall(), [])


class HttpSecurityTests(TestCase):
    def setUp(self):
        self.admin = services.initialize_admin('owner', PASSWORD)
        self.client = Client(enforce_csrf_checks=True)

    def post(self, url, body=None, client=None):
        client = client or self.client
        token = client.get('/api/v1/csrf/').json()['csrf_token']
        return client.post(url, data=json.dumps(body or {}), content_type='application/json', HTTP_X_CSRFTOKEN=token)

    def authenticate(self, client=None):
        return self.post('/api/v1/admin/login/', {'username':'owner', 'password':PASSWORD}, client)

    def test_csrf_auth_and_strict_request(self):
        self.assertEqual(self.client.post('/api/v1/identity/', '{}', content_type='application/json').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/admin/me/').status_code, 401)
        self.assertEqual(self.post('/api/v1/identity/', {'admin_id':'1'}).status_code, 400)
        self.assertEqual(self.authenticate().status_code, 200)
        self.assertEqual(self.client.get('/api/v1/admin/me/').json()['username'], 'owner')
        self.assertEqual(self.post('/api/v1/identity/').status_code, 200)

    def test_anonymous_renew_reset_and_no_tokens_in_db(self):
        self.post('/api/v1/identity/')
        token = self.client.cookies[settings.ANONYMOUS_COOKIE].value
        old = AnonymousClient.objects.get()
        self.assertNotEqual(old.token_hash, token)
        self.assertTrue(self.client.cookies[settings.ANONYMOUS_COOKIE]['httponly'])
        self.assertEqual(self.client.cookies[settings.ANONYMOUS_COOKIE]['samesite'], 'Lax')
        self.post('/api/v1/identity/')
        self.assertEqual(AnonymousClient.objects.count(), 1)
        self.post('/api/v1/identity/reset/')
        old.refresh_from_db()
        self.assertIsNotNone(old.revoked_at)
        self.assertNotEqual(token, self.client.cookies[settings.ANONYMOUS_COOKIE].value)
        self.assertEqual(AnonymousClient.objects.count(), 2)

    def test_cookie_separation_expiry_and_logout(self):
        self.post('/api/v1/identity/')
        anonymous = self.client.cookies[settings.ANONYMOUS_COOKIE].value
        self.authenticate()
        token = self.client.cookies[settings.ADMIN_COOKIE].value
        self.assertNotEqual(AdminSession.objects.get().token_hash, token)
        fake = Client()
        fake.cookies[settings.ADMIN_COOKIE] = anonymous
        self.assertEqual(fake.get('/api/v1/admin/me/').status_code, 401)
        self.post('/api/v1/admin/logout/')
        fake.cookies[settings.ADMIN_COOKIE] = token
        self.assertEqual(fake.get('/api/v1/admin/me/').status_code, 401)
        self.authenticate()
        AdminSession.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get('/api/v1/admin/me/').status_code, 401)

    def test_password_change_revokes_all_sessions_and_redacts_audit(self):
        second = Client(enforce_csrf_checks=True)
        self.authenticate()
        self.authenticate(second)
        self.assertEqual(AdminSession.objects.count(), 2)
        response = self.post('/api/v1/admin/password/', {'current_password':PASSWORD, 'new_password':'Other-Password!Foundation-292'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AdminSession.objects.count(), 0)
        self.assertEqual(second.get('/api/v1/admin/me/').status_code, 401)
        self.admin.refresh_from_db()
        self.assertNotEqual(self.admin.password_hash, PASSWORD)
        content = json.dumps(list(AdminAuditLog.objects.values()), default=str)
        self.assertNotIn(PASSWORD, content)
        self.assertNotIn('password_hash', content)

    def test_create_disable_invalidates_old_session(self):
        second_admin = services.create_admin(self.admin, 'second', PASSWORD)
        second = Client(enforce_csrf_checks=True)
        self.post('/api/v1/admin/login/', {'username':'second', 'password':PASSWORD}, second)
        self.authenticate()
        self.assertEqual(self.post(f'/api/v1/admin/accounts/{second_admin.pk}/disable/').status_code, 200)
        self.assertEqual(second.get('/api/v1/admin/me/').status_code, 401)
        self.assertEqual(self.post('/api/v1/admin/accounts/', {'username':'third', 'password':PASSWORD}).status_code, 201)
        services.disable_admin(self.admin, Admin.objects.get(username='third').pk)
        self.assertEqual(self.post(f'/api/v1/admin/accounts/{self.admin.pk}/disable/').status_code, 409)

    def test_failed_login_rate_and_secure_cookie(self):
        with override_settings(COOKIE_SECURE=True):
            response = self.post('/api/v1/identity/')
            self.assertTrue(response.cookies[settings.ANONYMOUS_COOKIE]['secure'])
        for _ in range(10):
            self.assertEqual(self.post('/api/v1/admin/login/', {'username':'owner','password':'wrong'}).status_code, 401)
        self.assertEqual(self.authenticate().status_code, 429)

    def test_server_password_recovery_revokes_and_does_not_enable(self):
        token, _ = services.login('owner', PASSWORD)
        services.reset_admin_password('owner', 'Recovery-Password!Foundation-998')
        self.assertFalse(AdminSession.objects.exists())
        self.client.cookies[settings.ADMIN_COOKIE] = token
        self.assertEqual(self.client.get('/api/v1/admin/me/').status_code, 401)
        target = services.create_admin(self.admin.__class__.objects.get(pk=self.admin.pk), 'disabled', PASSWORD)
        services.disable_admin(Admin.objects.get(pk=self.admin.pk), target.pk)
        services.reset_admin_password('disabled', 'Another-Recovery!Password-998')
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(AdminAuditLog.objects.filter(action='admin.password_reset', admin__isnull=True).exists())

    def test_unknown_body_origin_and_health(self):
        self.assertEqual(self.client.get('/health/').status_code, 200)
        csrf = self.client.get('/api/v1/csrf/').json()['csrf_token']
        response = self.client.post('/api/v1/identity/', '{}', content_type='application/json', HTTP_X_CSRFTOKEN=csrf, HTTP_ORIGIN='https://untrusted.example')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post('/api/v1/identity/', b'0'*32769, content_type='application/json', HTTP_X_CSRFTOKEN=csrf).status_code, 413)


class TextTests(TestCase):
    def test_protocol_limits_and_utc(self):
        from datetime import datetime, timezone as tz
        self.assertEqual(utc_milliseconds(datetime(2026,10,19,8,0,0,123456,tzinfo=tz(timedelta(hours=8)))), '2026-10-19T00:00:00.123Z')
        with self.assertRaises(ValidationError):
            utc_milliseconds(datetime(2026,10,19))
        self.assertEqual(pagination(), (0,20))
        self.assertEqual(pagination('2','100'), (100,100))
        for page,size in [(0,20),(1,101),(True,20),(1,'1.5')]:
            with self.assertRaises(ValidationError):
                pagination(page,size)

    def test_shared_javascript_python_cases(self):
        cases = [{'value':s, 'field':f, 'required':req} for s,f,req in [
            ('中'*199,'reason',False),('中'*200,'reason',False),('中'*201,'reason',False),
            ('😀'*200,'reason',False),('👨‍👩‍👧‍👦'*28,'reason',False),('e\u0301'*101,'reason',False),
            ('\ufeff \r\n理由\r两行\u3000','reason',False),('\u0085保留\u0085','reason',False),
            (' \r\n\t','reason',False),(' \t','title',True),('\ud800','reason',False),
            ('书'*300,'title',True),('书'*301,'title',True),('题'*101,'activity_title',True),
            ('简'*10000,'description',False),('简'*10001,'description',False)]]
        expected = []
        for case in cases:
            try:
                expected.append({'ok':True,'value':normalize(**case)})
            except ValidationError:
                expected.append({'ok':False})
        js = "import {normalize} from './static/text.mjs'; String.prototype.isWellFormed = undefined; let s=''; for await (const c of process.stdin) s+=c; console.log(JSON.stringify(JSON.parse(s).map(x=>{try{return {ok:true,value:normalize(x.value,x.field,x.required)}}catch{return {ok:false}}})));"
        run = subprocess.run(['node', '--input-type=module', '-e', js], input=json.dumps(cases), capture_output=True, encoding='utf-8', cwd=settings.BASE_DIR, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), expected)


class ConcurrentAdminTests(TransactionTestCase):
    def _fixture_teardown(self):
        # Test database reset only: production intentionally cannot delete append-only history.
        with connection.cursor() as cursor:
            triggers=cursor.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
            for name,_ in triggers:
                cursor.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        try:
            super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for _,sql in triggers:
                    cursor.execute(sql)

    def test_concurrent_mutual_disable_leaves_one_active(self):
        a = services.initialize_admin('first', PASSWORD)
        b = services.create_admin(a, 'second', PASSWORD)
        barrier = Barrier(2)
        def disable(actor, target):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                services.disable_admin(actor, target)
                return 'ok'
            except services.DomainError as exc:
                return exc.code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(disable,a,b.pk), pool.submit(disable,b,a.pk)]
            outcomes = [f.result(timeout=20) for f in futures]
        self.assertEqual(outcomes.count('ok'), 1)
        self.assertEqual(Admin.objects.filter(is_active=True).count(), 1)
