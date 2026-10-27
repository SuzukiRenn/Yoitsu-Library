"""Offline launch-operations safety checks; no server or real backup access."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.sqlite_backup import backup
from scripts.retain_sqlite_backups import retain
from deploy import offsite_snapshot as export
from deploy import operations_monitor as monitor
NOW=datetime(2026,10,27,2,tzinfo=timezone.utc)


class ServerRetentionTests(unittest.TestCase):
    def test_verified_cleanup_protects_legacy_and_newest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'live.sqlite3';out=root/'copies';out.mkdir()
            with closing(sqlite3.connect(source)) as db:
                db.execute('CREATE TABLE items (id INTEGER PRIMARY KEY)');db.execute('INSERT INTO items VALUES(1)');db.commit()
            def copy(age):
                receipt=backup(source,out,True);old=out/receipt['filename']
                name='booklist-'+(NOW-timedelta(days=age)).strftime('%Y%m%dT%H%M%S-')+f'{age:012x}.sqlite3'
                target=out/name;old.rename(target);old.with_suffix('.verified.json').unlink()
                receipt['filename']=name;target.with_suffix('.verified.json').write_text(json.dumps(receipt))
                return target
            anchor=copy(0);weekly=copy(8);duplicate=copy(9);expired=copy(40)
            legacy=out/'booklist-20250101T000000-aaaaaaaaaaaa.sqlite3';legacy.write_bytes(b'legacy untouched')
            plan=retain(out,anchor,apply=False,now=NOW)
            self.assertEqual(set(plan['delete']),{duplicate.name,expired.name})
            result=retain(out,anchor,now=NOW)
            self.assertEqual(set(result['deleted']),{duplicate.name,expired.name})
            self.assertTrue(anchor.exists() and weekly.exists() and legacy.exists())
            self.assertIn(legacy.name,result['protected_legacy_or_invalid'])

    def test_invalid_anchor_prevents_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'fresh_verified'):
                retain(temp,Path(temp)/'missing',now=NOW)


class ProtocolTests(unittest.TestCase):
    def test_edition_paths_are_disjoint(self):
        self.assertNotEqual(export.scope('anonymous'),export.scope('identity'))
        with self.assertRaises(ValueError):export.scope('../../etc')

    def test_old_anonymous_snapshot_protocol_is_preserved(self):
        with patch.object(export,'create') as create:
            export.dispatch('snapshot');create.assert_called_once_with('anonymous')

    def test_unsafe_commands_are_rejected(self):
        for command in ['snapshot other','snapshot identity; id','download identity ../../etc/passwd','ack identity bad.tar '+ 'a'*64,'id']:
            with self.assertRaises(ValueError):export.dispatch(command)

    def test_identity_download_and_ack_use_identity_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);anon=root/'anonymous';ident=root/'identity';anon.mkdir();ident.mkdir()
            name='booklist-20261027T000000Z-aaaaaaaa.tar'
            (anon/name).write_bytes(b'anonymous');(ident/name).write_bytes(b'identity')
            (ident/(name+'.json')).write_text('{}')
            def scope(kind):return (root,root,ident if kind=='identity' else anon,root)
            output=io.BytesIO()
            with patch.object(export,'scope',side_effect=scope),patch.object(sys,'stdout',SimpleNamespace(buffer=output)):
                export.dispatch('download identity '+name)
            self.assertEqual(output.getvalue(),b'identity')
            with patch.object(export,'scope',side_effect=scope),patch('builtins.print'):
                export.dispatch('ack identity '+name+' '+export.digest(ident/name))
            self.assertFalse((ident/name).exists());self.assertTrue((anon/name).exists())
            self.assertEqual(json.loads((ident/'last-verified.json').read_text())['edition'],'identity')

    def test_identity_export_contains_its_config_and_tls(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'data';config=root/'config';output=root/'export';current=root/'release';tls=root/'tls'
            for p in [data,config,current,tls]:p.mkdir()
            (data/'covers').mkdir()
            with closing(sqlite3.connect(data/'booklist.sqlite3')) as db:
                db.execute('CREATE TABLE booklist_work(id INTEGER PRIMARY KEY)');db.execute('INSERT INTO booklist_work VALUES(1)');db.commit()
            revision='a'*40
            for name in ['production.env','compose-'+revision+'.json','nginx-'+revision+'.conf']:(config/name).write_text('synthetic')
            for name in ['fullchain.pem','privkey.pem']:(tls/name).write_text('synthetic certificate fixture')
            def archive(args,**kwargs):Path(args[args.index('-o')+1]).write_bytes(b'synthetic source archive')
            with patch.object(export,'scope',return_value=(data,config,output,current)),patch.object(export,'SHARED_TLS',tls), \
                 patch.object(export.os,'geteuid',return_value=0,create=True),patch.object(export.shutil,'disk_usage',return_value=SimpleNamespace(free=3*1024**3)), \
                 patch.object(export.subprocess,'check_output',return_value=revision+'\n'),patch.object(export.subprocess,'run',side_effect=archive),patch('builtins.print'):
                export.create('identity')
            bundle=next(output.glob('*.tar'))
            with tarfile.open(bundle) as tar:
                names=tar.getnames();manifest=json.load(tar.extractfile('manifest.json'))
            self.assertEqual(manifest['edition'],'identity')
            self.assertIn('config/production.env',names);self.assertIn('shared-tls/privkey.pem',names)
            self.assertNotIn('config/predeploy.env',names)


class MonitorTests(unittest.TestCase):
    def test_backup_and_offsite_age_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'backups').mkdir();off=root/'offsite';off.mkdir()
            profile={'state':root,'offsite':off}
            (root/'backups/backup-health.json').write_text(json.dumps({'status':'verified','checked_at':NOW.isoformat()}))
            (off/'last-verified.json').write_text(json.dumps({'edition':'identity','verified_at':NOW.isoformat()}))
            self.assertEqual(monitor.backup_findings('identity',profile,NOW),[])
            self.assertEqual(len(monitor.backup_findings('identity',profile,NOW+timedelta(hours=49))),2)

    def test_missing_service_is_a_finding(self):
        with patch.object(monitor,'command',return_value=''):
            self.assertEqual(len(monitor.service_findings('identity',{'project':'fixture'})),4)

    def test_change_log_deduplicates_and_records_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'monitor'
            value={'checked_at':NOW.isoformat(),'status':'needs_attention','findings':['test:failure']}
            monitor.save(value,root);monitor.save(value,root)
            monitor.save(dict(value,status='healthy',findings=[]),root)
            events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
            self.assertEqual([e['event'] for e in events],['changed','recovered'])

    def test_collect_never_claims_mail_delivery(self):
        with patch.object(monitor,'backup_findings',return_value=[]),patch.object(monitor,'service_findings',return_value=[]), \
             patch.object(monitor.shutil,'disk_usage',return_value=SimpleNamespace(free=10*1024**3,total=20*1024**3)),patch.object(monitor,'command',return_value='ok'):
            result=monitor.collect(NOW)
        self.assertEqual(result['status'],'healthy');self.assertFalse(result['notification']['email_enabled'])


if __name__=='__main__':unittest.main()
