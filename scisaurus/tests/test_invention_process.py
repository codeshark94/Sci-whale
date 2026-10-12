"""Function-first hypotheses preserve completed history and held source lineage."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import IntakePrimaryClient, sha
from scisaurus.runtime.intake_recovery import prepare_invention_step
from scisaurus.runtime.invention import invention_contract, validate_invention
from scisaurus.runtime.literature_collection import collect_literature
import scisaurus.tests.test_completed_literature_step as completed_tests
from scisaurus.tests.test_literature_collection import FakeLiterature


class InventionProcessTests(unittest.TestCase):
    def setUp(self):
        self.f=completed_tests.CompletedLiteratureStepTests('test_completed_proposal_is_lossless_and_session_history_unchanged')
        self.f.setUp(); f=self.f.f
        self.owner=f
        f.config['timeout_seconds']=30
        f.body['config_sha256']=sha(canonical_bytes(f.config))
        f.receipt.write_bytes(canonical_bytes(f.body))
        state=json.loads(f.session.path.read_bytes())
        state['config_sha256']=f.body['config_sha256']
        state['calls'][0]['sha256']=sha(f.receipt.read_bytes())
        f.session.state=state;f.session.save()
        Path(f.attempt_body['backend_binding']).write_bytes(canonical_bytes({'backend':f.config}))
        (f.root/'inputs/system-contract.txt').write_text('Preserve scientific evidence and report hypotheses honestly.')
        first=json.loads(f.session.path.read_bytes())
        self.ids=[c['id'] for c in self.f.proposal['topic_selection']['candidates']]
        action={'operation':'collect_literature','arguments':{'requests':[
            {'candidate_id':identity,'query':'literature '+identity,'limit':5} for identity in self.ids]}}
        self.marker=f.project/'intake-controller/current.attempt.json'
        obs=collect_literature(action,candidate_ids=self.ids,root=f.project/'intake-controller/current.literature',
            deadline=time.monotonic()+2,client=FakeLiterature())
        job=f.project/'intake-jobs/observation';(job/'input').mkdir(parents=True);(job/'outputs').mkdir()
        (job/'input/assignment.json').write_text(json.dumps(json.loads((f.job/'input/assignment.json').read_bytes()),indent=2))
        (job/'input/system-contract.txt').write_bytes((f.root/'inputs/system-contract.txt').read_bytes())
        response={'tool_action':action};(job/'outputs/response.json').write_bytes(canonical_bytes(response))
        receipt=deepcopy(f.body);receipt.update(job_directory=str(job),
            development_session={'binding':f.binding,'prior_receipts':first['calls']},
            outputs={'response.json':sha((job/'outputs/response.json').read_bytes())})
        receipt['input_sha256']['system-contract.txt']=sha((job/'input/system-contract.txt').read_bytes())
        receipt['input_sha256']['assignment.json']=sha((job/'input/assignment.json').read_bytes())
        receipt['transport_turns']=[1]
        events=[]
        for name in receipt['input_sha256']:
            path=job/'input'/name
            displayed='\n'.join(f'{i}: {text}' for i,text in enumerate(path.read_text().splitlines(),1))
            for kind,data in [('tool/call',{'name':'read','callId':name,'arguments':json.dumps({'file_path':str(path)})}),
                              ('tool/result',{'message':{'content':[{'toolCallId':name,'isError':False,
                                  'content':[{'text':f'<path>{path}</path>\n{displayed}'}]}]}})]:
                events.append({'params':{'sessionId':receipt['session_id'],'event':{'type':kind,'data':{'turn':1,**data}}}})
        (job/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        self.receipt=job/'receipt.json';self.receipt.write_bytes(canonical_bytes(receipt));self.identity=sha(self.receipt.read_bytes())
        first['calls'].append({'receipt':str(self.receipt),'sha256':self.identity,'usage':receipt['usage']})
        f.session.state=first;f.session.save()
        marker=deepcopy(f.attempt_body);marker.update(status='stopped_after_observation',response=response,
            receipt=str(self.receipt),observation=obs)
        raw=canonical_bytes(marker);self.marker.write_bytes(raw)
        (f.project/'intake-controller'/(sha(raw)+'.json')).write_bytes(raw)
        control=f.project/'output/run-control.json';value=json.loads(control.read_bytes());value['attempt']=str(self.marker);control.write_bytes(canonical_bytes(value))
        self.before=f.session.path.read_bytes();self.proposal_before=self.f.output.read_bytes()

    def tearDown(self):self.f.tearDown()
    def prepare(self):return prepare_invention_step(self.owner.root,receipt_sha256=self.identity,attempt_path=self.marker)

    def test_same_completed_session_holds_all_candidates_and_preserves_full_sources(self):
        ticket=self.prepare();value=json.loads(ticket.read_bytes());request=value['step_request']
        self.assertEqual(request['kind'],'invention');self.assertEqual(request['held_candidate_ids'],self.ids)
        self.assertEqual(self.owner.session.path.read_bytes(),self.before)
        self.assertEqual(self.f.output.read_bytes(),self.proposal_before)
        rebuilt=[{k:''.join(v) for k,v in c.items()} for c in request['held_candidate_literal_chunks']]
        self.assertEqual(rebuilt,self.f.proposal['topic_selection']['candidates'])
        self.f.preflight(ticket)
        self.assertEqual(self.prepare(),ticket)
        ticket.with_suffix('.attempt.json').write_bytes(b'{}')
        with self.assertRaises(StateError):self.prepare()

    def test_source_capture_change_blocks_before_dispatch(self):
        ticket=self.prepare();value=json.loads(ticket.read_bytes())
        observation=json.loads(Path(value['step_request']['observation_path']).read_bytes())
        identity=observation['rows'][0]['result']['capture_sha256']
        (Path(value['step_request']['observation_path']).parent/'captures'/identity).write_bytes(b'changed')
        with self.assertRaises(StateError):self.f.preflight(ticket)
        self.assertEqual(self.owner.session.path.read_bytes(),self.before)

    def test_tampered_hold_or_process_contract_is_rejected(self):
        ticket=self.prepare();value=json.loads(ticket.read_bytes());value['step_request']['held_candidate_ids']=[]
        raw=canonical_bytes(value);changed=ticket.parent/(sha(raw)+'.json');changed.write_bytes(raw)
        with self.assertRaisesRegex(StateError,'held candidates'):self.f.preflight(changed)
        value=json.loads(ticket.read_bytes());value['step_request']['rules']=['Select by available solver']
        raw=canonical_bytes(value);changed=ticket.parent/(sha(raw)+'.json');changed.write_bytes(raw)
        with self.assertRaises(StateError):self.f.preflight(changed)

    def test_invention_response_requires_prediction_falsifier_and_unverified_status(self):
        request=json.loads(self.prepare().read_bytes())['step_request']
        value=deepcopy(invention_contract()['invention']);value['decision']='propose'
        d=value['directions'][0];d['id']='new-function';d['held_candidate_separation']={identity:'different function and causal mechanism' for identity in self.ids}
        validate_invention(value,request,[])
        altered=deepcopy(value);altered['directions'][0]['prediction']['falsifier']=''
        with self.assertRaises(ValidationError):validate_invention(altered,request,[])
        altered=deepcopy(value);altered['directions'][0]['novelty_status']='confirmed'
        with self.assertRaises(ValidationError):validate_invention(altered,request,[])
        altered=deepcopy(value);altered['directions'][0]['id']=self.ids[0]
        with self.assertRaises(ValidationError):validate_invention(altered,request,[])
        altered=deepcopy(value);altered['directions'][0]['causal_chain']=['intervention','observable effect']
        with self.assertRaises(ValidationError):validate_invention(altered,request,[])
        validate_invention({'decision':'abstain','directions':[],'limitations':['No defensible mechanism']},request,[])

    def run_mocked(self,ticket,response):
        from scisaurus.runtime.fresh_intake import run_once
        args=self.f.preflight(ticket)
        root,checkpoint,backend,assignment,protected=args
        assignment['scientific_software_tools']={'actions':{'fetch_source':{},'run':{}},
            'response':{'tool_action':{'operation':'fetch_source | run'}},'rules':[]}
        result=SimpleNamespace(response_metadata={'receipt':str(self.receipt)},usage=self.owner.usage,
                               json_object=lambda:response)
        with patch('scisaurus.runtime.fresh_intake.preflight',return_value=args), \
             patch('scisaurus.runtime.fresh_intake.IntakePrimaryClient') as client:
            client.return_value.complete.return_value=result
            try:
                record=run_once(root,sys.executable,recovery={'source':'offline fixture confirmation',
                    'observed_at':'2026-01-01T00:00:00Z'},continuation=ticket)
                self.assertEqual(client.return_value.complete.call_count,1)
                dispatched=client.return_value.complete.call_args.kwargs
                self.assertEqual(json.loads(dispatched['prompt'])['scientific_software_tools']['actions'],{})
                self.assertIn('Active phase: invention',dispatched['system'])
                return record
            finally:
                self.assertLessEqual(client.return_value.complete.call_count,1)

    def test_invention_branch_holds_candidates_stops_and_rejects_second_attempt(self):
        ticket=self.prepare()
        value=deepcopy(invention_contract()['invention']);value['decision']='propose'
        value['directions'][0]['held_candidate_separation']={identity:'distinct function and mechanism' for identity in self.ids}
        record=self.run_mocked(ticket,{'invention':value})
        self.assertEqual(record['status'],'stopped_with_unreviewed_invention')
        control=json.loads((self.owner.project/'output/run-control.json').read_bytes())
        self.assertTrue(control['stop_requested'])
        self.assertEqual(control['candidate_statuses'],{identity:'held' for identity in self.ids})
        with self.assertRaises(StateError):self.run_mocked(ticket,{'invention':value})

    def test_wrong_response_stops_without_repair_or_tool_execution(self):
        ticket=self.prepare()
        with self.assertRaises(ValidationError):self.run_mocked(ticket,{'topic_selection':{}})
        record=json.loads(ticket.with_suffix('.attempt.json').read_bytes())
        self.assertEqual(record['status'],'stopped_on_error')
        self.assertTrue(json.loads((self.owner.project/'output/run-control.json').read_bytes())['stop_requested'])

    def test_skipped_input_lines_stop_after_paid_response_without_retry(self):
        ticket=self.prepare()
        path=self.receipt.parent/'events.jsonl'
        events=[json.loads(x) for x in path.read_text().splitlines()]
        block=events[1]['params']['event']['data']['message']['content'][0]['content'][0]
        block['text']='\n'.join(line for line in block['text'].splitlines() if not line.startswith('2: '))
        path.write_text('\n'.join(json.dumps(e) for e in events))
        with self.assertRaisesRegex(StateError,'input delivery is incomplete'):
            self.run_mocked(ticket,{'invention':{'decision':'abstain','directions':[],'limitations':['Unknown']}})
        record=json.loads(ticket.with_suffix('.attempt.json').read_bytes())
        self.assertEqual(record['status'],'stopped_on_error')
        self.assertFalse(record['input_read_coverage']['complete'])
        self.assertEqual(self.owner.session.path.read_bytes(),self.before)
        self.assertEqual(self.f.output.read_bytes(),self.proposal_before)

    def test_unrelated_read_results_cannot_cover_owned_inputs(self):
        from scisaurus.runtime.input_delivery import input_read_coverage
        path=self.receipt.parent/'events.jsonl'
        original=path.read_text()
        for change in ['turn','session','call','text']:
            with self.subTest(change=change):
                events=[json.loads(x) for x in original.splitlines()]
                result=events[1]['params'];data=result['event']['data']
                block=data['message']['content'][0]
                if change=='turn':data['turn']=2
                elif change=='session':result['sessionId']='foreign'
                elif change=='call':block['toolCallId']='unobserved'
                else:block['content'][0]['text']=block['content'][0]['text'].replace('1: {','1: invented')
                path.write_text('\n'.join(json.dumps(e) for e in events))
                self.assertFalse(input_read_coverage(self.receipt)['complete'])

    def test_input_delivery_rechecks_archived_input_hashes(self):
        from scisaurus.runtime.input_delivery import input_read_coverage
        (self.receipt.parent/'input/assignment.json').write_text('{}')
        with self.assertRaisesRegex(StateError,'exact input'):input_read_coverage(self.receipt)

    def test_result_in_another_owned_turn_does_not_match_read_call(self):
        from scisaurus.runtime.input_delivery import input_read_coverage
        receipt=json.loads(self.receipt.read_bytes());receipt['transport_turns']=[1,2]
        self.receipt.write_bytes(canonical_bytes(receipt))
        path=self.receipt.parent/'events.jsonl'
        events=[json.loads(x) for x in path.read_text().splitlines()]
        for row in events:
            if row['params']['event']['type']=='tool/result':row['params']['event']['data']['turn']=2
        path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertFalse(input_read_coverage(self.receipt)['complete'])

    def test_primary_task_is_function_first_and_has_no_other_author_or_tool_operation(self):
        task=IntakePrimaryClient.task(None,{'invention_process':{}})
        # A real request is nonempty and selects the invention task.
        task=IntakePrimaryClient.task(None,{'invention_process':{'rules':['function first']}})
        self.assertIn('unmet function, distinct physical mechanisms, falsifiable predictions',task)
        self.assertIn('output_contract.invention',task)
        self.assertIn('Do not submit topic_selection or tool_action',task)
        self.assertIn('Novelty is unverified',task)
