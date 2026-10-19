from copy import deepcopy
from datetime import timedelta
import importlib
import io
import json
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from booklist.models import (Work, Tag, TagSource, WorkTag, TagSuppression, SourceFieldState,
    AnonymousClient, ImportBatch, ImportItem, ImportAttempt, AdminAuditLog, ChangeEvent)
from booklist import importing, services
from booklist.catalog_admin import set_override,suppress_tag
from integrations.wenku8.offline import extract, SnapshotError, field, parser

FIXTURE=Path(settings.BASE_DIR)/'tests/fixtures/wenku8'
PASSWORD='Offline-Import!Admin-Verification-778'


def snapshot(book=4151):
    return extract((FIXTURE/'details'/f'{book}.htm').read_bytes(),book,f'https://www.wenku8.net/book/{book}.htm')


class ImportTests(TestCase):
    def test_three_saved_samples_and_repeat_have_no_duplicate_or_spurious_events(self):
        forbidden=[]
        def no_network(*args,**kwargs):
            forbidden.append(True)
            raise AssertionError('Import tried to connect')
        with patch.object(socket.socket,'connect',no_network),patch.object(socket,'create_connection',no_network):
            for book in (4151,3219,2951):
                work,warnings,created=importing.apply_snapshot(snapshot(book))
                self.assertTrue(created)
                self.assertEqual(warnings,[])
                self.assertIsNone(work.original_submitted_title)
                self.assertIsNone(work.public_added_at)
            count=ChangeEvent.objects.count()
            for book in (4151,3219,2951):
                work,warnings,created=importing.apply_snapshot(snapshot(book))
                self.assertFalse(created)
                self.assertEqual(warnings,[])
            self.assertEqual(Work.objects.count(),3)
            self.assertEqual(ChangeEvent.objects.count(),count)
        self.assertFalse(forbidden)
        self.assertEqual(Work.objects.get(wenku8_book_id=3219).source_metadata['word_count'],117571)
        self.assertIn('凶暴虐千金',Work.objects.get(wenku8_book_id=3219).wenku8_title)

    def test_parser_missing_and_explicit_empty_fields_are_distinct(self):
        p=parser()
        root=p.html.fromstring((FIXTURE/'details/4151.htm').read_bytes())
        author=next(td for td in root.xpath('//td[not(.//td)]') if p.LEGACY.value_after_label(td,'小说作者') is not None)
        author.text='小说作者：'
        raw=p.html.tostring(root,encoding='utf-8',include_meta_content_type=True)
        empty=extract(raw,4151,'https://www.wenku8.net/book/4151.htm')
        self.assertEqual(empty['fields']['author']['state'],'empty')
        author.getparent().remove(author)
        raw=p.html.tostring(root,encoding='utf-8',include_meta_content_type=True)
        missing=extract(raw,4151,'https://www.wenku8.net/book/4151.htm')
        self.assertEqual(missing['fields']['author']['state'],'not_fetched')
        work,_,_=importing.apply_snapshot(snapshot())
        importing.apply_snapshot(missing)
        work.refresh_from_db()
        self.assertEqual(work.wenku8_author,'樱生怀')
        importing.apply_snapshot(empty)
        work.refresh_from_db()
        self.assertIsNone(work.wenku8_author)

    def test_missing_cover_description_and_tags_do_not_block_identity(self):
        p=parser()
        root=p.html.fromstring((FIXTURE/'details/4151.htm').read_bytes())
        detail=p.metadata_tables(root)[0].xpath('following-sibling::table[1]')[0]
        detail.getparent().remove(detail)
        raw=p.html.tostring(root,encoding='utf-8',include_meta_content_type=True)
        data=extract(raw,4151,'https://www.wenku8.net/book/4151.htm')
        work,warnings,created=importing.apply_snapshot(data)
        self.assertTrue(created)
        self.assertIsNone(work.wenku8_cover_url)
        self.assertIsNone(work.wenku8_description)
        self.assertIn('tags_incomplete',warnings)

    def test_identity_url_title_and_search_results_rejected(self):
        with self.assertRaises(SnapshotError):
            extract((FIXTURE/'details/4151.htm').read_bytes(),3219,'https://www.wenku8.net/book/3219.htm')
        for url in ('https://evil.example/book/4151.htm','https://www.wenku8.net/book/4151.htm?token=x'):
            with self.assertRaises(SnapshotError):
                extract((FIXTURE/'details/4151.htm').read_bytes(),4151,url)
        data=snapshot()
        data['fields']['title']['value']='长'*301
        with self.assertRaises(SnapshotError):
            importing.apply_snapshot(data)
        self.assertFalse(Work.objects.exists())

    def test_parser_overlong_title_raw_value_is_retained(self):
        p=parser()
        root=p.html.fromstring((FIXTURE/'details/4151.htm').read_bytes())
        header=p.metadata_tables(root)[0].xpath('.//span/b')[0]
        header.text='长'*301
        data=extract(p.html.tostring(root,encoding='utf-8',include_meta_content_type=True),4151,'https://www.wenku8.net/book/4151.htm')
        self.assertEqual(data['fields']['title']['state'],'invalid')
        self.assertEqual(len(data['fields']['title']['raw']),301)
        with self.assertRaises(SnapshotError):
            importing.apply_snapshot(data)

    def test_optional_invalid_fields_never_clear_previous_values(self):
        work,_,_=importing.apply_snapshot(snapshot())
        previous=work.wenku8_description
        data=snapshot()
        data['fields']['introduction']=field('value','长'*10001,raw='长'*10001)
        data['fields']['cover_url']=field('value','javascript:bad',raw='javascript:bad')
        _,warnings,_=importing.apply_snapshot(data)
        work.refresh_from_db()
        self.assertEqual(work.wenku8_description,previous)
        self.assertTrue(work.wenku8_cover_url.startswith('http'))
        state=SourceFieldState.objects.get(work=work,field='introduction')
        self.assertEqual(state.state,'invalid')
        self.assertEqual(len(state.raw_value),10001)
        self.assertIn('invalid_or_overlong_cover_url',warnings)

    def test_explicit_empty_optional_fields_clear_source_only(self):
        work,_,_=importing.apply_snapshot(snapshot())
        data=snapshot()
        for name in ('author','introduction','cover_url'):
            data['fields'][name]=field('empty',raw='')
        importing.apply_snapshot(data)
        work.refresh_from_db()
        self.assertIsNone(work.wenku8_author)
        self.assertIsNone(work.wenku8_cover_url)
        self.assertIsNone(work.wenku8_description)

    def test_overrides_original_title_visibility_and_source_restore(self):
        admin=services.initialize_admin('owner',PASSWORD)
        work,_,_=importing.apply_snapshot(snapshot())
        work.original_submitted_title='最初输入'
        work.public_added_at=timezone.now()
        work.hidden_at=timezone.now()
        work.review_status='pending'
        work.save()
        added,hidden=work.public_added_at,work.hidden_at
        set_override(admin,work.pk,'title','管理员标题')
        set_override(admin,work.pk,'author','')
        data=snapshot()
        data['fields']['title']=field('value','来源新标题',raw='来源新标题')
        importing.apply_snapshot(data)
        work.refresh_from_db()
        self.assertEqual(work.display_title,'管理员标题')
        self.assertEqual(work.wenku8_title,'来源新标题')
        self.assertTrue(work.author_overridden)
        self.assertEqual(work.author_override,'')
        self.assertEqual(work.original_submitted_title,'最初输入')
        self.assertEqual((work.public_added_at,work.hidden_at,work.review_status),(added,hidden,'pending'))
        work=set_override(admin,work.pk,'title',enabled=False)
        self.assertEqual(work.display_title,'来源新标题')
        self.assertTrue(AdminAuditLog.objects.filter(action='work.restore_source',admin=admin).exists())

    def test_only_complete_tags_retract_source_and_never_user_tags(self):
        work,_,_=importing.apply_snapshot(snapshot())
        _,anon=services.issue_anonymous()
        user_tag=Tag.objects.get(name='后宫')
        WorkTag.objects.create(work=work,tag=user_tag,source_type='user',anonymous_client=anon)
        data=snapshot()
        data['fields']['tags']=field('value',[{'id':None,'name':'穿越'}],raw='穿越',complete=False)
        importing.apply_snapshot(data)
        self.assertEqual(WorkTag.objects.filter(work=work,source_type='source',state='active').count(),6)
        data['fields']['tags']=field('empty',raw='',complete=True)
        importing.apply_snapshot(data)
        self.assertFalse(WorkTag.objects.filter(work=work,source_type='source',state='active').exists())
        self.assertTrue(WorkTag.objects.filter(work=work,source_type='user',state='active').exists())
        importing.apply_snapshot(snapshot())
        self.assertEqual(WorkTag.objects.filter(work=work,source_type='source',state='active').count(),6)

    def test_parser_empty_tag_label_is_complete_but_missing_is_not(self):
        p=parser()
        root=p.html.fromstring((FIXTURE/'details/4151.htm').read_bytes())
        tags=next(n for n in root.xpath('//span/b') if p.LEGACY.value_after_label(n,'作品Tags') is not None)
        tags.text='作品Tags：'
        data=extract(p.html.tostring(root,encoding='utf-8',include_meta_content_type=True),4151,'https://www.wenku8.net/book/4151.htm')
        self.assertEqual((data['fields']['tags']['state'],data['fields']['tags']['complete']),('empty',True))
        tags.getparent().remove(tags)
        data=extract(p.html.tostring(root,encoding='utf-8',include_meta_content_type=True),4151,'https://www.wenku8.net/book/4151.htm')
        self.assertEqual((data['fields']['tags']['state'],data['fields']['tags']['complete']),('not_fetched',False))

    def test_html_recovery_does_not_certify_truncated_metadata(self):
        work,_,_=importing.apply_snapshot(snapshot())
        raw=(FIXTURE/'details/4151.htm').read_text(encoding='utf-8')
        raw=raw.replace('作品Tags：穿越 奇幻 魔法 经营 后宫 大小姐','作品Tags：穿越').rsplit('</body>',1)[0]
        data=extract(raw.encode('utf-8'),4151,'https://www.wenku8.net/book/4151.htm')
        self.assertEqual(data['fields']['tags']['state'],'parse_error')
        self.assertFalse(data['fields']['tags']['complete'])
        importing.apply_snapshot(data)
        self.assertEqual(WorkTag.objects.filter(work=work,source_type='source',state='active').count(),6)

    def test_ignored_url_noise_imports_but_text_errors_do_not_retract_tags(self):
        raw=(FIXTURE/'details/4151.htm').read_text(encoding='utf-8')
        raw=raw.replace('<body>','<body><a href="/?x=1&from=2">nav</a>')
        work,warnings,_=importing.apply_snapshot(extract(raw.encode('utf-8'),4151,'https://www.wenku8.net/book/4151.htm'))
        self.assertEqual(warnings,[])
        self.assertEqual(WorkTag.objects.filter(work=work,state='active').count(),6)
        raw=raw.replace('作品Tags：穿越 奇幻 魔法 经营 后宫 大小姐','作品Tags：partial&from=2')
        _,warnings,_=importing.apply_snapshot(extract(raw.encode('utf-8'),4151,'https://www.wenku8.net/book/4151.htm'))
        self.assertIn('incomplete_document',warnings)
        self.assertEqual(WorkTag.objects.filter(work=work,state='active').count(),6)

    def test_same_title_unmatched_work_is_not_automatically_bound(self):
        title=snapshot()['fields']['title']['value']
        original=Work.objects.create(original_submitted_title=title)
        imported,_,_=importing.apply_snapshot(snapshot())
        original.refresh_from_db()
        self.assertNotEqual(original.pk,imported.pk)
        self.assertIsNone(original.wenku8_book_id)
        self.assertIsNone(original.merged_into_id)

    def test_source_suppression_not_revived_and_explicit_release_follows_source(self):
        admin=services.initialize_admin('owner',PASSWORD)
        work,_,_=importing.apply_snapshot(snapshot())
        tag=Tag.objects.get(name='穿越')
        suppress_tag(admin,work.pk,tag.pk,'source',True,'错误来源标签')
        importing.apply_snapshot(snapshot())
        self.assertFalse(WorkTag.objects.filter(work=work,tag=tag,source_type='source',state='active').exists())
        suppress_tag(admin,work.pk,tag.pk,'source',False,'恢复跟随来源')
        importing.apply_snapshot(snapshot())
        self.assertTrue(WorkTag.objects.filter(work=work,tag=tag,source_type='source',state='active').exists())

    def test_work_level_prohibition_and_disabled_tag_not_reenabled(self):
        admin=services.initialize_admin('owner',PASSWORD)
        work,_,_=importing.apply_snapshot(snapshot())
        tag=Tag.objects.get(name='穿越')
        tag.is_active=False
        tag.save()
        suppress_tag(admin,work.pk,tag.pk,'all',True,'禁止该作品标签')
        data=snapshot()
        data['fields']['tags']=field('empty',raw='',complete=True)
        importing.apply_snapshot(data)
        importing.apply_snapshot(snapshot())
        tag.refresh_from_db()
        self.assertFalse(tag.is_active)
        self.assertFalse(WorkTag.objects.filter(work=work,tag=tag,source_type='source',state='active').exists())

    def test_different_ids_same_name_are_distinct_and_mapped_identity_is_reused(self):
        data=snapshot()
        data['fields']['tags']=field('value',[{'id':10,'name':'同名'},{'id':20,'name':'同名'}],raw='同名 同名',complete=True)
        work,warnings,_=importing.apply_snapshot(data)
        self.assertEqual(Tag.objects.filter(name='同名').count(),2)
        self.assertEqual(Tag.objects.filter(name='同名',duplicate_review_needed=True).count(),2)
        self.assertIn('duplicate_tag_name_needs_review',warnings)
        canonical=Tag.objects.create(name='核实后的主标签',normalized_name='核实后的主标签')
        # Preconfigured approved mapping, not a test of an implemented tag merge service.
        TagSource.objects.filter(source_key='wenku8:id:10').update(tag=canonical)
        importing.apply_snapshot(data)
        self.assertEqual(TagSource.objects.get(source_key='wenku8:id:10').tag_id,canonical.pk)
        self.assertTrue(WorkTag.objects.filter(work=work,tag=canonical,state='active').exists())
        self.assertEqual(Tag.objects.filter(name='同名').count(),2)

    def test_tag_name_change_does_not_overwrite_admin_corrected_display(self):
        data=snapshot()
        data['fields']['tags']=field('value',[{'id':10,'name':'旧名'}],raw='旧名',complete=True)
        importing.apply_snapshot(data)
        tag=Tag.objects.get(name='旧名')
        tag.name='管理员纠正'
        tag.save()
        data['fields']['tags']['value'][0]['name']='来源新名'
        _,warnings,_=importing.apply_snapshot(data)
        tag.refresh_from_db()
        self.assertEqual(tag.name,'管理员纠正')
        self.assertIn('tag_name_change_needs_review',warnings)

    def test_stale_snapshot_and_merged_source_records_rejected(self):
        work,_,_=importing.apply_snapshot(snapshot())
        data=snapshot()
        data['fields']['last_updated']=field('value','2020-01-01',raw='2020-01-01')
        with self.assertRaises(SnapshotError):
            importing.apply_snapshot(data)
        other=Work.objects.create(original_submitted_title='来源异常根')
        with self.assertRaises(IntegrityError),transaction.atomic():
            Work.objects.filter(pk=work.pk).update(merged_into=other)

    def test_atomic_rollback_when_sync_fails(self):
        with patch('booklist.importing.sync_tags',side_effect=IntegrityError('injected storage failure')):
            with self.assertRaises(IntegrityError):
                importing.apply_snapshot(snapshot())
        self.assertFalse(Work.objects.exists())
        self.assertFalse(SourceFieldState.objects.exists())
        self.assertFalse(ChangeEvent.objects.exists())


class BatchTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=settings.DATA_DIR)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.doc=json.loads((FIXTURE/'manifest.json').read_text(encoding='utf-8'))
        for item in self.doc['items']:
            item['path']=item['key']+'.htm'
            (self.root/item['path']).write_bytes((FIXTURE/'details'/(item['key']+'.htm')).read_bytes())
        self.path=self.root/'manifest.json'
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.doc,ensure_ascii=False),encoding='utf-8')

    def test_cli_pause_resume_skip_and_new_batch_duplicate(self):
        output=io.StringIO()
        call_command('import_wenku8',manifest=self.path,max_items=1,stdout=output)
        first=json.loads(output.getvalue())
        self.assertEqual(first['status'],'paused')
        self.assertEqual(first['counts']['pending'],2)
        output=io.StringIO()
        call_command('import_wenku8',resume=first['batch_id'],stdout=output)
        result=json.loads(output.getvalue())
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['processed_this_run'],2)
        batch=importing.resume_batch(first['batch_id'])
        self.assertEqual(importing.run_batch(batch)['processed_this_run'],0)
        self.assertEqual(ImportAttempt.objects.count(),3)
        importing.run_batch(importing.create_batch(self.path))
        self.assertEqual(Work.objects.count(),3)

    def test_bad_identity_isolated_and_failed_attempts_append_only(self):
        (self.root/'3219.htm').write_bytes((FIXTURE/'details/4151.htm').read_bytes())
        batch=importing.create_batch(self.path)
        result=importing.run_batch(batch)
        self.assertEqual(result['counts'],{'pending':0,'imported':2,'partial':0,'failed':1})
        self.assertFalse(Work.objects.filter(wenku8_book_id=3219).exists())
        self.assertEqual(importing.run_batch(batch)['processed_this_run'],0)
        self.assertEqual(importing.run_batch(batch,retry_failed=True)['processed_this_run'],1)
        failed=ImportAttempt.objects.filter(status='failed')
        self.assertEqual(failed.count(),2)
        with self.assertRaises(IntegrityError),transaction.atomic():
            failed.update(error_code='changed')

    def test_input_manifest_and_parser_change_rejected(self):
        batch=importing.create_batch(self.path)
        with patch('booklist.importing.fingerprint',return_value='changed'):
            with self.assertRaises(SnapshotError):
                importing.resume_batch(batch.pk)
        self.doc['items'][0]['key']='changed'
        self.save()
        with self.assertRaises(SnapshotError):
            importing.resume_batch(batch.pk)

    def test_changed_file_isolated_and_retry_after_transient_failure(self):
        batch=importing.create_batch(self.path)
        (self.root/'4151.htm').write_text('changed',encoding='utf-8')
        result=importing.run_batch(batch)
        self.assertEqual(result['counts']['failed'],1)
        self.assertEqual(batch.items.get(book_id=4151).error_code,'input_changed_start_new_batch')
        new_batch=importing.create_batch(FIXTURE/'manifest.json')
        with patch('booklist.importing.apply_snapshot',side_effect=IntegrityError('transient test failure')):
            result=importing.run_batch(new_batch,max_items=1)
        self.assertEqual(result['counts']['failed'],1)
        result=importing.run_batch(new_batch,retry_failed=True)
        self.assertEqual(result['counts']['imported'],3)

    def test_manifest_rejects_outside_paths_and_duplicate_ids(self):
        self.doc['items'][0]['path']='../outside.htm'
        self.save()
        with self.assertRaises(SnapshotError):
            importing.create_batch(self.path)
        self.doc=json.loads((FIXTURE/'manifest.json').read_text(encoding='utf-8'))
        self.doc['items'][1]=deepcopy(self.doc['items'][0])
        self.doc['items'][1]['key']='different'
        self.save()
        with self.assertRaises(SnapshotError):
            importing.create_batch(self.path)

    def test_cli_partial_failure_has_nonzero_exit(self):
        (self.root/'3219.htm').write_text('<html>unknown</html>',encoding='utf-8')
        with self.assertRaises(CommandError):
            call_command('import_wenku8',manifest=self.path,stdout=io.StringIO())
        self.assertEqual(Work.objects.count(),2)
