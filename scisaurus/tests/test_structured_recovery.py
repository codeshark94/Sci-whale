"""Paid tool aborts continue only from an exact immutable draft under structural publication."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.dsh_batch import DshBatchError
from scisaurus.runtime.fresh_intake import IntakePrimaryClient, run_once, sha
from scisaurus.runtime.structured_recovery import prepare_structured_recovery, validate_continuation
from scisaurus.runtime.structured_response import preserved_document
import scisaurus.tests.test_concept_invention as concept_tests
import scisaurus.tests.test_invention_review as review_tests


class StructuredRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=concept_tests.ConceptInventionTests('test_all_candidates_held_without_history_or_backend_changes')
        self.f.setUp();self.owner=self.f.owner
        self.source_ticket=self.f.prepare();self.active=json.loads(self.source_ticket.read_bytes())['step_request']
        args=self.f.preflight(self.source_ticket);root,checkpoint,backend,assignment,protected=args
        assignment['scientific_software_tools']={'actions':{},'response':{'tool_action':{}},'rules':[]}
        binding=Path(json.loads(self.f.marker.read_bytes())['backend_binding'])
        binding.write_bytes(canonical_bytes({'backend':backend,'backend_sha256':sha(canonical_bytes(backend)),
            'development_session':self.owner.binding,'session_id':'original'}))
        self.clean=self.f.value(self.active)
        second=deepcopy(self.clean['concepts'][0]);second['id']='second-concept';self.clean['concepts'].append(second)
        draft=deepcopy(self.clean)
        for concept in draft['concepts']:concept['mechanism']='same repeated explanation'
        self.document=json.dumps({'concept_design':draft},ensure_ascii=False,indent=2)
        self.work=Path(self.owner.binding['root'])/'work/response.json';self.work.parent.mkdir(exist_ok=True)
        self.work.write_text(self.document)
        self.receipt=self.owner.project/'intake-jobs/aborted/receipt.json'
        def fail(**kwargs):
            projected=json.loads(kwargs['prompt']);stub=object.__new__(IntakePrimaryClient)
            inputs=stub.input_files(projected,kwargs['system']);job=self.receipt.parent
            (job/'input').mkdir(parents=True)
            for name,raw in inputs.items():(job/'input'/name).write_bytes(raw)
            state=json.loads(self.owner.session.path.read_bytes())
            body=deepcopy(self.owner.body)
            body.update(status='result_unknown',finish_reason='aborted',outputs={},transport_turns=[1],
                job_directory=str(job),development_session={'binding':self.owner.binding,'prior_receipts':state['calls']},
                input_sha256={name:sha(raw) for name,raw in inputs.items()})
            self.receipt.write_bytes(canonical_bytes(body))
            review_tests.events_for(job,body)
            es=[]
            for name,identity,arguments,error in [
                ('write','written',{'file_path':str(self.work),'content':self.document},False),
                ('edit','ambiguous',{'file_path':str(self.work),'old_string':'"mechanism": "same repeated explanation"','new_string':''},True)]:
                for kind,data in [('tool/call',{'name':name,'callId':identity,'arguments':json.dumps(arguments)}),
                                  ('tool/result',{'message':{'content':[{'toolCallId':identity,'isError':error,
                                    'content':[{'text':'old_string matched 2 times' if error else 'created'}]}]}})]:
                    es.append({'params':{'sessionId':body['session_id'],'event':{'type':kind,
                        'data':{'turn':1,'step':2 if name=='write' else 3,**data}}}})
            with (job/'events.jsonl').open('a') as stream:
                stream.write('\n'+'\n'.join(json.dumps(e) for e in es))
            state['active_job']=str(self.receipt);self.owner.session.state=state;self.owner.session.save()
            raise DshBatchError('owned native tool abort',receipt=self.receipt,usage=self.owner.usage)
        with patch('scisaurus.runtime.intake_guard.verify_publication',return_value={'offline_transport_fixture':True}), \
             patch('scisaurus.runtime.fresh_intake.preflight',return_value=args),\
             patch('scisaurus.runtime.fresh_intake.IntakePrimaryClient') as client:
            client.return_value.complete.side_effect=fail
            with self.assertRaises(DshBatchError):run_once(root,sys.executable,continuation=self.source_ticket,
                recovery={'source':'offline fixture','observed_at':'2026-01-01T00:00:00Z'})
            self.assertEqual(client.return_value.complete.call_count,1)
        self.marker=self.source_ticket.with_suffix('.attempt.json');self.identity=sha(self.receipt.read_bytes())
        self.before=json.loads(self.owner.session.path.read_bytes());self.receipt_before=self.receipt.read_bytes()

    def tearDown(self):self.f.tearDown()
    def prepare(self):return prepare_structured_recovery(self.owner.root,receipt_sha256=self.identity,attempt_path=self.marker)
    def preflight(self,ticket):
        with patch('scisaurus.runtime.intake_guard.guarded_backend',return_value={}):
            return self.f.preflight(ticket)

    def test_accounting_retains_unknown_usage_backend_and_exact_draft(self):
        metadata,body=preserved_document(self.receipt)
        self.assertEqual(body,self.document.encode());self.assertEqual(metadata['failure']['tool'],'edit')
        ticket=self.prepare();value=json.loads(ticket.read_bytes());state=json.loads(self.owner.session.path.read_bytes())
        self.assertEqual(value['prior_receipt_status'],'result_unknown')
        self.assertEqual(state['session_id'],self.before['session_id']);self.assertIsNone(state['active_job'])
        self.assertEqual(state['calls'][:-1],self.before['calls']);self.assertEqual(state['calls'][-1]['usage'],self.owner.usage)
        self.assertIn('reconciliation_ref',state['calls'][-1]);self.assertEqual(self.receipt.read_bytes(),self.receipt_before)
        self.assertEqual(state['config_sha256'],self.before['config_sha256'])
        self.assertEqual(state.get('backend_revisions'),self.before.get('backend_revisions'))
        self.preflight(ticket);self.assertEqual(self.prepare(),ticket)
        self.assertEqual(self.work.read_text(),self.document)

    def test_workspace_drift_or_foreign_receipt_refuses_accounting(self):
        self.work.write_text('changed')
        with self.assertRaises(StateError):self.prepare()
        self.assertEqual(json.loads(self.owner.session.path.read_bytes()),self.before)
        self.work.write_text(self.document)
        with self.assertRaises(StateError):prepare_structured_recovery(self.owner.root,receipt_sha256='0'*64,attempt_path=self.marker)
        self.assertEqual(json.loads(self.owner.session.path.read_bytes()),self.before)

    def test_scope_or_snapshot_tamper_refuses_preflight(self):
        ticket=self.prepare()
        for key in ['draft_capability_ids','active_request','snapshot_sha256']:
            value=json.loads(ticket.read_bytes())
            if key=='active_request':value['step_request'][key]['held_candidate_ids']=[]
            else:value['step_request'][key]=[] if key=='draft_capability_ids' else '0'*64
            raw=canonical_bytes(value);path=ticket.parent/(sha(raw)+'.json');path.write_bytes(raw)
            with self.assertRaises(StateError):self.preflight(path)

    def test_relative_native_paths_have_the_same_owned_provenance(self):
        path=self.receipt.parent/'events.jsonl';rows=[json.loads(x) for x in path.read_text().splitlines()]
        for row in rows:
            data=row.get('params',{}).get('event',{}).get('data',{})
            if data.get('name') in {'write','edit'}:
                args=json.loads(data['arguments']);args['file_path']='response.json';data['arguments']=json.dumps(args)
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        self.assertEqual(preserved_document(self.receipt)[1],self.document.encode())

    def test_late_success_after_terminal_error_refuses_accounting(self):
        path=self.receipt.parent/'events.jsonl';rows=[json.loads(x) for x in path.read_text().splitlines()]
        params={'sessionId':json.loads(self.receipt.read_bytes())['session_id']}
        pending={'params':{**params,'event':{'type':'tool/call','data':{'turn':1,'step':2,
            'name':'write','callId':'late','arguments':json.dumps({'file_path':str(self.work),'content':self.document})}}}}
        rows.insert(-2,pending)
        rows.append({'params':{**params,'event':{'type':'tool/result','data':{'turn':1,'step':3,
            'message':{'content':[{'toolCallId':'late','isError':False,'content':[{'text':'created'}]}]}}}}})
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        with self.assertRaisesRegex(StateError,'results after its terminal error'):self.prepare()
        self.assertEqual(json.loads(self.owner.session.path.read_bytes()),self.before)

    def test_continuation_keeps_identities_and_allows_explicit_abstention(self):
        request=json.loads(self.prepare().read_bytes())['step_request']
        validate_continuation({'concept_design':self.clean},request)
        changed=deepcopy(self.clean);changed['concepts'][0]['id']='new-topic'
        with self.assertRaises(ValidationError):validate_continuation({'concept_design':changed},request)
        abstain=deepcopy(self.clean);abstain.update(decision='abstain',concepts=[],limitations=['No defensible completion'])
        validate_continuation({'concept_design':abstain},request)

    def run_mocked(self,ticket,wrong=False):
        args=self.preflight(ticket);root,checkpoint,backend,assignment,protected=args
        assignment['scientific_software_tools']={'actions':{},'response':{'tool_action':{}},'rules':[]}
        request=json.loads(ticket.read_bytes())['step_request']
        projected=deepcopy(assignment);projected.update(concept_invention=request['active_request'],structured_continuation=request,
            phase='concept_invention',output_contract=request['active_request']['output_contract'])
        stub=object.__new__(IntakePrimaryClient);inputs=stub.input_files(projected,'Continue the preserved draft.')
        self.assertEqual(json.loads(inputs['assignment.json'])['structured_output_policy']['publication'],'complete_document_write')
        task=stub.task(projected);self.assertIn('Never use substring edits',task);self.assertNotIn('boldly imagine',task)
        job=self.owner.project/'intake-jobs/resumed';(job/'input').mkdir(parents=True)
        for name,raw in inputs.items():(job/'input'/name).write_bytes(raw)
        body=json.loads(self.receipt.read_bytes());body.update(status='completed',job_directory=str(job),
            input_sha256={name:sha(raw) for name,raw in inputs.items()})
        receipt=job/'receipt.json';receipt.write_bytes(canonical_bytes(body));review_tests.events_for(job,body)
        response={'tool_action':{}} if wrong else {'concept_design':self.clean}
        result=SimpleNamespace(response_metadata={'receipt':str(receipt)},usage=self.owner.usage,json_object=lambda:response)
        with patch('scisaurus.runtime.intake_guard.verify_publication',return_value={'offline_transport_fixture':True}), \
             patch('scisaurus.runtime.fresh_intake.preflight',return_value=args),\
             patch('scisaurus.runtime.fresh_intake.IntakePrimaryClient') as client,\
             patch('scisaurus.runtime.fresh_intake.SoftwareWorkbench',side_effect=AssertionError('no scientific tools')):
            client.return_value.complete.return_value=result
            try:return run_once(root,sys.executable,continuation=ticket,recovery={'source':'offline fixture','observed_at':'2026-01-01T00:00:00Z'})
            finally:self.assertEqual(client.return_value.complete.call_count,1)

    def test_one_same_primary_continuation_stops_and_no_repeat_or_backend_revision(self):
        ticket=self.prepare();record=self.run_mocked(ticket)
        self.assertEqual(record['status'],'stopped_with_unreviewed_concepts')
        self.assertTrue(record['input_read_coverage']['complete']);self.assertEqual(self.receipt.read_bytes(),self.receipt_before)
        self.assertTrue(json.loads((self.owner.project/'output/run-control.json').read_bytes())['stop_requested'])
        with self.assertRaises(StateError):self.prepare()

    def test_wrong_response_preserves_paid_usage_and_stops_without_repair(self):
        ticket=self.prepare()
        with self.assertRaises(ValidationError):self.run_mocked(ticket,wrong=True)
        record=json.loads(ticket.with_suffix('.attempt.json').read_bytes())
        self.assertEqual(record['status'],'stopped_on_error');self.assertEqual(record['usage'],self.owner.usage)
        self.assertEqual(self.receipt.read_bytes(),self.receipt_before)
        self.assertTrue(json.loads((self.owner.project/'output/run-control.json').read_bytes())['stop_requested'])
