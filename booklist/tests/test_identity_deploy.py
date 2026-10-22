"""Guard the deployment boundary and catalog-only transfer contract."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from deploy.identity_config import configuration, PROJECT
from scripts.identity_catalog import export, load, TABLES, EMPTY


class IdentityDeploymentTests(unittest.TestCase):
    def test_identity_compose_has_separate_state_and_loopback_only_tls(self):
        config, nginx = configuration('a' * 40)
        self.assertEqual(config['name'], PROJECT)
        self.assertEqual(config['services']['proxy']['ports'], ['127.0.0.1:8444:8443'])
        self.assertIn('proxy_set_header Host $http_host;', nginx)
        self.assertNotIn('ports', config['services']['app'])
        self.assertEqual(config['services']['app']['networks']['edge'], {},
                         'One-off tools must not inherit the running app static IP')
        self.assertTrue(config['networks']['private']['internal'])
        self.assertTrue(config['networks']['edge']['internal'])
        for name, service in config['services'].items():
            for volume in service['volumes']:
                self.assertNotIn('/var/lib/shared-booklist/predeploy/', volume)
            if name != 'proxy':
                self.assertEqual(service['env_file'], ['/etc/shared-booklist-identity/production.env'])
                self.assertTrue(service['image'].startswith('shared-booklist-identity:'))
        self.assertEqual(config['services']['proxy']['volumes'][1],
                         '/etc/shared-booklist/ssh-access/tls:/etc/nginx/tls:ro')

    def make_databases(self, root):
        source = root / 'source'; source.mkdir()
        original = source / 'booklist.sqlite3'; target = root / 'target.sqlite3'
        schema = [
            'CREATE TABLE booklist_work(id INTEGER PRIMARY KEY,title TEXT)',
            'CREATE TABLE booklist_tag(id INTEGER PRIMARY KEY,name TEXT)',
            'CREATE TABLE booklist_sourcefieldstate(id INTEGER PRIMARY KEY,work_id INTEGER REFERENCES booklist_work(id))',
            'CREATE TABLE booklist_tagsource(id INTEGER PRIMARY KEY,tag_id INTEGER REFERENCES booklist_tag(id))',
            'CREATE TABLE booklist_workalias(id INTEGER PRIMARY KEY,work_id INTEGER,created_by_id INTEGER)',
            'CREATE TABLE booklist_worktag(id INTEGER PRIMARY KEY,work_id INTEGER,tag_id INTEGER,source_type TEXT,anonymous_client_id INTEGER,admin_id INTEGER)',
            'CREATE TABLE booklist_tagsuppression(id INTEGER PRIMARY KEY,work_id INTEGER,tag_id INTEGER,is_active INTEGER,scope TEXT)',
        ] + ['CREATE TABLE ' + table + '(id INTEGER PRIMARY KEY)' for table in EMPTY]
        for path in (original, target):
            with closing(sqlite3.connect(path)) as db:
                for statement in schema:
                    db.execute(statement)
        with closing(sqlite3.connect(original)) as db:
            db.execute("INSERT INTO booklist_work VALUES(1,'Catalog title')")
            db.executemany('INSERT INTO booklist_tag VALUES(?,?)', [(1, 'source'), (2, 'suppressed')])
            db.execute('INSERT INTO booklist_workalias VALUES(1,1,7)')
            db.executemany('INSERT INTO booklist_worktag VALUES(?,?,?,?,?,?)',
                           [(1, 1, 1, 'source', None, None), (2, 1, 1, 'user', 8, None),
                            (3, 1, 1, 'admin', None, 7), (4, 1, 2, 'source', None, None)])
            db.execute("INSERT INTO booklist_tagsuppression VALUES(1,1,2,1,'all')")
            for table in EMPTY:
                db.execute('INSERT INTO ' + table + '(id) VALUES(1)')
            db.commit()
        return source, target

    def test_catalog_omits_accounts_interactions_and_private_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, target = self.make_databases(root)
            receipt = export(source, root / 'packet')
            catalog = root / 'packet/catalog.json'
            payload = json.loads(catalog.read_text())
            self.assertEqual(set(payload['tables']), set(TABLES))
            self.assertEqual(len(payload['tables']['booklist_worktag']), 1)
            self.assertIsNone(payload['tables']['booklist_workalias'][0]['created_by_id'])
            self.assertFalse(receipt['accounts_and_interactions_exported'])
            load(target, catalog)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute('SELECT title FROM booklist_work').fetchone()[0], 'Catalog title')
                for table in EMPTY:
                    self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
            with self.assertRaisesRegex(ValueError, 'empty identity database'):
                load(target, catalog)

    def test_invalid_catalog_rolls_back_and_cannot_add_principals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source, target = self.make_databases(root)
            export(source, root / 'packet')
            catalog = root / 'packet/catalog.json'
            payload = json.loads(catalog.read_text())
            original = copy.deepcopy(payload)
            payload['tables']['booklist_worktag'][0]['anonymous_client_id'] = 99
            catalog.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, 'contribution principals'):
                load(target, catalog)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM booklist_work').fetchone()[0], 0)
            original['tables']['booklist_account'] = []
            catalog.write_text(json.dumps(original))
            with self.assertRaisesRegex(ValueError, 'whitelist'):
                load(target, catalog)
