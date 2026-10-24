"""Fresh offline bootstrap must not depend on local trial artifacts."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.collect_bilinovel_catalog import initialize,Worker
from scripts import bili_metadata

class BootstrapTests(unittest.TestCase):
    def test_fresh_queue_and_frozen_adapter(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'queue';config=initialize(out)
            self.assertTrue(config['discover_page_limit'])
            worker=Worker(out)
            try:
                self.assertEqual(worker.db.execute('select count(*) from works').fetchone()[0],0)
                self.assertEqual(worker.db.execute('select number,status from pages').fetchone()['status'],'pending')
                self.assertTrue(callable(worker.adapter.extract))
                self.assertEqual(worker.adapter.key('Ａ b!'),'ab')
            finally:worker.db.close()
    def test_parser_accepts_complete_synthetic_metadata(self):
        fields={'title':'Test','novel:book_name':'Test','novel:author':'Author','url':'https://www.bilinovel.com/novel/1.html','novel:category':'电击文库','image':'https://example.test/cover.jpg','novel:update_time':'2026-10-23'}
        raw='<meta charset="utf-8">'+''.join(f'<meta property="og:{key}" content="{value}">' for key,value in fields.items())
        raw+='<h1 class="book-title">Test</h1><div id="bookSummary"><content>Summary</content></div><span class="tag-small-group"><a>日本轻小说</a></span>'
        item=bili_metadata.extract(raw.encode(),1)
        self.assertEqual(item['edition'],'book');self.assertEqual(item['description'],'Summary')

if __name__=='__main__':unittest.main()
