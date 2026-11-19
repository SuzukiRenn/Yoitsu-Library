import copy
import hashlib
from urllib.parse import urlencode
from unittest.mock import patch
from django.conf import settings
from django.test import TestCase
from booklist import rankings, catalog
from booklist.models import Work, PublicRecommendation, PublicLike, Notification, Account
from booklist.management.commands.import_rankings_catalog import import_catalog
from booklist.navigation import safe_return

class RankingsTests(TestCase):
    def seed_refs(self):
        made={}
        for row in rankings.dataset()['works'].values():
            refs=row['refs']
            if not any(refs.values()):continue
            key=(refs['wenku8_id'],refs['bili_id'])
            if key in made:continue
            metadata={'edition':'book'}
            if refs['bili_id']:metadata['bili_id']=refs['bili_id']
            made[key]=Work.objects.create(original_submitted_title=row['title'],wenku8_title=row['title'] if refs['wenku8_id'] else None,
                wenku8_book_id=refs['wenku8_id'],identity_status='matched' if refs['wenku8_id'] else 'unmatched',source_metadata=metadata)
        return made

    def test_curated_data_and_every_cover_hash(self):
        data=rankings.dataset()
        entries=[e for y in data['years'] for s in y['sections'] for e in s['entries']]
        self.assertEqual(len(entries),619)
        self.assertEqual(len(data['years']),23)
        self.assertEqual(sum(len(y['sections']) for y in data['years']),34)
        for name,digest in data['coverHashes'].items():
            self.assertEqual(hashlib.sha256((settings.BASE_DIR/'static/rankings/covers'/name).read_bytes()).hexdigest(),digest)
        self.assertTrue(all(e['workKey'] in data['works'] and rankings.curated_cover(e['cover']) for e in entries))

    def test_import_is_additive_idempotent_and_never_creates_recommendations(self):
        self.seed_refs()
        before=list(Work.objects.order_by('id').values())
        plan=import_catalog()
        result=import_catalog(apply=True)
        self.assertEqual(result['created_works'],plan['would_create'])
        self.assertEqual(list(Work.objects.filter(id__in=[r['id'] for r in before]).order_by('id').values()),before)
        self.assertEqual(import_catalog(apply=True)['created_works'],0)
        self.assertEqual(len(rankings.catalog_bindings()),296)
        self.assertEqual(PublicRecommendation.objects.count(),0)
        self.assertEqual(PublicLike.objects.count(),0)
        self.assertEqual(Notification.objects.count(),0)
        self.assertEqual(Account.objects.count(),0)
        self.assertFalse(Work.objects.exclude(public_added_at=None).exists())

    def test_real_details_and_rankings_return_url(self):
        self.seed_refs();import_catalog(apply=True)
        response=self.client.get('/api/v1/rankings/')
        self.assertEqual(response.status_code,200)
        entries=[e for y in response.json()['years'] for s in y['sections'] for e in s['entries']]
        self.assertTrue(all(e['workUrl'] for e in entries))
        source='/rankings/?year=2025&category=bunko&shown=20'
        self.assertEqual(safe_return(source),source)
        detail=self.client.get(entries[0]['workUrl']+'?'+urlencode({'from':source}))
        self.assertContains(detail,'/rankings/?year=2025&amp;category=bunko&amp;shown=20')
        page=self.client.get(source)
        self.assertContains(page,'data-navigation-loading')
        self.assertContains(page,'href="/rankings/" aria-current="true"')
        self.assertNotContains(page,'排版预览')
        self.assertNotContains(page,'href="/" aria-current="true"')

    def test_hidden_or_merged_existing_books_never_reappear_as_duplicates(self):
        from django.utils import timezone
        self.seed_refs();import_catalog(apply=True)
        work=next(w for w in rankings.catalog_bindings().values() if w.source_metadata.get('source_provider')=='konorano_rankings')
        target=Work.objects.create(original_submitted_title='Merged destination')
        work.merged_into=target;work.save(update_fields=['merged_into'])
        self.assertEqual(rankings.visible_binding(work).pk,target.pk)
        target.hidden_at=timezone.now();target.save(update_fields=['hidden_at'])
        self.assertIsNone(rankings.visible_binding(work))
        self.assertEqual(import_catalog(apply=True)['created_works'],0)

    def test_existing_manual_book_is_reused_without_changing_it(self):
        self.seed_refs()
        row=next(w for w in rankings.dataset()['works'].values() if not any(w['refs'].values()))
        existing=Work.objects.create(original_submitted_title=row['title'],author_override=row['author'],author_overridden=True)
        before=Work.objects.filter(pk=existing.pk).values().get()
        self.assertEqual(rankings.catalog_bindings()[row['key']].pk,existing.pk)
        import_catalog(apply=True)
        self.assertEqual(Work.objects.filter(pk=existing.pk).values().get(),before)

    def test_static_cover_allowlist_and_method_parameter_boundaries(self):
        name=next(iter(rankings.dataset()['coverHashes']))
        valid='/static/rankings/covers/'+name
        self.assertEqual(catalog.cover_url(valid),valid)
        for invalid in ('/static/rankings/covers/../data.json','/static/rankings/covers/'+'0'*64+'.jpg',valid+'?x=1','//evil.test/image.jpg'):
            self.assertIsNone(rankings.curated_cover(invalid))
            self.assertIsNone(catalog.cover_url(invalid))
        self.assertEqual(catalog.cover_url('https://example.test/image.jpg'),'https://example.test/image.jpg')
        self.assertEqual(self.client.get('/api/v1/rankings/?extra=1').status_code,400)
        self.assertEqual(self.client.get('/rankings/?year=2025&year=2026').status_code,400)
        self.assertNotEqual(self.client.post('/rankings/').status_code,200)

    def test_2027_sources_and_all_historical_data_are_preserved(self):
        import json
        data=rankings.dataset()
        canonical=lambda value:hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(canonical(data['years'][:-1]),'bbb308fcee840a4ae6eaf2a296fcb02a758301468de016dde68ac73d6f59ff15')
        old_keys={e['workKey'] for y in data['years'][:-1] for s in y['sections'] for e in s['entries']}
        self.assertEqual(len(old_keys),280)
        self.assertEqual(canonical({k:data['works'][k] for k in old_keys}),'cc5c3253525415548fcf9e198c07c20a51cd52f699d3704b714c6a4e467cd311')
        latest=data['years'][-1]
        self.assertEqual(latest['year'],2027)
        bunko,novels=latest['sections']
        self.assertEqual([e['rank'] for e in bunko['entries']],list(range(1,21)))
        self.assertEqual([e['rank'] for e in novels['entries']],list(range(1,11)))
        self.assertEqual(bunko['entries'][0]['japanese'],'負けヒロインが多すぎる！')
        self.assertEqual(novels['entries'][0]['japanese'],'崩壊世界の魔法杖職人')
        self.assertEqual(novels['entries'][-1]['japanese'],'本好きの下剋上 ハンネローレの貴族院五年生')
        # The same series must retain its old history identity, including punctuation variants.
        for entry in bunko['entries']+novels['entries']:
            if entry['workKey'] in old_keys:
                self.assertEqual(entry['japanese'],data['works'][entry['workKey']]['japanese'])
        provenance=json.loads((settings.BASE_DIR/'static/rankings/sources.json').read_text(encoding='utf-8'))
        annual=provenance['annual_updates'][0]
        self.assertEqual(annual['rank_sources']['bunko']['url'],'https://bgm.tv/index/106106')
        self.assertEqual(len(annual['records']),30)
        self.assertEqual(provenance['workbook_sha256'],data['sourceSha256'])
        page=self.client.get('/rankings/')
        self.assertContains(page,'2005—2027 年作品排名')
        self.assertContains(page,'id="selected-year">2027</span>')
        self.assertContains(page,'rankings.mjs?v=20261119-1')

    def test_new_bunko_book_does_not_bind_to_same_named_web_edition(self):
        self.seed_refs()
        row=rankings.dataset()['works'][rankings.dataset()['years'][-1]['sections'][0]['entries'][2]['workKey']]
        web=Work.objects.create(original_submitted_title=row['title'],author_override=row['author'],
            author_overridden=True,source_metadata={'edition':'web','bili_id':5271})
        self.assertNotIn(row['key'],rankings.catalog_bindings())
        import_catalog(apply=True)
        actual=rankings.catalog_bindings()[row['key']]
        self.assertNotEqual(actual.pk,web.pk)
        self.assertEqual(actual.source_metadata['edition'],'book')
