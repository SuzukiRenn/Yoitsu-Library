from pathlib import Path
import socket
from unittest.mock import patch
from django.test import TestCase
from booklist.models import Work
from integrations.wenku8.offline import SnapshotError
from scripts.verify_online_sample import import_and_check


FIXTURE = Path(__file__).absolute().parents[2]/'tests/fixtures/wenku8/details/4151.htm'


class OnlineSampleImportTests(TestCase):
    def test_callback_validates_and_reads_back_without_network(self):
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Unexpected network')):
            result = import_and_check(FIXTURE.read_bytes())
        self.assertTrue(all(result['checks'].values()))
        self.assertEqual(result['tag_count'], 6)
        self.assertEqual(Work.objects.count(), 1)

    def test_incomplete_document_is_rejected_before_database_write(self):
        raw = FIXTURE.read_text(encoding='utf-8').rsplit('</body>', 1)[0].encode('utf-8')
        with self.assertRaises(SnapshotError):
            import_and_check(raw)
        self.assertFalse(Work.objects.exists())
