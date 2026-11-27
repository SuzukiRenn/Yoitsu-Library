"""Exercise the offline transfer entry points against separate synthetic volumes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from django.conf import settings
from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path(os.environ.get("COURSE_CATALOG_TEST_SOURCE_ROOT", ROOT))
SCRIPT = ROOT / "scripts/course_catalog_transfer.py"
START = """
import django
django.setup()
from booklist.models import *
from django.utils import timezone
from datetime import timedelta
"""

SOURCE_FIXTURE = r"""
import struct, zlib
from booklist import cover_cache, rankings

def chunk(kind, data):
    body = kind + data
    return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

def png(color):
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\x00' + color)) + chunk(b'IEND', b''))

url = 'https://img.wenku8.com/image/0/123/123s.png'
base = cover_cache.store(url, png(b'old'), 'image/png')
version = cover_cache.store_version(url, png(b'new'), 'image/png')
one = Work.objects.create(wenku8_book_id=123, identity_status='matched', wenku8_title='源版本书',
                          wenku8_author='作者甲', wenku8_description='星际旅行的公开简介', wenku8_cover_url=url,
                          source_metadata={'edition': 'book', 'publisher_category': '电击文库',
                                           'cover_cache_version': version, 'private_arbitrary': '不应传输'})
WorkAlias.objects.create(work=one, alias='有效别名', normalized_alias='有效别名')
WorkAlias.objects.create(work=one, alias='历史别名', normalized_alias='历史别名', archived_at=timezone.now())
admin = Admin.objects.create(username='source-admin-secret', password_hash='synthetic')
reader = AnonymousClient.objects.create(token_hash='b' * 64, last_seen_at=timezone.now(),
                                        expires_at=timezone.now() + timedelta(days=1))
for name, origin in [('科幻', 'source'), ('旅行', 'user'), ('禁止', 'source'), ('隐藏标签', 'source')]:
    tag = Tag.objects.create(name=name, normalized_name=name, is_active=name != '隐藏标签')
    WorkTag.objects.create(work=one, tag=tag, source_type=origin, anonymous_client=reader if origin == 'user' else None)
    if name == '禁止':
        TagSuppression.objects.create(work=one, tag=tag, scope='source', admin=admin)

url = 'https://www.bilinovel.com/files/article/image/4/4321/4321s.png'
cover_cache.store(url, png(b'web'), 'image/png')
local = Work.objects.create(original_submitted_title='本地封面书', cover_url_overridden=True, cover_url_override=url,
                            source_metadata={'source_provider': 'bilinovel', 'bili_id': 4321, 'edition': 'web'})
UserWorkMetadata.objects.create(work=local, anonymous_client=reader, author='作者乙', description='公开的手工简介')
PublicRecommendation.objects.create(work=local, anonymous_client=reader)
Recommendation.objects.create(work=local, anonymous_client=reader, scope='public', reason='绝不导出的推荐理由')
data = rankings.dataset()
key, ranked = next((key, row) for key, row in data['works'].items() if row['cover'])
Work.objects.create(original_submitted_title=ranked['title'], author_overridden=True, author_override=ranked['author'],
                    cover_url_overridden=True, cover_url_override=ranked['cover'],
                    source_metadata={'source_provider': 'konorano_rankings', 'ranking_key': key, 'edition': 'book'})
Work.objects.create(wenku8_book_id=124, identity_status='matched', wenku8_title='有目标对应书目',
                    wenku8_cover_url='https://covers.example.net/book.png')
Work.objects.create(original_submitted_title='隐藏书勿导出', hidden_at=timezone.now())
Work.objects.create(original_submitted_title='待审书勿导出', review_status='pending')
Work.objects.create(original_submitted_title='合并书勿导出', merged_into=one)
Work.objects.create(original_submitted_title='退役书勿导出', source_metadata={'manual_retired_at': timezone.now().isoformat()})
"""

TARGET_FIXTURE = """
reader = AnonymousClient.objects.create(token_hash='a' * 64, last_seen_at=timezone.now(),
                                        expires_at=timezone.now() + timedelta(days=1))
Account.objects.create(username='course-reader', display_name='课设读者', principal=reader, password_hash='synthetic')
work = Work.objects.create(wenku8_book_id=124, identity_status='matched', wenku8_title='目标站自己维护的标题',
                           public_added_at=timezone.now())
