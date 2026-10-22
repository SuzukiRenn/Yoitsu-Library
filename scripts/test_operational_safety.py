"""Failure-boundary tests for offsite restore and DNS record ownership."""
import hashlib
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy import dns_hook
from deploy.manage_renewal import proxy_target
from deploy import manage_renewal
from scripts.verify_offsite_backup import verify


class DNSOwnershipTests(unittest.TestCase):
    def test_other_domains_never_reach_api(self):
        with patch.object(dns_hook, 'api') as api:
            with self.assertRaises(ValueError):
                dns_hook.hook('auth', {'CERTBOT_DOMAIN': 'example.com', 'CERTBOT_VALIDATION': 'x'*43})
            api.assert_not_called()

    def test_cleanup_cannot_delete_a_different_record(self):
        token = 'x'*43
        digest = hashlib.sha256(token.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state/(digest+'.json')).write_text(json.dumps({
                'domain': dns_hook.DOMAIN, 'digest': digest, 'record_id': '123'}))
            with patch.object(dns_hook, 'STATE', state), patch.object(dns_hook, 'api') as api:
                with self.assertRaises(RuntimeError):
                    dns_hook.hook('cleanup', {'CERTBOT_DOMAIN': dns_hook.DOMAIN,
                                            'CERTBOT_VALIDATION': token, 'CERTBOT_AUTH_OUTPUT': '456'})
                api.assert_not_called()


class ProxyTargetTests(unittest.TestCase):
    def test_private_and_public_proxy_use_existing_local_endpoint(self):
        for ip, port in [('127.0.0.1', '8443'), ('0.0.0.0', '443')]:
            self.assertEqual(proxy_target({'HostConfig': {'PortBindings': {
                '8443/tcp': [{'HostIp': ip, 'HostPort': port}]}}}), '127.0.0.1:'+port)

    def test_missing_https_binding_is_rejected(self):
        with self.assertRaises(RuntimeError):
            proxy_target({'HostConfig': {'PortBindings': {}}})


class CertificateRetryTests(unittest.TestCase):
    def test_ca_read_timeout_retries_once_and_cleans_each_container(self):
        responses = [SimpleNamespace(returncode=1, stdout='ReadTimeout'),
                     SimpleNamespace(returncode=0),
                     SimpleNamespace(returncode=0, stdout='drill complete'),
                     SimpleNamespace(returncode=0)]
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(manage_renewal, 'ROOT', Path(temporary)), \
                 patch.object(manage_renewal.subprocess, 'run', side_effect=responses) as run, \
                 patch.object(manage_renewal.time, 'sleep') as sleep:
                manage_renewal.certbot('reconfigure')
                self.assertEqual(sum(call.args[0][:2] == ['docker', 'run'] for call in run.call_args_list), 2)
                self.assertEqual(sum(call.args[0][:3] == ['docker', 'rm', '-f'] for call in run.call_args_list), 2)
                sleep.assert_called_once_with(30)

    def test_uncleaned_dns_record_blocks_automatic_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)/'dns-state'; state.mkdir()
            (state/'challenge.json').write_text('{}')
            with patch.object(manage_renewal, 'ROOT', Path(temporary)), \
                 patch.object(manage_renewal.subprocess, 'run', side_effect=[
                     SimpleNamespace(returncode=1, stdout='ReadTimeout'), SimpleNamespace(returncode=0)]) as run, \
                 patch.object(manage_renewal.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError):
                    manage_renewal.certbot('reconfigure')
                self.assertEqual(sum(call.args[0][:2] == ['docker', 'run'] for call in run.call_args_list), 1)
                sleep.assert_not_called()


class OffsiteTests(unittest.TestCase):
    def bundle(self, folder, unsafe=False):
        database = folder/'fixture.sqlite3'
        with closing(sqlite3.connect(database)) as db:
            db.execute('CREATE TABLE booklist_work(id INTEGER PRIMARY KEY, title TEXT)')
            db.execute("INSERT INTO booklist_work VALUES(1,'Preserved recommendation')")
            db.commit()
        raw = database.read_bytes()
        name = '../escaped' if unsafe else 'data/booklist.sqlite3'
        manifest = {'works': 1, 'files': {name: {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}}}
        archive = folder/'backup.tar'
        with tarfile.open(archive, 'w') as tar:
            for member, body in [(name, raw), ('manifest.json', json.dumps(manifest).encode())]:
                info = tarfile.TarInfo(member); info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        receipt = {'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                   'bytes': archive.stat().st_size, 'works': 1}
        return archive, receipt

    def test_verified_bundle_restores_independently(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = verify(*self.bundle(Path(temporary)))
            self.assertTrue(result['isolated_restore_verified'])
            self.assertEqual(result['foreign_key_errors'], 0)

    def test_changed_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, receipt = self.bundle(Path(temporary))
            with archive.open('ab') as stream: stream.write(b'changed')
            with self.assertRaisesRegex(ValueError, 'transport_hash_mismatch'):
                verify(archive, receipt)

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'unsafe_archive_member'):
                verify(*self.bundle(Path(temporary), unsafe=True))


if __name__ == '__main__':
    unittest.main()
