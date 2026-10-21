import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.error import HTTPError
from booklist.collection_parallel import PacedOpener,concurrency_limit

class ParallelPacingTests(TestCase):
    def test_workers_share_spacing_and_stricter_policy(self):
        now=[100.0]; starts=[]
        class Fake:
            def open(self,request,timeout):
                starts.append(now[0]);now[0]+=3;return object()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'pacing.json'
            a=PacedOpener(Fake(),path,lambda:10,clock=lambda:now[0],sleep=lambda t:now.__setitem__(0,now[0]+t))
            b=PacedOpener(Fake(),path,lambda:14,clock=lambda:now[0],sleep=lambda t:now.__setitem__(0,now[0]+t))
            a.open(None,15);b.open(None,15);a.open(None,15)
            self.assertEqual(starts,[100,114,128])
            self.assertEqual(b.wait_seconds,11)
            self.assertEqual(json.loads(path.read_text())['interval'],14)

    def test_restriction_stops_other_workers_without_another_call(self):
        class Restricted:
            def open(self,*args,**kwargs):raise HTTPError('local',429,'limit',{},None)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'pacing.json'
            a=PacedOpener(Restricted(),path,lambda:10)
            with self.assertRaises(HTTPError):a.open(None,15)
            with self.assertRaisesRegex(RuntimeError,'stopped'):a.open(None,15)

    def test_corrupt_gate_fails_closed(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'pacing.json';path.write_text('{bad')
            with self.assertRaises(ValueError):PacedOpener(None,path,lambda:10).open(None,15)

    def test_memory_bounds(self):
        for available,expected in [(None,1),(200,1),(767,1),(768,2),(16000,2)]:
            self.assertEqual(concurrency_limit(available),expected)

    def test_trial_has_a_hard_request_budget_then_reverts(self):
        now=[100.0];starts=[]
        class Fake:
            def open(self,request,timeout):starts.append(now[0]);return object()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'pacing.json'
            path.write_text(json.dumps({'configured_interval':8,'interval':8,
                'trial_requests_remaining':2,'trial_expires_at':10**12}))
            gate=PacedOpener(Fake(),path,lambda:8,clock=lambda:now[0],
                sleep=lambda t:now.__setitem__(0,now[0]+t))
            for _ in range(4):gate.open(None,15)
            self.assertEqual(starts,[100,108,118,128])
            self.assertEqual(json.loads(path.read_text())['configured_interval'],10)

    def test_transport_failure_rolls_back_without_relaxing_robot_rules(self):
        now=[100.0];starts=[]
        class Fake:
            def open(self,request,timeout):starts.append(now[0]);return object()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'pacing.json'
            path.write_text(json.dumps({'configured_interval':8,'interval':8,
                'trial_requests_remaining':40,'trial_expires_at':10**12}))
            gate=PacedOpener(Fake(),path,lambda:8,clock=lambda:now[0],
                sleep=lambda t:now.__setitem__(0,now[0]+t))
            gate.open(None,15)
            gate.record_result({'result':'transport_failed','status':None,'elapsed_ms':100})
            gate.open(None,15)
            self.assertEqual(starts,[100,110])
            strict=PacedOpener(Fake(),path,lambda:20,clock=lambda:now[0],
                sleep=lambda t:now.__setitem__(0,now[0]+t))
            strict.open(None,15)
            self.assertEqual(starts[-1],130)

    def test_steady_mode_keeps_eight_seconds_and_failure_fallback_is_sticky(self):
        now=[100.0];starts=[]
        class Fake:
            def open(self,request,timeout):starts.append(now[0]);return object()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'pacing.json'
            path.write_text(json.dumps({'mode':'steady','configured_interval':8,'interval':8}))
            gate=PacedOpener(Fake(),path,lambda:8,clock=lambda:now[0],
                sleep=lambda t:now.__setitem__(0,now[0]+t))
            for _ in range(42):gate.open(None,15)
            self.assertEqual(starts[-1]-starts[-2],8)
            gate.record_result({'result':'received','status':200,'elapsed_ms':16000})
            gate.open(None,15);gate.open(None,15)
            self.assertEqual(starts[-1]-starts[-2],10)
            self.assertEqual(json.loads(path.read_text())['configured_interval'],10)

class ParallelCoordinatorTests(TestCase):
    def test_two_disjoint_collectors_with_a_single_importer_and_preserved_ledger(self):
        import importlib.util
        import threading
        import time
        from unittest.mock import patch
        root=Path(__file__).resolve().parents[2]
        script=root/'.runtime/collect_retry_220_parallel.py'
        if not script.is_file():self.skipTest('Local frozen queue coordinator is not part of a fresh checkout')
        spec=importlib.util.spec_from_file_location('queue_under_test',script)
        coordinator=importlib.util.module_from_spec(spec);spec.loader.exec_module(coordinator)
        with TemporaryDirectory() as folder:
            base=Path(folder);out=base/'out';out.mkdir()
            review=base/'review.json';review.write_text('{}')
            state={'status':'running','requested_ids':list(range(1,41)),'total_planned_batches':2,
                'batches':[],'imported_ids':[99],'skipped_existing_ids':[],'quarantined':[],'failure_streak':0}
            (out/'queue.json').write_text(json.dumps(state))
            running=[0,0];claimed=[];import_threads=[];lock=threading.Lock()
            def collect(entry,*args):
                with lock:
                    running[0]+=1;running[1]=max(running);claimed.extend(entry['ids'])
                time.sleep(.06)
                with lock:running[0]-=1
                return base,{}
            def finish(entry,directory,source,state,save,env):
                import_threads.append(threading.get_ident())
                state['imported_ids'].extend(entry['ids']);entry['status']='completed';save()
            with patch.object(coordinator,'ROOT',base),patch.object(coordinator,'OUT',out),\
                 patch.object(coordinator,'PACING',out/'pacing.json'),\
                 patch.object(coordinator.legacy,'REVIEW',review),\
                 patch.object(coordinator.legacy,'existing_ids',return_value=set()),\
                 patch.object(coordinator,'validate_queue'),\
                 patch.object(coordinator,'memory_available_mib',return_value=2048),\
                 patch.object(coordinator,'collect',side_effect=collect),patch.object(coordinator,'finish',side_effect=finish):
                self.assertEqual(coordinator.main(),0)
            final=json.loads((out/'queue.json').read_text())
            self.assertEqual(running[1],2)
            self.assertEqual(len(claimed),len(set(claimed)))
            self.assertEqual(set(claimed),set(range(1,41)))
            self.assertEqual(set(import_threads),{threading.get_ident()})
            self.assertEqual(set(final['imported_ids']),{99,*range(1,41)})
            self.assertEqual(final['status'],'completed')

    def test_frozen_scope_and_overlapping_assignments_are_rejected(self):
        import importlib.util
        import copy
        root=Path(__file__).resolve().parents[2]
        script=root/'.runtime/collect_retry_220_parallel.py'
        if not script.is_file():self.skipTest('Local frozen queue coordinator is not part of a fresh checkout')
        spec=importlib.util.spec_from_file_location('queue_validation_test',script)
        coordinator=importlib.util.module_from_spec(spec);spec.loader.exec_module(coordinator)
        ids=list(range(1,4162))
        review={'success':True,'missing_pages':[],'new_ids':ids,'files':[]}
        state={'requested_ids':ids,'total_planned_batches':209,'initial_source_count':218,
               'imported_ids':[],'batches':[{'number':1,'ids':ids[:20],'status':'collecting'}]}
        coordinator.validate_queue(state,review)
        changed=copy.deepcopy(state);changed['batches'].append({'number':2,'ids':[1],'status':'collecting'})
        with self.assertRaisesRegex(RuntimeError,'Overlapping'):coordinator.validate_queue(changed,review)
        changed=copy.deepcopy(state);changed['requested_ids'].append(90000)
        with self.assertRaisesRegex(RuntimeError,'scope changed'):coordinator.validate_queue(changed,review)
        changed=copy.deepcopy(state);changed['total_planned_batches']=210
        with self.assertRaisesRegex(RuntimeError,'plan changed'):coordinator.validate_queue(changed,review)