PublicRecommendation.objects.create(work=work, anonymous_client=reader)
Recommendation.objects.create(work=work, anonymous_client=reader, scope='public', reason='课设站已有的理由')
"""


def run_python(root, data, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("BOOKLIST_")}
    env.update(BOOKLIST_ENV="test", BOOKLIST_DATA_DIR=str(data), DJANGO_SETTINGS_MODULE="config.settings",
               PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-B", *map(str, args)], cwd=root, env=env,
                          capture_output=True, encoding="utf-8", timeout=120)


def run_ok(root, data, *args):
    result = run_python(root, data, *args)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return result


def state(data):
    with closing(sqlite3.connect(data / "booklist.sqlite3")) as db:
        return {
            name: db.execute("SELECT * FROM " + name + " ORDER BY rowid").fetchall()
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'booklist_%'")
        }


class CourseCatalogTransferTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        scratch = tempfile.TemporaryDirectory(prefix="course-catalog-transfer-", dir=settings.DATA_DIR)
        cls.addClassCleanup(scratch.cleanup)
        cls.root = Path(scratch.name)
        cls.source = cls.root / "identity"
        cls.template = cls.root / "target-template"
        for data, root, sample in ((cls.source, SOURCE_ROOT, SOURCE_FIXTURE), (cls.template, ROOT, TARGET_FIXTURE)):
            data.mkdir()
            run_ok(root, data, "manage.py", "migrate", "--noinput", "--verbosity", "0")
            run_ok(root, data, "-c", START + sample)
        (cls.template / ".course-volume").touch()
        cls.bundle = cls.root / "bundle"
        before = cls.source_files()
        result = run_ok(SOURCE_ROOT, cls.source, SCRIPT, "export", "--bundle", cls.bundle)
        cls.export_receipt = json.loads(result.stdout.splitlines()[-1])
        assert cls.source_files() == before, "Export changed source data bytes"

    @classmethod
    def source_files(cls):
        # SQLite can create its own transient WAL sidecars when the parent is writable.
        return {str(path.relative_to(cls.source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in cls.source.rglob("*") if path.is_file()
                and path.name not in {"booklist.sqlite3-wal", "booklist.sqlite3-shm"}}

    def setUp(self):
        self.target = self.root / self._testMethodName
        shutil.copytree(self.template, self.target)

    def transfer(self, *options, bundle=None, success=True):
        result = run_python(ROOT, self.target, SCRIPT, "import", "--bundle", bundle or self.bundle, *options)
        self.assertEqual(result.returncode, 0 if success else 1, (result.stdout, result.stderr))
        receipt = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(receipt["success"], success)
        return receipt

    def test_round_trip_and_retry_preserve_existing_rows(self):
        self.assertEqual((self.export_receipt["works"], self.export_receipt["assets"]), (4, 3))
        manifest = (self.bundle / "manifest.json").read_text(encoding="utf-8")
        for text in ("历史别名", "隐藏标签", "禁止", "勿导出", "绝不导出的推荐理由", "source-admin-secret", "private_arbitrary"):
            self.assertNotIn(text, manifest)

        before = state(self.target)
        dry_run = self.transfer("--dry-run")
        self.assertEqual((dry_run["created_works"], dry_run["would_create_works"], dry_run["existing_works"]), (0, 3, 1))
        self.assertEqual(state(self.target), before)
        self.assertFalse((self.target / "covers").exists())
        first = self.transfer()
        self.assertEqual((first["created_works"], first["existing_works"], first["cache_files_added"]), (3, 1, 4))
        self.assertEqual(first["manifest_sha256"], self.export_receipt["manifest_sha256"])
        after = state(self.target)
        for table, rows in before.items():
            self.assertEqual(after[table][:len(rows)], rows, table)
            # Existing database triggers append public catalog change events.
            if table not in {"booklist_work", "booklist_workalias", "booklist_tag", "booklist_worktag",
                             "booklist_changeevent"}:
                self.assertEqual(after[table], rows, table)
        second = self.transfer()
        self.assertEqual((second["created_works"], second["existing_works"], second["cache_files_added"]), (0, 4, 0))
        self.assertEqual(state(self.target), after)

        run_ok(ROOT, self.target, "-c", START + """
