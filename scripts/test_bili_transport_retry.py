"""Offline checks for bounded retries; makes no network or application DB calls."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('collector',Path(__file__).with_name('collect_bilinovel_catalog.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class RetryTests(unittest.TestCase):
    def run_request(self,responses,stop_during_wait=False):
        with tempfile.TemporaryDirectory() as directory:
            w=c.Worker.__new__(c.Worker);w.out=Path(directory);w.rules=None;w.delay=3
            w.last_finished=0;w.opener=None;w.checkpoint=lambda **kw:None
            w.db=sqlite3.connect(':memory:')
            w.db.execute('create table requests(at,kind,url,record_json)')
            clock=[100.0]
            def sleep(seconds):
                clock[0]+=seconds
                if stop_during_wait:(w.out/'STOP').touch()
            try:
                with patch.object(c.net,'fetch',side_effect=responses) as fetch,patch.object(c.time,'monotonic',side_effect=lambda:clock[0]),patch.object(c.time,'sleep',side_effect=sleep):
                    result=w.request('https://www.bilinovel.com/robots.txt',65536,'robots')
                return result,fetch.call_count,w.db.execute('select count(*) from requests').fetchone()[0],clock[0]
            finally:w.db.close()

    def test_transient_then_success(self):
        bad=({'status':None,'result':'transport_failed','cause_class':'SSLEOFError'},None)
        good=({'status':200,'result':'received'},b'ok')
        result,calls,records,clock=self.run_request([bad,good])
        self.assertEqual((calls,records,clock),(2,2,110.0));self.assertEqual(result[1],b'ok')

    def test_exhausted_is_bounded(self):
        bad=({'status':None,'result':'transport_failed','cause_class':'TimeoutError'},None)
        _,calls,records,clock=self.run_request([bad]*3)
        self.assertEqual((calls,records,clock),(3,3,140.0))

    def test_partial_200_body_retries_without_accepting_partial_data(self):
        bad=({'status':200,'result':'transport_failed','phase':'body','error_class':'IncompleteRead','body_complete':False},None)
        good=({'status':200,'result':'received','body_complete':True},b'complete')
        result,calls,records,_=self.run_request([bad,good])
        self.assertEqual((calls,records),(2,2))
        self.assertEqual(result[1],b'complete')
        self.assertFalse(c.transient_transport({'status':403,'result':'transport_failed','error_class':'IncompleteRead'}))

    def test_http_and_certificate_errors_never_retry(self):
        for record in [{'status':s,'result':'http_status_stop'} for s in (401,403,404,429,500)]+[{'status':None,'result':'transport_failed','cause_class':'SSLCertVerificationError'}]:
            with self.subTest(record=record):
                _,calls,records,_=self.run_request([(record,None)])
                self.assertEqual((calls,records),(1,1))

    def test_stop_interrupts_backoff(self):
        bad=({'status':None,'result':'transport_failed','cause_class':'SSLEOFError'},None)
        with self.assertRaisesRegex(c.Pause,'user_stop_marker'):
            self.run_request([bad],stop_during_wait=True)

    def test_passive_footer_requires_valid_metadata(self):
        adapter=SimpleNamespace(detail_action=lambda *a:'skip_challenge',extract=lambda raw,bid:{'bili_id':bid})
        self.assertEqual(c.detail_action(adapter,{'status':200},b'complete page',326),'parse')
        adapter.extract=lambda *a:(_ for _ in ()).throw(ValueError('missing_identity'))
        self.assertEqual(c.detail_action(adapter,{'status':200},b'challenge',326),'skip_challenge')
        adapter.extract=lambda *a:{'bili_id':326}
        self.assertEqual(c.detail_action(adapter,{'status':403},b'blocked',326),'skip_challenge')

    def test_listing_passive_footer_and_genuine_challenge(self):
        w=c.Worker.__new__(c.Worker);w.adapter=SimpleNamespace(detail_action=lambda *a:'skip_challenge')
        w.config={'page_limit':180};w.db=None;w.out=None
        raw=b'<div id="pagelink"><strong>1</strong></div><li class="book-li"><a class="book-layout" href="/novel/1.html"><h4 class="book-title">Title</h4></a></li><script src="/cdn-cgi/challenge-platform/test"></script>'
        w.request=lambda *a:({'status':200,'media_type':'text/html'},raw)
        with patch.object(c,'save_page') as save:
            w.listing({'url':'https://www.bilinovel.com/wenku/','number':1})
            save.assert_called_once()
        with self.assertRaises(c.Pause):c.parse_page(b'<title>Just a moment</title>',1,180)

if __name__=='__main__':unittest.main()
