"""Lean primary work preserves receipts and treats notes as unadmitted evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scisaurus.core.errors import StateError,ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import IntakePrimaryClient,run_once,sha
from scisaurus.runtime.inline_inputs import inline_inputs
from scisaurus.runtime.intake_contract import validate_response
from scisaurus.runtime.input_delivery import verify_full_input_reads,input_read_coverage
from scisaurus.runtime.primary_work import prepare_work_step,work_assignment,tool_exchange
import scisaurus.tests.test_intake_contract as contract_tests


class PrimaryWorkTests(unittest.TestCase):
    def setUp(self):
        self.f=contract_tests.IntakeContractTests('test_guarded_recovery_preserves_history_draft_route_and_is_idempotent')
        self.f.setUp();self.owner=self.f.owner;self.before=self.owner.session.path.read_bytes()
        self.originals={p:p.read_bytes() for p in self.owner.project.rglob('*') if p.is_file() and p.name not in {'control.sqlite','control.sqlite-wal','control.sqlite-shm','session.json','run-control.json','owner.lock'}}
    def tearDown(self):self.f.tearDown()
    def prepare(self):return prepare_work_step(self.owner.root,receipt_sha256=self.f.identity,
        attempt_path=self.f.marker,runtime_python=sys.executable)
    def preflight(self,ticket):return self.f.f.preflight(ticket)

    def test_work_packet_has_no_stage_schema_or_other_author_and_preserves_history(self):
        ticket=self.prepare();t=json.loads(ticket.read_bytes());state=json.loads(self.owner.session.path.read_bytes())
        self.assertEqual(state['calls'],json.loads(self.before)['calls']);self.assertEqual(state['session_id'],json.loads(self.before)['session_id'])
        self.assertTrue(all(p.read_bytes()==raw for p,raw in self.originals.items()))
        assignment=work_assignment(self.preflight(ticket)[3],t['step_request'])
        self.assertNotIn('output_contract',assignment);self.assertNotIn('phase',assignment)
        self.assertNotIn('concept_invention',assignment);self.assertIn('run',assignment['scientific_software_tools']['actions'])
        self.assertEqual(t['step_request']['checkpoint']['response'],self.f.response)
        validate_response(assignment,{'concept_design':{'arbitrary_new_mechanism':'preserved research content'}})
        with self.assertRaises(ValidationError):validate_response(assignment,{'tool_action':{'operation':'undeclared','arguments':{}}})
        self.assertEqual(self.prepare(),ticket)
        ticket.with_suffix('.attempt.json').write_text('{}')
        with self.assertRaises(StateError):self.prepare()

    def test_same_owner_tool_feedback_and_failure_stop_have_no_model_fallback(self):
        t=json.loads(self.prepare().read_bytes());assignment=work_assignment(self.preflight(self.prepare())[3],t['step_request'])
        bench=Mock();bench.execute.return_value={'outcome':'ok','receipt_ref':'real-observation','raw':[1,2]};record={}
        exchange=tool_exchange(assignment,bench,record);action={'tool_action':{'operation':'check_environment','arguments':{}}}
        result=exchange({'response.json':canonical_bytes(action)},{'model_calls':1})
        self.assertEqual(json.loads(result['inputs']['tool-result.json']),bench.execute.return_value)
        self.assertEqual(record['observations'][0],bench.execute.return_value)
        self.assertIsNone(exchange({'response.json':b'{"note":"new work"}'},{'model_calls':1}))
        self.assertEqual(bench.execute.call_count,1)
        bench.execute.return_value={'outcome':'result_unknown','usage':{'model_calls':2}}
        with self.assertRaises(StateError):exchange({'response.json':canonical_bytes(action)},{'model_calls':1})
        self.assertEqual(record['observations'][-1],bench.execute.return_value)

    def mocked_note(self,ticket,delivery=True):
        args=self.preflight(ticket);root,cp,backend,assignment,protected=args;t=json.loads(ticket.read_bytes())
        assignment=work_assignment(assignment,t['step_request']);stub=object.__new__(IntakePrimaryClient);inputs=stub.input_files(assignment,'primary work')
        job=self.owner.project/'intake-jobs/lean-completed';(job/'input').mkdir(parents=True);(job/'outputs').mkdir()
        for name,raw in inputs.items():(job/'input'/name).write_bytes(raw)
        response={'note':'An unfinished mechanism with arbitrary prose; no scientific success claimed.'};(job/'outputs/response.json').write_bytes(canonical_bytes(response))
        state=json.loads(self.owner.session.path.read_bytes());body=deepcopy(json.loads(self.f.receipt.read_bytes()))
        body.update(job_directory=str(job),config_sha256=state['config_sha256'],message_id='owned-message',
            input_sha256={n:sha(v) for n,v in inputs.items()},outputs={'response.json':sha((job/'outputs/response.json').read_bytes())},
            development_session={'binding':self.owner.binding,'prior_receipts':state['calls']})
        manifest=job/'manifest.json';manifest.write_bytes(canonical_bytes({'input_delivery':'inline','mode':'research_work','inputs':{str(job/'input'/name):sha(raw) for name,raw in inputs.items()}}))
        body['task_contract']={'path':str(manifest),'sha256':sha(manifest.read_bytes())};rp=job/'receipt.json';rp.write_bytes(canonical_bytes(body))
        event={'params':{'sessionId':state['session_id'],'event':{'type':'user/message','data':{
            'id':'owned-message','content':[{'type':'text','text':inline_inputs(inputs) if delivery else 'partial'}]}}}}
        (job/'events.jsonl').write_text(json.dumps(event))
        result=SimpleNamespace(response_metadata={'receipt':str(rp)},usage=body['usage'],json_object=lambda:response)
        with patch('scisaurus.runtime.fresh_intake.preflight',return_value=args),patch('scisaurus.runtime.fresh_intake.IntakePrimaryClient') as client,patch('scisaurus.runtime.fresh_intake.LaboratoryBinding.load',return_value=SimpleNamespace(attestation={})),patch('scisaurus.runtime.fresh_intake.load_attestation',return_value={}):
            client.return_value.runner.task_contract={};client.return_value.complete.return_value=result
            try:return run_once(root,sys.executable,continuation=ticket,recovery={'source':'offline fixture','observed_at':'2026-01-01T00:00:00Z'})
            finally:self.assertEqual(client.return_value.complete.call_count,1)

    def test_notes_checkpoint_without_read_rounds_or_mandatory_science_format_review(self):
        ticket=self.prepare();record=self.mocked_note(ticket)
        self.assertEqual(record['status'],'stopped_with_research_progress');self.assertEqual(record['scientific_admission'],'not_assessed')
        self.assertEqual(record['input_read_coverage']['delivery'],'inline');self.assertNotIn('publication_check',record)
        self.assertTrue(json.loads((self.owner.project/'output/run-control.json').read_bytes())['stop_requested'])
        self.assertEqual(json.loads(self.owner.session.path.read_bytes())['calls'],json.loads(self.before)['calls'])

    def test_missing_inline_delivery_stops_without_second_call(self):
        ticket=self.prepare()
        with self.assertRaises(StateError):self.mocked_note(ticket,delivery=False)
        self.assertEqual(json.loads(ticket.with_suffix('.attempt.json').read_bytes())['status'],'stopped_on_error')

    def test_tampered_checkpoint_refuses_and_legacy_route_cannot_replace_work(self):
        ticket=self.prepare();t=json.loads(ticket.read_bytes());t['step_request']['checkpoint']['response']={'invented':'yes'}
        raw=canonical_bytes(t);path=ticket.parent/(sha(raw)+'.json');path.write_bytes(raw)
        with self.assertRaises(StateError):self.preflight(path)
        state=self.owner.session.path.read_bytes()
        with self.assertRaises(StateError):self.f.prepare()
        self.assertEqual(self.owner.session.path.read_bytes(),state)

    def test_scientific_inputs_and_time_policy_survive_packet_reduction(self):
        original={'objective':'question','frozen_scientific_input':{'fraction':.25},
            'time_policy':{'overall':'unbounded','support_seconds':18000,'allocation_target':.95},
            'phase':'obsolete','output_contract':{'many':'fields'}}
        value=work_assignment(original,{'kind':'research_work'})
        self.assertEqual(value['frozen_scientific_input'],original['frozen_scientific_input'])
        self.assertEqual(value['time_policy'],original['time_policy'])
        self.assertNotIn('phase',value);self.assertNotIn('output_contract',value)
        self.assertIn('phase',original)


class UnknownWorkTests(unittest.TestCase):
    def test_unknown_charges_remain_unknown_and_are_reconciled_once(self):
        import scisaurus.tests.test_structured_recovery as recovery_tests
        fixture=recovery_tests.StructuredRecoveryTests('test_accounting_retains_unknown_usage_backend_and_exact_draft')
        fixture.setUp()
        try:
            before=fixture.receipt.read_bytes();old=deepcopy(fixture.before)
            ticket=prepare_work_step(fixture.owner.root,receipt_sha256=fixture.identity,
                attempt_path=fixture.marker,runtime_python=sys.executable)
            state=json.loads(fixture.owner.session.path.read_bytes());t=json.loads(ticket.read_bytes())
            self.assertEqual(fixture.receipt.read_bytes(),before);self.assertIsNone(state['active_job'])
            self.assertEqual(state['calls'][:-1],old['calls']);self.assertEqual(state['calls'][-1]['usage'],fixture.owner.usage)
            self.assertEqual(t['step_request']['checkpoint']['prior_outcome'],'result_unknown')
            self.assertEqual(t['reconciliation_ref'],state['calls'][-1]['reconciliation_ref'])
            fixture.f.preflight(ticket)
            self.assertEqual(prepare_work_step(fixture.owner.root,receipt_sha256=fixture.identity,
                attempt_path=fixture.marker,runtime_python=sys.executable),ticket)
        finally:fixture.tearDown()

class InlineTransportTests(unittest.TestCase):
    def test_real_local_transport_exchanges_feedback_and_verifies_every_owned_input_message(self):
        import scisaurus.tests.test_development_session as session_tests
        from scisaurus.runtime.dsh_batch import DshBatchRunner,sha256
        fixture=session_tests.DevelopmentSessionTests('test_history_workspace_and_usage_continue_across_producer_boundaries');fixture.setUp()
        try:
            script=Path(fixture.config['command'][1]);text=script.read_text()
            text=text.replace("Path('answer.txt').write_text(str(n))", "Path('response.json').write_text(json.dumps({'note':str(n)}))")
            text=text.replace("event('agent/inbox/spliced',", "event('user/message',{'id':mid,'role':'user','content':p['contentBlocks']})\n  event('agent/inbox/spliced',")
            script.write_text(text);fixture.config['pinned_files'][str(script)]=sha256(script)
            runner=DshBatchRunner(fixture.config,root=fixture.project/'work',development_session=fixture.binding)
            runner.task_contract={'mode':'research_work','input_delivery':'inline'};rounds=[]
            def exchange(files,usage):
                rounds.append(json.loads(files['response.json']))
                return None if len(rounds)==3 else {'task':'Continue from observed feedback','inputs':{'tool-result.json':b'{"outcome":"ok"}'}}
            result=runner.run('primary work',inputs={'assignment.json':b'{"objective":"retain"}'},outputs=['response.json'],exchange=exchange)
            rp=Path(result['receipt']);receipt=json.loads(rp.read_bytes());coverage=verify_full_input_reads(rp)
            self.assertEqual(len(receipt['turns']),3);self.assertEqual(coverage['delivery'],'inline')
            self.assertEqual(result['usage']['model_calls'],3);self.assertTrue(receipt['process_reaped'])
            self.assertIn('2/tool-result.json',coverage['files'])
            events=rp.parent/'events.jsonl';raw=events.read_text();rows=[json.loads(l) for l in raw.splitlines()]
            for row in rows:
                event=row.get('params',{}).get('event',{})
                if event.get('type')=='user/message' and event['data']['id']==receipt['turns'][1]['message_id']:
                    event['data']['content']=[{'type':'text','text':'missing feedback'}]
            events.write_text('\n'.join(json.dumps(row) for row in rows))
            self.assertFalse(input_read_coverage(rp)['complete'])
        finally:fixture.tearDown()

class NativeWorkPolicyTests(unittest.TestCase):
    def test_inline_free_notes_and_optional_review_use_installed_native_schema(self):
        import shutil,subprocess,tempfile
        node=shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node'
        plugin=(Path(__file__).resolve().parents[2]/'scripts/dsh/intake-contract.mjs').as_uri()
        code=r'''
import {mkdtempSync,writeFileSync,rmSync,realpathSync} from 'node:fs';import {tmpdir} from 'node:os';
import {join} from 'node:path';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
const {createPolicy,apply}=await import(PLUGIN);const dir=realpathSync(mkdtempSync(join(tmpdir(),'primary-work-')));
try{const path=join(dir,'assignment.json');const raw=JSON.stringify({research_work:{kind:'research_work'}});
writeFileSync(path,raw);const manifest={workspace:dir,output:join(dir,'response.json'),assignment:path,
 inputs:{[path]:createHash('sha256').update(raw).digest('hex')},input_delivery:'inline',mode:'research_work'};
const policy=createPolicy(manifest,JSON.parse);const agent={};
assert.equal(policy.pending(agent).length,0);
assert.equal(policy.guard({name:'write',agent,arguments:{file_path:manifest.output,content:'{"new_mechanism":"free notes"}'}}),undefined);
assert.equal(policy.guard({name:'review_response',agent,arguments:{}}),undefined);
assert.match(policy.guard({name:'bash',agent,arguments:{command:'other author'}}),/permits reading/);
const mp=join(dir,'manifest.json');writeFileSync(mp,JSON.stringify(manifest));process.env.SCISAURUS_TASK_CONTRACT=mp;
let registered;apply({tools:{guard(){},register(value){registered=value;}},on(){}},{});
assert.equal(registered.name,'review_response');assert.match(registered.description,/independent scientific review/);
}finally{rmSync(dir,{recursive:true});}
'''.replace('PLUGIN',json.dumps(plugin))
        result=subprocess.run([node,'--input-type=module','-e',code],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
