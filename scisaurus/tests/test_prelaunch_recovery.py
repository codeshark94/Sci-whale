"""Never-started reconciliation preserves raw receipts, settled usage and explicit scope."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import IntakePrimaryClient, sha
from scisaurus.runtime.prelaunch_recovery import inspect_failure, prepare_continuation, read_proof
import scisaurus.tests.test_intake_contract as contract_tests


class PrelaunchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=contract_tests.IntakeContractTests('test_guarded_recovery_preserves_history_draft_route_and_is_idempotent')
        self.f.setUp();self.owner=self.f.owner;self.ticket=self.f.prepare();source=json.loads(self.ticket.read_bytes())
        self.transport_validation=patch('scisaurus.runtime.prelaunch_recovery.validate_batch_config',side_effect=deepcopy)
        self.transport_runtime=patch('scisaurus.runtime.prelaunch_recovery.verify_batch_runtime')
        self.transport_validation.start();self.transport_runtime.start()
        state=json.loads(self.owner.session.path.read_bytes());request=source['step_request']
        assignment=deepcopy(self.f.assignment);assignment.update(phase='concept_invention',
            concept_invention=request['active_request'],structured_continuation=request,
            output_contract=request['active_request']['output_contract'])
        stub=object.__new__(IntakePrimaryClient);inputs=stub.input_files(assignment,'one explicit concept continuation')
        job=self.owner.project/'intake-jobs/unstarted';(job/'input').mkdir(parents=True)
        for name,raw in inputs.items():(job/'input'/name).write_bytes(raw)
        manifest={'workspace':str(self.owner.session.root/'work'),
                  'inputs':{str(job/'input'/name):sha(raw) for name,raw in inputs.items()}}
        manifest_path=job/'task-contract.json';manifest_path.write_bytes(canonical_bytes(manifest))
        body={'schema_version':'dsh-batch-receipt-1','status':'prepared','job_directory':str(job),
            'session_id':state['session_id'],'config_sha256':state['config_sha256'],
            'provider':source['backend']['provider'],'model':source['backend']['model'],
            'task_sha256':'a'*64,'input_sha256':{name:sha(raw) for name,raw in inputs.items()},
            'outputs':{},'seed_sha256':{},'usage':{'model_calls':0,'input_tokens':0,'output_tokens':0},
            'development_session':{'binding':self.owner.binding,'prior_receipts':state['calls']},
            'task_contract':{'path':str(manifest_path),'sha256':sha(manifest_path.read_bytes())}}
        self.prepared=job/'receipt.json';self.prepared.write_bytes(canonical_bytes(body))
        wrapper_dir=self.owner.project/'intake-jobs/wrapper';wrapper_dir.mkdir()
        self.wrapper=wrapper_dir/'receipt.json';self.wrapper.write_bytes(canonical_bytes({
            'schema_version':'dsh-batch-receipt-1','status':'not_dispatched','usage':body['usage'],
            'config_sha256':state['config_sha256'],'task_sha256':body['task_sha256'],
            'provider':body['provider'],'model':body['model'],
            'error':'ValidationError: sandbox dependency read roots must be existing absolute directories'}))
        record={'schema_version':'fresh-intake-attempt-1','status':'stopped_on_error',
            'scientific_admission':'not_assessed','session_id':state['session_id'],'usage':body['usage'],
            'receipt':str(self.wrapper),'preparation_sha256':source['preparation_sha256'],
            'backend_binding':source['backend_binding']}
        self.marker=self.ticket.with_suffix('.attempt.json');raw=canonical_bytes(record)
        self.marker.write_bytes(raw);(self.owner.project/'intake-controller'/(sha(raw)+'.json')).write_bytes(raw)
        control_path=self.owner.project/'output/run-control.json';control=json.loads(control_path.read_bytes())
        control['attempt']=str(self.marker);control_path.write_bytes(canonical_bytes(control))
        state['active_job']=str(self.prepared);self.owner.session.state=state;self.owner.session.save()
        self.before=self.owner.session.path.read_bytes();self.originals={p:p.read_bytes() for p in (self.prepared,self.wrapper,self.marker)}
        self.args={'prepared_receipt':str(self.prepared),'prepared_sha256':sha(self.prepared.read_bytes()),
            'wrapper_sha256':sha(self.wrapper.read_bytes()),'attempt_path':str(self.marker)}

    def tearDown(self):
        self.transport_validation.stop();self.transport_runtime.stop();self.f.tearDown()
    def prepare(self):return prepare_continuation(self.owner.root,runtime_python=sys.executable,**self.args)

    def test_owned_reconciliation_and_explicit_ticket_preserve_all_raw_and_paid_history(self):
        inspect_failure(self.owner.root,**self.args);ticket=self.prepare();value=json.loads(ticket.read_bytes())
        state=json.loads(self.owner.session.path.read_bytes());before=json.loads(self.before)
        self.assertIsNone(state['active_job']);self.assertEqual(state['session_id'],before['session_id'])
        self.assertEqual(state['calls'],before['calls']);self.assertEqual(len(state['prelaunch_reconciliations']),1)
        self.assertTrue(all(p.read_bytes()==raw for p,raw in self.originals.items()))
        proof=read_proof(self.owner.project,value['prelaunch_reconciliation_ref'])
        self.assertEqual(proof['disposition'],'never_started_no_scientific_result')
        self.assertEqual(value['step_request'],json.loads(self.ticket.read_bytes())['step_request'])
        self.f.f.preflight(ticket);self.assertEqual(self.prepare(),ticket)

    def test_nonzero_usage_or_launch_evidence_or_changed_wrapper_refuses_without_mutation(self):
        for field,value in [('usage',{'model_calls':1,'input_tokens':1,'output_tokens':0}),('pid',123),('transport_turns',[1])]:
            body=json.loads(self.originals[self.prepared]);body[field]=value;self.prepared.write_bytes(canonical_bytes(body))
            args={**self.args,'prepared_sha256':sha(self.prepared.read_bytes())}
            with self.assertRaises(StateError):prepare_continuation(self.owner.root,runtime_python=sys.executable,**args)
            self.assertEqual(self.owner.session.path.read_bytes(),self.before)
        self.prepared.write_bytes(self.originals[self.prepared])
        (self.prepared.parent/'events.jsonl').write_text('')
        with self.assertRaises(StateError):self.prepare()
        self.assertEqual(self.owner.session.path.read_bytes(),self.before)

    def test_changed_scope_or_foreign_source_refuses(self):
        raw=self.ticket.read_bytes();source=json.loads(raw);source['step_request']['active_request']['held_candidate_ids']=[]
        changed=canonical_bytes(source);path=self.ticket.parent/(sha(changed)+'.json');path.write_bytes(changed)
        marker=path.with_suffix('.attempt.json');marker.write_bytes(self.marker.read_bytes())
        control_path=self.owner.project/'output/run-control.json';control=json.loads(control_path.read_bytes())
        control['attempt']=str(marker);control_path.write_bytes(canonical_bytes(control))
        with self.assertRaises(StateError):prepare_continuation(self.owner.root,runtime_python=sys.executable,
            **{**self.args,'attempt_path':str(marker)})
        self.assertEqual(self.owner.session.path.read_bytes(),self.before)

    def test_both_receipts_require_launch_free_exact_shape_and_route(self):
        for target in (self.prepared,self.wrapper):
            for field,value in [('pid',123),('message_id','paid-message'),('transport_turns',[1]),
                    ('process_reaped',True),('turns',[{'message_id':'paid-message'}]),('prompt_requests',1),
                    ('provider','foreign'),('model','foreign')]:
                with self.subTest(receipt=target.name,field=field):
                    body=json.loads(self.originals[target]);body[field]=value;target.write_bytes(canonical_bytes(body))
                    args={**self.args,('prepared_sha256' if target==self.prepared else 'wrapper_sha256'):sha(target.read_bytes())}
                    with self.assertRaises(StateError):prepare_continuation(self.owner.root,runtime_python=sys.executable,**args)
                    self.assertEqual(self.owner.session.path.read_bytes(),self.before)
                    target.write_bytes(self.originals[target])
            for name in ('events.jsonl','outputs','stderr.log','turns'):
                path=target.parent/name
                path.mkdir() if name in {'outputs','turns'} else path.write_text('startup-ready')
                with self.assertRaises(StateError):self.prepare()
                self.assertEqual(self.owner.session.path.read_bytes(),self.before)
                path.rmdir() if name in {'outputs','turns'} else path.unlink()

    def test_changed_snapshot_is_rejected_before_ownership_release_and_in_preflight(self):
        request=json.loads(self.ticket.read_bytes())['step_request'];path=Path(request['snapshot_path']);raw=path.read_bytes()
        path.chmod(0o644);path.write_bytes(b'{}')
        with self.assertRaises(StateError):self.prepare()
        self.assertEqual(self.owner.session.path.read_bytes(),self.before)
        path.write_bytes(raw);ticket=self.prepare();before=self.owner.session.path.read_bytes();path.write_bytes(b'{}')
        with self.assertRaises(StateError):self.f.f.preflight(ticket)
        self.assertEqual(self.owner.session.path.read_bytes(),before)

    def test_missing_reconciliation_membership_refuses_idempotent_preparation(self):
        self.prepare();state=json.loads(self.owner.session.path.read_bytes());state.pop('prelaunch_reconciliations')
        self.owner.session.state=state;self.owner.session.save();before=self.owner.session.path.read_bytes()
        with self.assertRaises(StateError):self.prepare()
        self.assertEqual(self.owner.session.path.read_bytes(),before)

    def test_consumed_ticket_never_authorizes_automatic_repeat(self):
        ticket=self.prepare();ticket.with_suffix('.attempt.json').write_text('{}')
        with self.assertRaises(StateError):self.prepare()


class OfflineSandboxStartupTests(unittest.TestCase):
    def test_actual_batch_setup_initializes_without_a_model_prompt_or_session(self):
        from scisaurus.runtime.dsh_batch import DshBatchRunner
        from scisaurus.tests.test_development_session import DevelopmentSessionTests
        fixture=DevelopmentSessionTests('test_history_workspace_and_usage_continue_across_producer_boundaries');fixture.setUp()
        try:
            runner=DshBatchRunner(fixture.config,root=fixture.project/'startup-only');runner.task_contract={}
            result=runner.run('offline startup',inputs={'assignment.json':b'{}'},outputs=['response.json'],startup_only=True)
            receipt=json.loads(Path(result['receipt']).read_bytes())
            self.assertEqual(receipt['status'],'startup_verified');self.assertFalse(receipt['network_enabled'])
            self.assertEqual(receipt['prompt_requests'],0);self.assertEqual(receipt['usage']['model_calls'],0)
            self.assertTrue(receipt['process_reaped']);self.assertEqual(result['files'],{})
            self.assertFalse((Path(result['receipt']).parent/'outputs').exists())
        finally:fixture.tearDown()
