"""An unpublished response cannot erase paid work or fabricate a document."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from scisaurus.core.errors import StateError,ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import IntakePrimaryClient,sha
from scisaurus.runtime.structured_recovery import draft_evidence,prepare_structured_recovery
from scisaurus.runtime.structured_response import publication_evidence,preserved_document
import scisaurus.tests.test_structured_recovery as recovery_tests
from scisaurus.tests.test_invention_review import events_for


class UnpublishedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=recovery_tests.StructuredRecoveryTests('test_accounting_retains_unknown_usage_backend_and_exact_draft')
        self.f.setUp();self.owner=self.f.owner;self.ticket=self.f.prepare();t=json.loads(self.ticket.read_bytes())
        self.request=t['step_request'];state=json.loads(self.owner.session.path.read_bytes())
        assignment=deepcopy(self.f.preflight(self.ticket)[3]);assignment.update(phase='concept_invention',
            concept_invention=self.request['active_request'],structured_continuation=self.request,
            output_contract=self.request['active_request']['output_contract'])
        self.f.work.unlink()
        stub=object.__new__(IntakePrimaryClient);inputs=stub.input_files(assignment,'single explicit continuation')
        self.job=self.owner.project/'intake-jobs/read-aborted';(self.job/'input').mkdir(parents=True)
        for name,raw in inputs.items():(self.job/'input'/name).write_bytes(raw)
        receipt=deepcopy(json.loads(self.f.receipt.read_bytes()));receipt.update(job_directory=str(self.job),
            input_sha256={name:sha(raw) for name,raw in inputs.items()},
            development_session={'binding':self.owner.binding,'prior_receipts':state['calls']})
        self.receipt=self.job/'receipt.json';self.receipt.write_bytes(canonical_bytes(receipt));events_for(self.job,receipt)
        failure={'name':'read','callId':'missing-response','arguments':json.dumps({'file_path':str(self.f.work)})}
        rows=[{'params':{'sessionId':state['session_id'],'event':{'type':kind,'data':{'turn':1,'step':5,**data}}}}
              for kind,data in [('tool/call',failure),('tool/result',{'message':{'content':[{
                  'toolCallId':'missing-response','isError':True,'content':[{'type':'text','text':'not found'}]}]}})]]
        self.events=self.job/'events.jsonl'
        with self.events.open('a') as stream:stream.write('\n'+'\n'.join(json.dumps(r) for r in rows))
        self.marker=self.ticket.with_suffix('.attempt.json');record=deepcopy(json.loads(self.f.marker.read_bytes()))
        record.update(receipt=str(self.receipt),backend_binding=t['backend_binding'],error='native read failed')
        raw=canonical_bytes(record);self.marker.write_bytes(raw)
        (self.owner.project/'intake-controller'/(sha(raw)+'.json')).write_bytes(raw)
        cp=self.owner.project/'output/run-control.json';control=json.loads(cp.read_bytes());control['attempt']=str(self.marker)
        cp.write_bytes(canonical_bytes(control));state['active_job']=str(self.receipt)
        self.owner.session.state=state;self.owner.session.save();self.before=self.owner.session.path.read_bytes()
        self.original=self.receipt.read_bytes();self.identity=sha(self.original)

    def tearDown(self):self.f.tearDown()
    def prepare(self):return prepare_structured_recovery(self.owner.root,receipt_sha256=self.identity,attempt_path=self.marker)

    def test_read_abort_recovers_only_exact_preserved_input_and_accounts_all_reported_usage(self):
        metadata,body=publication_evidence(self.receipt)
        self.assertIsNone(body);self.assertEqual(metadata['publication_state'],'absent')
        with self.assertRaises(StateError):preserved_document(self.receipt)
        ticket=self.prepare();value=json.loads(ticket.read_bytes());state=json.loads(self.owner.session.path.read_bytes())
        snapshot=json.loads(Path(value['step_request']['snapshot_path']).read_bytes())
        self.assertEqual(snapshot['provenance']['draft_origin'],'preserved_input')
        self.assertEqual(value['step_request']['draft_sha256'],self.request['draft_sha256'])
        self.assertEqual(value['step_request']['draft_concept_ids'],self.request['draft_concept_ids'])
        self.assertEqual(value['step_request']['active_request'],self.request['active_request'])
        self.assertIn('extra',value['step_request']['prior_contract_error'])
        self.assertTrue(value['step_request']['prior_input_read_coverage']['complete'])
        self.assertEqual(state['calls'][:-1],json.loads(self.before)['calls'])
        self.assertEqual(state['calls'][-1]['usage'],json.loads(self.original)['usage'])
        self.assertIsNone(state['active_job']);self.assertFalse(self.f.work.exists())
        self.assertEqual(self.receipt.read_bytes(),self.original);self.f.preflight(ticket)
        self.assertEqual(self.prepare(),ticket)
        ticket.with_suffix('.attempt.json').write_text('{}')
        with self.assertRaises(StateError):self.prepare()

    def test_changed_input_or_snapshot_refuses_before_accounting(self):
        for path in (self.job/'input/draft-response.json',Path(self.request['snapshot_path'])):
            raw=path.read_bytes();path.chmod(0o644);path.write_bytes(b'{}')
            with self.assertRaises((StateError,ValidationError)):self.prepare()
            self.assertEqual(self.owner.session.path.read_bytes(),self.before);path.write_bytes(raw)

    def test_later_model_step_or_tool_work_or_existing_response_refuses(self):
        original=self.events.read_bytes();rows=[json.loads(x) for x in original.decode().splitlines()]
        for kind,data in [('step/start',{}),('tool/call',{'name':'write','callId':'late','arguments':'{}'})]:
            extra={'params':{'sessionId':json.loads(self.original)['session_id'],
                'event':{'type':kind,'data':{'turn':1,'step':6,**data}}}}
            self.events.write_text(original.decode()+'\n'+json.dumps(extra))
            with self.assertRaises(StateError):self.prepare()
            self.assertEqual(self.owner.session.path.read_bytes(),self.before)
        self.events.write_bytes(original);self.f.work.write_text('{}')
        with self.assertRaises(StateError):self.prepare()
        self.assertEqual(self.owner.session.path.read_bytes(),self.before)

    def test_all_input_and_task_policy_identify_absent_output_without_read_before_create(self):
        a=json.loads((self.job/'input/assignment.json').read_bytes());p=a['structured_output_policy']
        self.assertEqual(p['initial_response_state'],'absent');self.assertEqual(p['preserved_document_input'],'draft-response.json')
        stub=object.__new__(IntakePrimaryClient);task=stub.task({'structured_continuation':self.request})
        self.assertIn('Do not read it before your first successful',task)
        self.assertNotIn('read the existing output if needed',task)
        ticket=self.prepare();r=json.loads(ticket.read_bytes())['step_request']
        self.assertTrue(any('absent at dispatch start' in rule for rule in r['rules']))
