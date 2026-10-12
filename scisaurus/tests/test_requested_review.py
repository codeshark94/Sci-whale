"""Requested isolated feedback, retained paid accounting and native primary continuity."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scisaurus.core.errors import StateError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.dsh_batch import DshBatchError
from scisaurus.runtime.models import ModelResult
from scisaurus.runtime.primary_work import native_tool_handler,work_assignment,work_request
from scisaurus.runtime.requested_review import request_review,reconcile_review,review_is_accounted


class RequestedReviewTests(unittest.TestCase):
    def test_common_native_tool_requests_feedback_without_workbench_author(self):
        assignment=work_assignment({}, {'kind':'research_work'});bench=Mock();review=Mock(return_value={'outcome':'ok','feedback':{'notes':'unresolved'}});record={}
        result=native_tool_handler(assignment,bench,record,review=review)({'operation':'request_review','arguments':{'evidence':[1,2]}})
        self.assertEqual(result['feedback']['notes'],'unresolved');bench.execute.assert_not_called();review.assert_called_once_with({'evidence':[1,2]})
        self.assertEqual(record['observations'],[result])

    def test_review_input_has_no_primary_history_and_returns_hold_without_veto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);producer=root/'producer';producer.mkdir();shared=root/'shared';shared.mkdir()
            backend={'read_roots':[str(producer),str(root),str(shared)],'pinned_files':{str(producer/'composition.json'):'old',str(shared/'tool.js'):'source'},'command':[sys.executable]}
            configured={'author_backend':backend,'runtime_python':sys.executable};record=[]
            client=Mock();client.runner.task_contract={};client.complete.return_value=ModelResult(json.dumps({'notes':'hold: missing control'}),'fixed',{'model_calls':1},0,'stop',response_metadata={'receipt':'offline-review'})
            with patch('scisaurus.runtime.requested_review.guard_backend',side_effect=lambda backend,*args,**kwargs:backend) as guard, \
                 patch('scisaurus.runtime.program_sandbox.sandbox_read_grants',return_value=([],[])), \
                 patch('scisaurus.runtime.program_sandbox.require_isolated_read_grants') as isolation, \
                 patch('scisaurus.runtime.requested_review.PrimaryClient',return_value=client) as primary:
                reply=request_review(configured,producer/'reviews',{'task':'check current raw','evidence':[1,2]},deadline=time.monotonic()+20,isolated_from=[producer],record=record.append)
            self.assertIn('hold',reply['feedback']['notes']);self.assertEqual([x['status'] for x in record],['started','completed'])
            delivered=json.loads(client.complete.call_args.kwargs['prompt'])
            self.assertNotIn('development_session',delivered);self.assertNotIn('research_work',delivered);self.assertNotIn('checkpoint',delivered)
            self.assertEqual(set(delivered['scientific_software_tools']['actions']),{'run','inspect_runtime','list_runtimes','check_environment'})
            self.assertNotIn('development_session',primary.call_args.kwargs)
            self.assertEqual(guard.call_args.args[0]['read_roots'],[str(shared)])
            self.assertNotIn(str(producer/'composition.json'),guard.call_args.args[0]['pinned_files'])
            self.assertEqual(isolation.call_args.kwargs['isolated_from'],[producer])
            self.assertEqual(backend['read_roots'],[str(producer),str(root),str(shared)])

    def unknown_fixture(self,root):
        job=root/'job';(job/'input').mkdir(parents=True)
        assignment={'evidence':[1,2]};raw=canonical_bytes(assignment);(job/'input/assignment.json').write_bytes(raw)
        usage={'model_calls':3,'input_tokens':71,'output_tokens':18};backend={'model':'fixed'}
        receipt={'schema_version':'dsh-batch-receipt-1','status':'result_unknown','process_reaped':True,
            'job_directory':str(job),'config_sha256':hashlib.sha256(canonical_bytes(backend)).hexdigest(),
            'input_sha256':{'assignment.json':hashlib.sha256(raw).hexdigest()},'outputs':{},'usage':usage}
        path=job/'receipt.json';path.write_bytes(canonical_bytes(receipt))
        return {'kind':'requested_review','status':'result_unknown','dispatch_root':str(root),'receipt':str(path),
            'assignment':assignment,'backend':backend,'usage':usage}

    def test_reconciliation_keeps_unknown_receipt_and_nonzero_paid_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dispatch=self.unknown_fixture(root);path=Path(dispatch['receipt']);original=path.read_bytes();before=deepcopy(dispatch)
            self.assertFalse(review_is_accounted(dispatch))
            proof=reconcile_review(dispatch,receipt_sha256=hashlib.sha256(original).hexdigest())
            self.assertTrue(review_is_accounted(dispatch));self.assertEqual(path.read_bytes(),original);self.assertEqual(dispatch,before)
            self.assertEqual(json.loads(proof.read_bytes())['answer_status'],'result_unknown')
            self.assertEqual(reconcile_review(dispatch,receipt_sha256=hashlib.sha256(original).hexdigest()),proof)
            for changed in [dict(dispatch,usage={}),dict(dispatch,assignment={'evidence':[99]})]:
                with self.assertRaises(StateError):reconcile_review(changed,receipt_sha256=hashlib.sha256(original).hexdigest())
            with self.assertRaises(StateError):reconcile_review(dispatch,receipt_sha256='0'*64)

    def test_primary_transport_settlement_does_not_erase_nested_review_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dispatch=self.unknown_fixture(root/'review');job=root/'primary';(job/'input').mkdir(parents=True)
            (job/'input/assignment.json').write_text('{}');(job/'events.jsonl').write_text('');rp=job/'receipt.json';rp.write_text('{}')
            context={'record':{'review_dispatches':[dispatch]},'receipt_path':rp,'receipt':{'status':'completed','usage':{'model_calls':1}},
                'state':{'session_id':'primary','calls':[]},'response':None,'response_path':job/'notes'}
            with self.assertRaisesRegex(StateError,'review dispatch'):work_request(context)
            reconcile_review(dispatch,receipt_sha256=hashlib.sha256(Path(dispatch['receipt']).read_bytes()).hexdigest())
            packet=work_request(context);self.assertEqual(packet['checkpoint']['review_dispatches'],[dispatch])
            self.assertEqual(packet['checkpoint']['review_dispatches'][0]['usage']['model_calls'],3)

    def test_known_run_arguments_receive_defaults_and_raw_observation(self):
        assignment=work_assignment({}, {'kind':'research_work'});bench=Mock();bench.execute.return_value={'outcome':'ok','stdout':'plain raw'}
        result=native_tool_handler(assignment,bench,{})({'operation':'run','arguments':{'source':'print(1)','runtime':'declared'}})
        self.assertEqual(result['stdout'],'plain raw');self.assertEqual(bench.execute.call_args.args[0]['arguments'],{'input':{},'purpose':'scientific_computation','expected':None,'source':'print(1)','runtime':'declared'})

    def test_explicit_stop_preserves_review_receipt_and_usage(self):
        from scisaurus.runtime.run_control import RunPausedError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);error=RunPausedError('operator stopped');error.receipt=str(root/'receipt.json');error.usage={'model_calls':2,'input_tokens':31,'output_tokens':9}
            client=Mock();client.runner.task_contract={};client.complete.side_effect=error;records=[]
            backend={'read_roots':[],'pinned_files':{},'command':[sys.executable]}
            with patch('scisaurus.runtime.requested_review.guard_backend',side_effect=lambda backend,*args,**kwargs:backend), \
                 patch('scisaurus.runtime.program_sandbox.sandbox_read_grants',return_value=([],[])), \
                 patch('scisaurus.runtime.program_sandbox.require_isolated_read_grants'), \
                 patch('scisaurus.runtime.requested_review.PrimaryClient',return_value=client):
                with self.assertRaises(RunPausedError):request_review({'author_backend':backend,'runtime_python':sys.executable},root/'review',{'evidence':[1]},deadline=time.monotonic()+10,isolated_from=[],record=records.append)
            self.assertEqual(records[-1]['status'],'result_unknown');self.assertEqual(records[-1]['receipt'],error.receipt)
            self.assertEqual(records[-1]['usage'],error.usage);self.assertEqual(client.complete.call_count,1)

    def test_repeated_review_evidence_retains_each_paid_dispatch(self):
        from scisaurus.runtime.requested_review import retain_review_dispatch
        record={}
        for root,receipt in [('first','receipt-1'),('second','receipt-2')]:
            retain_review_dispatch(record,{'dispatch_root':root,'request_identity':'same-evidence','status':'started'})
            retain_review_dispatch(record,{'dispatch_root':root,'request_identity':'same-evidence','status':'completed','receipt':receipt,'usage':{'model_calls':2,'input_tokens':30}})
        self.assertEqual(len(record['review_dispatches']),2)
        self.assertEqual(record['review_usage'],{'model_calls':4,'input_tokens':60})

    def test_nested_quota_is_visible_after_primary_transport_settlement(self):
        from scisaurus.runtime.requested_review import receipt_has_provider_hold
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'native-tool-results').mkdir()
            raw=canonical_bytes({'dispatch':{'receipt':'reviewer','usage':{'model_calls':2},'failure':{'kind':'provider_rate_limit','details':{'provider_error_kind':'quota_exhausted'}}}})
            (root/'native-tool-results/response.json').write_bytes(raw)
            path=root/'receipt.json';path.write_bytes(canonical_bytes({'status':'completed','native_tool_calls':[{'status':'responded','response_file':'response.json','response_sha256':hashlib.sha256(raw).hexdigest()}]}))
            self.assertTrue(receipt_has_provider_hold(path))
            (root/'native-tool-results/response.json').write_text('{}')
            with self.assertRaises(StateError):receipt_has_provider_hold(path)


class PrimaryBackendTests(unittest.TestCase):
    def test_initial_and_continued_bindings_keep_same_session_and_history(self):
        from scisaurus.tests.test_development_session import DevelopmentSessionTests
        from scisaurus.runtime.development_session import DevelopmentSession
        from scisaurus.runtime.primary_client import prepare_primary_backend,current_primary_backend
        fixture=DevelopmentSessionTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        with DevelopmentSession(fixture.binding).lease(fixture.config,time.monotonic()+1) as session:
            original=deepcopy(session.state)
        backend=prepare_primary_backend(fixture.config,fixture.project/'intake-controller',sys.executable,fixture.binding)
        state=json.loads(Path(fixture.binding['root'],'session.json').read_bytes())
        self.assertEqual(state['session_id'],original['session_id']);self.assertEqual(state['calls'],[])
        with DevelopmentSession(fixture.binding).lease(backend,time.monotonic()+1):pass
        self.assertEqual(current_primary_backend(fixture.binding,fixture.config),backend)
        self.assertEqual(prepare_primary_backend(backend,fixture.project/'intake-controller',sys.executable,fixture.binding),backend)
        self.assertEqual(json.loads(Path(fixture.binding['root'],'session.json').read_bytes()),state)


if __name__=='__main__':unittest.main()