from django.test import Client
from booklist import catalog, cover_cache, rankings
client = Client(HTTP_HOST='localhost')
work = Work.objects.get(wenku8_book_id=123)
one = catalog.work_detail(work.pk)
assert one['title'] == '源版本书' and one['description'] == '星际旅行的公开简介'
assert {tag['name'] for tag in one['tags']} == {'科幻', '旅行'}
assert catalog.search('有效别名')['results'][0]['id'] == work.pk
assert client.get('/works/%s/' % work.pk).status_code == 200
assert client.get(one['cover_url']).status_code == 200
assert cover_cache.cache_info(one['cover_url'].split('/')[-1])['versioned']
local = Work.objects.get(source_metadata__bili_id=4321)
two = catalog.work_detail(local.pk)
assert two['author'] == '作者乙' and two['description'] == '公开的手工简介'
assert two['edition'] == 'web' and two['cover_url'].startswith('/media/covers/')
assert not two['is_public'] and client.get(two['cover_url']).status_code == 200
ranked = Work.objects.get(source_metadata__source_provider='konorano_rankings')
assert catalog.work_detail(ranked.pk)['cover_url'].startswith('/static/rankings/covers/')
assert rankings.catalog_bindings()[ranked.source_metadata['ranking_key']].pk == ranked.pk
""")

    def test_matching_cover_orphan_can_be_reused(self):
        cache = self.target / "covers"
        cache.mkdir()
        image = next((self.bundle / "covers/cache").glob("*.png"))
        shutil.copyfile(image, cache / image.name)
        receipt = self.transfer()
        self.assertEqual(receipt["cache_files_added"], 3)

    def test_concurrent_imports_serialize(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            imports = [executor.submit(self.transfer) for _ in range(2)]
            receipts = [job.result() for job in imports]
        self.assertEqual(sorted(receipt["created_works"] for receipt in receipts), [0, 3])
        self.assertEqual(sorted(receipt["cache_files_added"] for receipt in receipts), [0, 4])

    def test_export_reads_wal_without_checkpointing_source(self):
        copy = self.root / "live-identity"
        shutil.copytree(self.source, copy, ignore=shutil.ignore_patterns("*-wal", "*-shm"))
        bundle = self.root / "live-bundle"
        with closing(sqlite3.connect(copy / "booklist.sqlite3")) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("UPDATE booklist_work SET wenku8_title='WAL-only title' WHERE wenku8_book_id=123")
            db.commit()
            sqlite = copy / "booklist.sqlite3"
            wal = copy / "booklist.sqlite3-wal"
            before = (sqlite.read_bytes(), wal.read_bytes())
            run_ok(SOURCE_ROOT, copy, SCRIPT, "export", "--bundle", bundle)
            doc = json.loads((bundle / "manifest.json").read_bytes())
            self.assertIn("WAL-only title", {row["title"] for row in doc["works"]})
            self.assertEqual((sqlite.read_bytes(), wal.read_bytes()), before)

    def test_cache_conflict_preserves_target(self):
        cache = self.target / "covers"
        cache.mkdir()
        file = cache / next((self.bundle / "covers/cache").glob("*.png")).name
        file.write_bytes(b"preexisting other asset")
        before = state(self.target)
        self.assertEqual(self.transfer(success=False)["error_code"], "target_cache_cover_conflict")
        self.assertEqual(state(self.target), before)
        self.assertEqual(list(cache.iterdir()), [file])
        self.assertEqual(file.read_bytes(), b"preexisting other asset")

    def test_database_insertion_failure_rolls_back_pending_tags(self):
        with closing(sqlite3.connect(self.target / "booklist.sqlite3")) as db:
            db.execute("CREATE TRIGGER reject_transfer BEFORE INSERT ON booklist_work "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic injected insert failure'); END")
        before = state(self.target)
        self.assertEqual(self.transfer(success=False)["error_type"], "IntegrityError")
        self.assertEqual(state(self.target), before)
        self.assertFalse((self.target / "covers").exists())

    def test_file_publication_failure_rolls_back_db_and_files(self):
        run_ok(ROOT, self.target, "-c", START + """
from pathlib import Path
from unittest.mock import patch
from scripts import course_catalog_transfer as transfer
install = transfer.install_noreplace
def fail_on_metadata(src, dst):
    if dst.name.endswith('.json'):
        raise OSError('synthetic injected file failure')
    install(src, dst)
try:
    with patch.object(transfer, 'install_noreplace', side_effect=fail_on_metadata):
        transfer.import_bundle(Path(""" + repr(str(self.bundle)) + """))
except OSError:
    pass
else:
    raise AssertionError('expected injected failure')
assert Work.objects.count() == 1 and Tag.objects.count() == 0
from django.conf import settings
assert not list(settings.COVER_CACHE_DIR.iterdir())
""")

    def test_moderated_overlap_ends_before_writes(self):
        run_ok(ROOT, self.target, "-c", START + """
Work.objects.filter(wenku8_book_id=124).update(hidden_at=timezone.now())
""")
        before = state(self.target)
        self.assertTrue(self.transfer(success=False)["error_code"].startswith("target_work_conflict:"))
        self.assertEqual(state(self.target), before)
        self.assertFalse((self.target / "covers").exists())

    def test_damaged_bundle_and_wrong_target_are_rejected(self):
        broken = self.root / "damaged-bundle"
        shutil.copytree(self.bundle, broken)
        image = next((broken / "covers/cache").glob("*.png"))
        image.write_bytes(b"corrupt")
        before = state(self.target)
        self.assertEqual(self.transfer(bundle=broken, success=False)["error_code"], "bundle_cover_checksum_mismatch")
        self.assertEqual(state(self.target), before)
        (self.target / ".course-volume").unlink()
        self.assertEqual(self.transfer(success=False)["error_code"], "import_requires_marked_course_volume")
