"""Lean primary work preserves receipts and treats notes as unadmitted evidence."""
from copy import deepcopy
import json
import shutil
import subprocess
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scisaurus.tests.context_fixtures import context_backend
from scisaurus.core.errors import StateError,ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import IntakePrimaryClient,run_once,sha
from scisaurus.runtime.inline_inputs import inline_inputs
from scisaurus.runtime.intake_contract import validate_response
from scisaurus.runtime.input_delivery import verify_full_input_reads,input_read_coverage
from scisaurus.runtime.primary_work import prepare_work_step,work_assignment,native_tool_handler,validate_native_action
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

    def test_transport_custody_is_not_repeated_in_research_context(self):
        from scisaurus.runtime.primary_work import research_context_projection,project_inline_research_inputs
        dispatch={'status':'completed','receipt':'owned-review/receipt.json','usage':{'model_calls':6},
            'assignment':{'geometry':{'L':1,'t':.001},'raw':['NaN',0,3]},
            'feedback':{'notes':'The coefficients differ.'},'backend':{'pinned_files':{'path':'abc'},'model':'fixed'}}
        request={'kind':'research_work','feedback':'Correct the same-condition comparison.',
            'checkpoint':{'review_dispatches':[dispatch],'observations':[{'raw':[1,2]}]}}
        original={'objective':'mechanical fold','research_work':request}
        saved=deepcopy(original);projected=research_context_projection(original)
        self.assertEqual(original,saved)
        reviewed=projected['research_work']['checkpoint']['review_dispatches'][0]
        self.assertNotIn('backend',reviewed)
        self.assertEqual(reviewed['backend_sha256'],sha(canonical_bytes(dispatch['backend'])))
        for key in dispatch.keys()-{'backend'}:self.assertEqual(reviewed[key],dispatch[key])
        self.assertEqual(research_context_projection(projected),projected)
        files={'assignment.json':json.dumps(original),'task.txt':'Use the installed solver.'}
        text='prefix'+inline_inputs(files)+'suffix'
        actual=project_inline_research_inputs(text)
        self.assertTrue(actual.startswith('prefix\n<current-inputs>'))
        self.assertTrue(actual.endswith('</current-inputs>\nsuffix'))
        packet=json.loads(actual.split('<current-inputs>\n')[1].split('\n</current-inputs>')[0])
        self.assertEqual(json.loads(packet['assignment.json']),projected)
        self.assertEqual(packet['task.txt'],files['task.txt'])
        self.assertEqual(project_inline_research_inputs(actual),actual)
        self.assertEqual(project_inline_research_inputs('arbitrary research prose'),'arbitrary research prose')

    def test_assignment_projection_keeps_original_review_accounting(self):
        dispatch={'backend':{'pinned_files':{'custody':'abc'}},'assignment':{'raw':[3]},
            'status':'completed','receipt':'owned','feedback':'unresolved'}
        request={'kind':'research_work','checkpoint':{'review_dispatches':[dispatch]}}
        assignment=work_assignment({},request)
        self.assertEqual(request['checkpoint']['review_dispatches'],[dispatch])
        self.assertEqual(assignment['research_work']['checkpoint']['review_dispatches'][0]['assignment'],{'raw':[3]})
        self.assertNotIn('backend',assignment['research_work']['checkpoint']['review_dispatches'][0])

    def test_installed_session_projection_is_append_only_and_replays(self):
        session_module=Path('/Users/seungyeop/workspace/dsh/packages/core/session/lib/index.js')
        if not session_module.is_file():self.skipTest('installed DSH session runtime unavailable')
        source_root=Path(__file__).resolve().parents[2]
        code=r'''
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';import {join} from 'node:path';
import {createHash} from 'node:crypto';
const {Session,SessionId}=await import(SESSION_MODULE);
const {apply}=await import(PLUGIN);
const root=mkdtempSync(join(tmpdir(),'research-context-'));
const assignment={research_work:{kind:'research_work',checkpoint:{review_dispatches:[]}},
 scientific_software_tools:{actions:{check_environment:{}},native_arguments_schema:{run:{type:'object',properties:{source:{type:'string'}},required:['source']}}}};
const prior={research_work:{kind:'research_work',feedback:'Same-condition comparison',checkpoint:{
 review_dispatches:[{backend:{pinned_files:{custody:'hash'}},assignment:{raw:[3,4]},feedback:'unresolved',usage:{model_calls:6}}]}}};
const text='prefix\n<current-inputs>\n'+JSON.stringify({'assignment.json':JSON.stringify(prior),'task.txt':'science'})+'\n</current-inputs>\nsuffix';
const s=Session.create(SessionId('offline'));
s.append('user/message',{id:'original',role:'user',source:{kind:'user'},content:[{type:'text',text}]},{surfaceOp:'append'});
const original=JSON.stringify(s.events),count=s.events.length;
const input=join(root,'assignment.json');writeFileSync(input,JSON.stringify(assignment));
const manifest=join(root,'manifest.json');writeFileSync(manifest,JSON.stringify({mode:'research_work',input_delivery:'inline',workspace:root,output:join(root,'response.json'),assignment:input,inputs:{[input]:createHash('sha256').update(JSON.stringify(assignment)).digest('hex')}}));
process.env.SCISAURUS_TASK_CONTRACT=manifest;
const hooks=new Map();
try {
 apply({tools:{guard(){},register(){}},on(name,fn){hooks.set(name,fn)}},
  {python:PYTHON,checker:CHECKER,bundle:BUNDLE});
 let forwarded=0;
 await hooks.get('agent/pre-step')({agent:{session:s}},async()=>{forwarded++;return {kind:'enter'}});
 assert.equal(forwarded,1);assert.equal(JSON.stringify(s.events.slice(0,count)),original);
 const projected=s.deriveMessages()[0].content[0].text;
 const packet=JSON.parse(projected.split('<current-inputs>\n')[1].split('\n</current-inputs>')[0]);
 const body=JSON.parse(packet['assignment.json']),dispatch=body.research_work.checkpoint.review_dispatches[0];
 assert.equal(dispatch.backend,undefined);assert.match(dispatch.backend_sha256,/^[a-f0-9]{64}$/);
 assert.deepEqual(dispatch.assignment,{raw:[3,4]});assert.equal(dispatch.feedback,'unresolved');assert.deepEqual(dispatch.usage,{model_calls:6});
 assert.equal(packet['task.txt'],'science');assert.equal(body.research_work.feedback,prior.research_work.feedback);
 assert.deepEqual(s.events.at(-1).sourceEventSeqs,[0]);
 const length=s.events.length;
 await hooks.get('agent/pre-step')({agent:{session:s}},async()=>({kind:'enter'}));
 assert.equal(s.events.length,length);
 const restored=Session.create(SessionId('offline'),[...s.events]);assert.deepEqual(restored.deriveMessages(),s.deriveMessages());
} finally {rmSync(root,{recursive:true,force:true});}
'''
        for key,value in {'SESSION_MODULE':session_module.as_uri(),
                'PLUGIN':(source_root/'scripts/dsh/intake-contract.mjs').as_uri(),
                'PYTHON':sys.executable,'CHECKER':str(source_root/'scripts/dsh/check-intake-response.py'),
                'BUNDLE':str(source_root)}.items():code=code.replace(key,json.dumps(value))
        result=subprocess.run([shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node',
            '--input-type=module','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_work_packet_has_no_stage_schema_or_other_author_and_preserves_history(self):
        ticket=self.prepare();t=json.loads(ticket.read_bytes());state=json.loads(self.owner.session.path.read_bytes())
        self.assertEqual(state['calls'],json.loads(self.before)['calls']);self.assertEqual(state['session_id'],json.loads(self.before)['session_id'])
        self.assertTrue(all(p.read_bytes()==raw for p,raw in self.originals.items()))
        assignment=work_assignment(self.preflight(ticket)[3],t['step_request'])
        self.assertNotIn('output_contract',assignment);self.assertNotIn('phase',assignment)
        self.assertNotIn('concept_invention',assignment);self.assertIn('run',assignment['scientific_software_tools']['actions'])
        self.assertEqual(t['step_request']['checkpoint']['response'],self.f.response)
        validate_response(assignment,{'concept_design':{'arbitrary_new_mechanism':'preserved research content'}})
        validate_response(assignment,{'tool_action':{'operation':'check_environment','arguments':{}}})
        validate_response(assignment,'# Unstructured research notes\nNo output schema.')
        with self.assertRaises(ValidationError):validate_native_action(assignment,{'operation':'undeclared','arguments':{}})
        self.assertNotIn('response',assignment['scientific_software_tools'])
        self.assertEqual(self.prepare(),ticket)
        ticket.with_suffix('.attempt.json').write_text('{}')
        with self.assertRaises(StateError):self.prepare()

    def test_same_owner_tool_feedback_and_failure_stop_have_no_model_fallback(self):
        t=json.loads(self.prepare().read_bytes());assignment=work_assignment(self.preflight(self.prepare())[3],t['step_request'])
        bench=Mock();bench.execute.return_value={'outcome':'ok','receipt_ref':'real-observation','raw':[1,2]};record={}
        execute=native_tool_handler(assignment,bench,record);action={'operation':'check_environment','arguments':{}}
        result=execute(action)
        self.assertEqual(result,bench.execute.return_value)
        self.assertEqual(record['observations'][0],bench.execute.return_value)
        self.assertEqual(bench.execute.call_count,1)
        bench.execute.return_value={'outcome':'result_unknown','usage':{'model_calls':2}}
        self.assertEqual(execute(action)['outcome'],'result_unknown')
        self.assertEqual(record['observations'][-1],bench.execute.return_value)

    def test_plain_user_feedback_is_delivered_without_a_research_output_contract(self):
        feedback='Use the available established open-source tools for a small physical check; keep unverified claims unresolved.'
        ticket=prepare_work_step(self.owner.root,receipt_sha256=self.f.identity,
            attempt_path=self.f.marker,runtime_python=sys.executable,feedback=feedback)
        assignment=work_assignment(self.preflight(ticket)[3],json.loads(ticket.read_bytes())['step_request'])
        self.assertEqual(assignment['research_work']['feedback'],feedback)
        self.assertNotIn('output_contract',assignment)
        self.assertEqual(json.loads(self.owner.session.path.read_bytes())['calls'],json.loads(self.before)['calls'])

    def test_bookkeeping_step_keeps_earlier_actual_tool_receipts_available(self):
        from scisaurus.runtime.primary_work import work_request
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            job=Path(directory);(job/'input').mkdir();(job/'events.jsonl').write_text('')
            receipt=job/'receipt.json';receipt.write_text('{}')
            prior={'outcome':'ok','receipt_ref':'software:sha256:owned','result':{'output':{'observed':2}}}
            (job/'input/assignment.json').write_text(json.dumps({'research_work':{'checkpoint':{'observations':[prior]}}}))
            note=job/'notes.md';note.write_text('A bookkeeping-only checkpoint')
            context={'record':{'observations':[]},'receipt':{'status':'completed','usage':{'model_calls':1}},
                'receipt_path':receipt,'response':{'notes':note.read_text()},'response_path':note,
                'state':{'session_id':'same-primary','calls':[]}}
            request=work_request(context)
            self.assertEqual(request['checkpoint']['observations'],[prior])
            archive=job/'old-job';(archive/'native-tool-results').mkdir(parents=True)
            result=json.dumps({'observation':prior}).encode();(archive/'native-tool-results/call.json').write_bytes(result)
            old_receipt={'session_id':'same-primary','native_tool_calls':[{'status':'responded','response_file':'call.json','response_sha256':sha(result)}]}
            old_path=archive/'receipt.json';old_path.write_bytes(canonical_bytes(old_receipt))
            context['state']['calls']=[{'receipt':str(old_path),'sha256':sha(old_path.read_bytes())}]
            (job/'input/assignment.json').write_text('{}')
            self.assertEqual(work_request(context)['checkpoint']['observations'],[prior])
            error=json.dumps({'channel':'owned','sessionId':'same-primary','callId':'bad-call','error':'invalid argument'}).encode()
            (archive/'native-tool-results/error.json').write_bytes(error)
            old_receipt['native_tool_calls'].append({'status':'responded','response_file':'error.json','response_sha256':sha(error)})
            old_path.write_bytes(canonical_bytes(old_receipt));context['state']['calls'][0]['sha256']=sha(old_path.read_bytes())
            self.assertEqual(work_request(context)['checkpoint']['observations'],[prior])
            self.assertEqual((archive/'native-tool-results/error.json').read_bytes(),error)

    def test_current_execution_keeps_raw_inline_and_archives_previous_checkpoint_exactly(self):
        from scisaurus.runtime.primary_work import work_request
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            job=Path(directory);(job/'input').mkdir();(job/'native-tool-results').mkdir()
            (job/'events.jsonl').write_text('');note=job/'notes.md';note.write_text('current notes')
            prior={'outcome':'ok','receipt_ref':'prior','result':{'source':'prior source','output':[1,2]}}
            old={'observations':[prior],'archived_checkpoint':{'path':'earlier','sha256':'old'}}
            input_path=job/'input/assignment.json'
            input_path.write_bytes(canonical_bytes({'research_work':{'checkpoint':old}}))
            current={'outcome':'ok','receipt_ref':'current','result':{'source':'current source','output':[3,4]}}
            body=canonical_bytes({'observation':current});(job/'native-tool-results/result.json').write_bytes(body)
            receipt={'session_id':'same','status':'completed','usage':{'model_calls':1},'native_tool_calls':[
                {'status':'responded','response_file':'result.json','response_sha256':sha(body)}]}
            path=job/'receipt.json';path.write_bytes(canonical_bytes(receipt))
            context={'record':{'observations':[current]},'receipt':receipt,'receipt_path':path,
                'response':{'notes':'current notes'},'response_path':note,
                'state':{'session_id':'same','calls':[{'receipt':str(path),'sha256':sha(path.read_bytes())}]}}
            checkpoint=work_request(context)['checkpoint']
            self.assertEqual(checkpoint['observations'],[current])
            archive=checkpoint['archived_checkpoint']
            self.assertEqual(archive['path'],str(input_path.resolve()))
            self.assertEqual(archive['sha256'],sha(input_path.read_bytes()))
            self.assertEqual(json.loads(Path(archive['path']).read_bytes())['research_work']['checkpoint'],old)
            input_path.write_bytes(canonical_bytes({'research_work':{'checkpoint':checkpoint}}))
            context['state']['calls']=[];context['record']['observations']=[]
            context['receipt']={**receipt,'native_tool_calls':[]}
            carried=work_request(context)['checkpoint']
            self.assertEqual(carried['archived_checkpoint'],archive)
            self.assertEqual(carried['observations'],[current])
            input_path.write_bytes(canonical_bytes({'research_work':{'checkpoint':{
                'observations':[{'outcome':'result_unknown'}]}}}))
            context['record']['observations']=[current]
            with self.assertRaisesRegex(StateError,'unknown tool execution'):
                work_request(context)

    def mocked_note(self,ticket,delivery=True,notes=None,workspace_notes=None):
        args=self.preflight(ticket);root,cp,backend,assignment,protected=args;t=json.loads(ticket.read_bytes())
        assignment=work_assignment(assignment,t['step_request']);stub=object.__new__(IntakePrimaryClient);inputs=stub.input_files(assignment,'primary work')
        job=self.owner.project/'intake-jobs/lean-completed';(job/'input').mkdir(parents=True);(job/'outputs').mkdir()
        for name,raw in inputs.items():(job/'input'/name).write_bytes(raw)
        response={'note':'An unfinished mechanism with arbitrary prose; no scientific success claimed.'}
        output_name='research-transcript.md' if notes is not None else 'response.json'
        raw=notes.encode() if notes is not None else canonical_bytes(response)
        if notes is not None:response={'notes':notes}
        (job/'outputs'/output_name).write_bytes(raw)
        output_hashes={output_name:sha(raw)}
        if workspace_notes is not None:
            (job/'outputs/research-notes.md').write_text(workspace_notes)
            output_hashes['research-notes.md']=sha(workspace_notes.encode())
            response['workspace_notes']=workspace_notes
        state=json.loads(self.owner.session.path.read_bytes());body=deepcopy(json.loads(self.f.receipt.read_bytes()))
        body.update(job_directory=str(job),config_sha256=state['config_sha256'],message_id='owned-message',
            input_sha256={n:sha(v) for n,v in inputs.items()},outputs=output_hashes,
            development_session={'binding':self.owner.binding,'prior_receipts':state['calls']})
        manifest=job/'manifest.json';manifest.write_bytes(canonical_bytes({'input_delivery':'inline','mode':'research_work','inputs':{str(job/'input'/name):sha(raw) for name,raw in inputs.items()}}))
        body['task_contract']={'path':str(manifest),'sha256':sha(manifest.read_bytes())};rp=job/'receipt.json';rp.write_bytes(canonical_bytes(body))
        event={'params':{'sessionId':state['session_id'],'event':{'type':'user/message','data':{
            'id':'owned-message','content':[{'type':'text','text':inline_inputs(inputs) if delivery else 'partial'}]}}}}
        (job/'events.jsonl').write_text(json.dumps(event))
        result=SimpleNamespace(response_metadata={'receipt':str(rp)},usage=body['usage'],json_object=lambda:response)
        catalog={'runtimes':[{'label':'continuum','probe_modules':['established-solver'],'capabilities':['mechanical fields']}]}
        with patch('scisaurus.runtime.fresh_intake.preflight',return_value=args),patch('scisaurus.runtime.fresh_intake.IntakePrimaryClient') as client,patch('scisaurus.runtime.fresh_intake.LaboratoryBinding.load',return_value=SimpleNamespace(attestation={},context=lambda:catalog)),patch('scisaurus.runtime.fresh_intake.load_attestation',return_value={}):
            client.return_value.runner.task_contract={};client.return_value.complete.return_value=result
            try:return run_once(root,sys.executable,continuation=ticket,recovery={'source':'offline fixture','observed_at':'2026-01-01T00:00:00Z'})
            finally:
                self.assertEqual(client.return_value.complete.call_count,1)
                self.assertEqual(json.loads(client.return_value.complete.call_args.kwargs['prompt'])['available_open_source_tools'],catalog)

    def test_notes_checkpoint_without_read_rounds_or_mandatory_science_format_review(self):
        ticket=self.prepare();record=self.mocked_note(ticket)
        self.assertEqual(record['status'],'stopped_with_research_progress');self.assertEqual(record['scientific_admission'],'not_assessed')
        self.assertEqual(record['input_read_coverage']['delivery'],'inline');self.assertNotIn('publication_check',record)
        self.assertTrue(json.loads((self.owner.project/'output/run-control.json').read_bytes())['stop_requested'])
        self.assertEqual(json.loads(self.owner.session.path.read_bytes())['calls'],json.loads(self.before)['calls'])

    def test_plain_notes_are_archived_and_read_by_the_next_checkpoint(self):
        from scisaurus.runtime.primary_work import checkpoint_response
        notes='# Mechanism\nA draft with math: F ~ h².\n```python\nprint("observation")\n```\n'
        ticket=self.prepare();record=self.mocked_note(ticket,notes=notes)
        receipt_path=Path(record['receipt']);receipt=json.loads(receipt_path.read_bytes())
        path,response=checkpoint_response(receipt_path,receipt)
        self.assertEqual(path.name,'research-transcript.md');self.assertEqual(path.read_text(),notes)
        self.assertEqual(response,{'notes':notes});self.assertEqual(record['response'],response)
        self.assertEqual(record['status'],'stopped_with_research_progress')
        self.assertEqual(record['scientific_admission'],'not_assessed')
        path.write_text(notes+'changed')
        with self.assertRaises(StateError):checkpoint_response(receipt_path,receipt)

    def test_transcript_and_optional_notes_reach_the_next_prepared_step(self):
        ticket=self.prepare();record=self.mocked_note(ticket,notes='',workspace_notes='# Mechanics\nAn unvalidated result.')
        receipt_path=Path(record['receipt']);receipt=json.loads(receipt_path.read_bytes())
        state_path=self.owner.session.path;state=json.loads(state_path.read_bytes())
        state['calls'].append({'receipt':str(receipt_path),'sha256':sha(receipt_path.read_bytes()),'usage':receipt['usage']})
        state_path.write_bytes(canonical_bytes(state));prior=state_path.read_bytes();receipt_raw=receipt_path.read_bytes()
        next_ticket=prepare_work_step(self.owner.root,receipt_sha256=sha(receipt_raw),
            attempt_path=ticket.with_suffix('.attempt.json'),runtime_python=sys.executable)
        self.preflight(next_ticket)
        next_step=json.loads(next_ticket.read_bytes())['step_request']
        self.assertEqual(next_step['checkpoint']['response'],{'notes':'','workspace_notes':'# Mechanics\nAn unvalidated result.'})
        self.assertEqual(receipt_path.read_bytes(),receipt_raw)
        self.assertEqual(json.loads(state_path.read_bytes())['calls'],json.loads(prior)['calls'])
        self.assertTrue(json.loads((self.owner.project/'output/run-control.json').read_bytes())['stop_requested'])

    def test_primary_client_records_plain_text_without_json_repair_or_extra_call(self):
        from scisaurus.runtime.models import ModelResult
        client=object.__new__(IntakePrimaryClient);client.runtime_python=sys.executable
        client.timeout_seconds=30;client.model='offline-fixture';client.config={}
        client.runner=Mock()
        notes='Unstructured notes with ```code``` and an unfinished JSON sketch: {\n'
        client.runner.run.return_value={'files':{'research-transcript.md':notes.encode(),'research-notes.md':b'# Optional detail'},
            'receipt':'offline-receipt','usage':{'model_calls':1},'elapsed_seconds':.1}
        result=client.complete(system='work',prompt=json.dumps({'research_work':{'kind':'research_work'}}))
        self.assertIsInstance(result,ModelResult);self.assertEqual(result.json_object(),{'notes':notes,'workspace_notes':'# Optional detail'})
        self.assertEqual(client.runner.run.call_count,1)
        self.assertEqual(client.runner.run.call_args.kwargs['outputs'],['research-transcript.md'])

    def test_missing_inline_delivery_is_recorded_without_format_veto(self):
        ticket=self.prepare()
        record=self.mocked_note(ticket,delivery=False)
        self.assertFalse(record['input_read_coverage']['complete'])
        self.assertEqual(record['status'],'stopped_with_research_progress')

    def test_tampered_checkpoint_refuses_and_legacy_route_cannot_replace_work(self):
        ticket=self.prepare();t=json.loads(ticket.read_bytes());t['step_request']['checkpoint']['response']={'invented':'yes'}
        raw=canonical_bytes(t);path=ticket.parent/(sha(raw)+'.json');path.write_bytes(raw)
        with self.assertRaises(StateError):self.preflight(path)
        state=self.owner.session.path.read_bytes()
        self.assertIn('request_review',work_assignment({}, {'kind':'research_work'})['scientific_software_tools']['actions'])
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
    def test_policy_identity_preserves_distinct_python_environment_paths(self):
        import tempfile
        from scisaurus.runtime.intake_guard import guard_backend,guarded_backend
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'composition.json';source.write_text('[]')
            backend=context_backend(root,{'composition':str(source),'read_roots':[],'pinned_files':{}})
            alias=root/'venv-python';alias.symlink_to(sys.executable)
            original=guard_backend(backend,root/'policies',sys.executable,work_mode=True)
            alternate=guard_backend(backend,root/'policies',alias,work_mode=True)
            self.assertNotEqual(original['composition'],alternate['composition'])
            self.assertEqual(guarded_backend(alternate)['python'],str(alias.absolute()))
            self.assertEqual(guard_backend(alternate,root/'policies',alias,work_mode=True),alternate)
            self.assertEqual(guard_backend(backend,root/'policies',sys.executable,work_mode=True),original)

    def test_policy_identity_includes_effective_composition(self):
        import tempfile
        from scisaurus.runtime.intake_guard import guard_backend
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'composition.json'
            source.write_text(json.dumps([{'id':'provider','name':'provider','config':{'model':'first'}}]))
            backend=context_backend(root,{'composition':str(source),'read_roots':[],'pinned_files':{}})
            first=guard_backend(backend,root/'policies',sys.executable,work_mode=True)
            retained=Path(first['composition']).read_bytes()
            source.write_text(json.dumps([{'id':'provider','name':'provider','config':{'model':'second'}}]))
            second=guard_backend(backend,root/'policies',sys.executable,work_mode=True)
            self.assertNotEqual(first['composition'],second['composition'])
            self.assertEqual(Path(first['composition']).read_bytes(),retained)

    def test_work_composition_keeps_native_provider_without_ephemeral_observation_gate(self):
        import tempfile
        from scisaurus.runtime.intake_guard import guard_backend
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'composition.json'
            fs={'id':'native-fs','name':'/installed/dsh/packages/fs/fs-local/lib/index.js'}
            policy={'id':'arbitrary-policy-alias','name':'/installed/dsh/packages/fs/fs-observation-policy/lib/index.js'}
            source.write_text(json.dumps([fs,policy]))
            backend=context_backend(root,{'composition':str(source),'read_roots':[],'pinned_files':{}})
            work=guard_backend(backend,root/'work',sys.executable,work_mode=True)
            actual=json.loads(Path(work['composition']).read_bytes())
            self.assertIn(fs,actual);self.assertNotIn(policy,actual)
            legacy=guard_backend(backend,root/'legacy',sys.executable)
            self.assertIn(policy,json.loads(Path(legacy['composition']).read_bytes()))

    def test_installed_tool_stack_advertises_and_executes_the_same_interface(self):
        import shutil,subprocess,tempfile
        from scisaurus.runtime.intake_guard import guard_backend,guarded_backend
        node=str(Path(shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node').resolve())
        dsh=Path('/Users/seungyeop/workspace/dsh/packages')
        if not (dsh/'core/tools/lib/index.js').exists():self.skipTest('installed DSH unavailable')
        plugin=(Path(__file__).resolve().parents[2]/'scripts/dsh/intake-contract.mjs').as_uri()
        code=r'''
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';import {join} from 'node:path';import {createHash} from 'node:crypto';
const base=DSH;const {Context}=await import(base+'/core/tools/node_modules/@deepseek-ai/cordis/lib/index.js');
const {default:SystemPrompt}=await import(base+'/core/system-prompt/lib/index.js');
const {default:ToolRuntime}=await import(base+'/core/tools/lib/index.js');
const {createScope}=await import(base+'/core/scope/lib/index.js');
const {LocalFileSystem}=await import(base+'/fs/fs-local/lib/index.js');
const FsPolicy=await import(base+'/fs/fs-observation-policy/lib/index.js');
const ToolFs=await import(base+'/fs/tool-fs/lib/index.js');
const Intake=await import(PLUGIN);
const dir=WORKSPACE;
const ctx=new Context();
try{
 await ctx.plugin(SystemPrompt);await ctx.plugin(ToolRuntime);
 await ctx.plugin(LocalFileSystem,{cwd:dir});await ctx.plugin(ToolFs);
 // Global extras reproduce the previous presentation surface without running them.
 for(const name of ['bash','todo_write','subagent','read_image'])ctx.tools.register({name,description:name,parameters:{},
   output:{schema:{type:'string'},render:(_a,v)=>[{type:'text',text:v}]},execute(){throw Error('unexpected extra tool body');}});
 const input=join(dir,'assignment.json');const assignment=JSON.stringify({research_work:{kind:'research_work'},scientific_software_tools:{actions:{check_environment:{},run:{}},native_arguments_schema:{run:RUN_SCHEMA}}});writeFileSync(input,assignment);
 const manifest=join(dir,'manifest.json');writeFileSync(manifest,JSON.stringify({workspace:dir,output:join(dir,'response.json'),
   assignment:input,inputs:{[input]:createHash('sha256').update(assignment).digest('hex')},input_delivery:'inline',mode:'research_work'}));
 process.env.SCISAURUS_TASK_CONTRACT=manifest;await ctx.plugin(Intake,CHECKER_CONFIG);
 const agent={id:'offline-native-owner',session:{header:{cwd:dir}},cancel(){}};let scope;
 await ctx.plugin(Object.assign(inner=>{scope=createScope(inner,agent);agent.ctx=scope.ctx;},{inject:['tools','systemPrompt','fs']}));
 ctx.emit('agent/created',{agent});
 const advertised=ctx.tools.schemas(agent).map(x=>x.name).sort();
 assert.deepEqual(advertised,Intake.nativeTools('research_work').sort());
 const runSchema=ctx.tools.schemas(agent).find(x=>x.name==='research_run').parameters;
 assert.deepEqual(runSchema.required,['source','input','purpose','expected']);
 let counter=0;async function call(name,args){return ctx.tools.execute({agent,name,arguments:args,callId:'offline-'+(++counter),signal:new AbortController().signal});}
 let rejected=await call('research_run',{source:'print(1)',purpose:'scientific_computation',runtime:'runtime'});
 assert.equal(rejected.isError,true);assert.match(JSON.stringify(rejected),/owning execution channel/);
 let result=await call('inspect_workspace',{});assert.equal(result.isError,false);assert.equal(result.value.response_exists,false);
 writeFileSync(join(dir,'resumed-notes.md'),'retained content');
 result=await call('write',{file_path:join(dir,'resumed-notes.md'),content:'replaced without a ritual read'});assert.equal(result.isError,false,JSON.stringify(result));
 result=await call('edit',{file_path:join(dir,'resumed-notes.md'),old_string:'ritual',new_string:'mandatory'});assert.equal(result.isError,false,JSON.stringify(result));
 assert.equal(readFileSync(join(dir,'resumed-notes.md'),'utf8'),'replaced without a mandatory read');
 result=await call('write',{file_path:join(dir,'pilot/source.py'),content:'value = 1\n'});assert.equal(result.isError,false,JSON.stringify(result));
 result=await call('read',{file_path:join(dir,'pilot/source.py')});assert.equal(result.isError,false);
 result=await call('edit',{file_path:join(dir,'pilot/source.py'),old_string:'value = 1',new_string:'value = 2'});assert.equal(result.isError,false,JSON.stringify(result));
 assert.equal(readFileSync(join(dir,'pilot/source.py'),'utf8'),'value = 2\n');
 result=await call('write',{file_path:join(dir,'response.json'),content:'{"note":"unadmitted checkpoint"}'});
 assert.equal(result.isError,false,JSON.stringify(result));
 result=await call('inspect_workspace',{});assert.equal(result.value.response_exists,true);
 result=await call('review_response',{});assert.equal(result.isError,false);assert.equal(result.value.notes_file_present,true);
 result=await call('read',{file_path:join(dir,'response.json')});assert.equal(result.isError,false);
 result=await call('edit',{file_path:join(dir,'response.json'),old_string:'unadmitted checkpoint',new_string:'corrected checkpoint'});
 assert.equal(result.isError,false,JSON.stringify(result));
 assert.equal(JSON.parse(readFileSync(join(dir,'response.json'),'utf8')).note,'corrected checkpoint');
 result=await call('write',{file_path:join(dir,'response.json'),content:'{"broken":'});assert.equal(result.isError,false);
 result=await call('review_response',{});assert.equal(result.isError,false);
 result=await call('write',{file_path:join(dir,'research-notes.md'),content:'# Notes\nA free-form sketch {\n'});
 assert.equal(result.isError,false,JSON.stringify(result));
 result=await call('edit',{file_path:join(dir,'research-notes.md'),old_string:'sketch',new_string:'mechanism'});
 assert.equal(result.isError,false,JSON.stringify(result));
 assert.equal(readFileSync(join(dir,'research-notes.md'),'utf8'),'# Notes\nA free-form mechanism {\n');
 result=await call('bash',{command:'anything'});assert.equal(result.isError,true);
 assert(!advertised.includes('bash'));assert(!advertised.includes('subagent'));
 console.log(JSON.stringify({advertised,source_write_read_edit:true,workspace_inspection:true,provider_requests:0}));
}finally{rmSync(dir,{recursive:true,force:true});}
'''.replace('DSH',json.dumps(str(dsh))).replace('PLUGIN',json.dumps(plugin))
        with tempfile.TemporaryDirectory() as directory:
            from scisaurus.runtime.program_sandbox import SANDBOX_EXEC,sandbox_profile
            root=Path(directory).resolve();work=root/'work';work.mkdir();temporary=root/'tmp';temporary.mkdir()
            composition=root/'composition.json';composition.write_text('[]')
            backend=guard_backend(context_backend(root,{'composition':str(composition),'read_roots':[],'pinned_files':{}}),
                root/'sealed',sys.executable,work_mode=True)
            from scisaurus.runtime.software_workbench import run_arguments_schema
            code=code.replace('CHECKER_CONFIG',json.dumps(guarded_backend(backend))).replace('WORKSPACE',json.dumps(str(work))).replace('RUN_SCHEMA',json.dumps(run_arguments_schema()))
            command=[node,'--input-type=module','-e',code]
            profile=sandbox_profile(work,[node],allow_network=False,temporary_workspace=temporary,
                read_only_paths=[str(dsh.parent),str(root/'sealed'),str(Path(sys.executable).resolve().parent.parent),
                    str(Path(sys.executable).absolute().parent.parent),
                    str(Path(__file__).resolve().parents[2]/'scripts/dsh')])
            result=subprocess.run([SANDBOX_EXEC,'-p',profile,*command],cwd=work,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_inline_free_notes_and_optional_review_use_installed_native_schema(self):
        import shutil,subprocess,tempfile
        node=shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node'
        plugin=(Path(__file__).resolve().parents[2]/'scripts/dsh/intake-contract.mjs').as_uri()
        code=r'''
import {mkdtempSync,writeFileSync,rmSync,realpathSync,symlinkSync} from 'node:fs';import {tmpdir} from 'node:os';
import {join} from 'node:path';import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
const {createPolicy,apply,nativeTools}=await import(PLUGIN);const dir=realpathSync(mkdtempSync(join(tmpdir(),'primary-work-')));
try{const path=join(dir,'assignment.json');const raw=JSON.stringify({research_work:{kind:'research_work'},scientific_software_tools:{actions:{check_environment:{},run:{}},native_arguments_schema:{run:RUN_SCHEMA}}});
writeFileSync(path,raw);const manifest={workspace:dir,output:join(dir,'response.json'),assignment:path,
 inputs:{[path]:createHash('sha256').update(raw).digest('hex')},input_delivery:'inline',mode:'research_work'};
const policy=createPolicy(manifest,JSON.parse);const agent={};
assert.equal(policy.pending(agent).length,0);
assert.equal(policy.guard({name:'write',agent,arguments:{file_path:manifest.output,content:'{"new_mechanism":"free notes"}'}}),undefined);
assert.equal(policy.guard({name:'review_response',agent,arguments:{}}),undefined);
assert.match(policy.guard({name:'bash',agent,arguments:{command:'other author'}}),/declared native interface/);
assert.equal(policy.guard({name:'write',agent,arguments:{file_path:join(dir,'pilot/source.py'),content:'source'}}),undefined);
assert.equal(policy.guard({name:'edit',agent,arguments:{file_path:join(dir,'source.py')}}),undefined);
assert.equal(policy.guard({name:'edit',agent,arguments:{file_path:manifest.output}}),undefined);
assert.equal(policy.guard({name:'write',agent,arguments:{file_path:manifest.output,content:'unstructured { notes'}}),undefined);
assert.match(policy.guard({name:'write',agent,arguments:{file_path:join(dir,'../foreign.py'),content:'source'}}),/owning workspace/);
symlinkSync('/tmp',join(dir,'escape'));
assert.match(policy.guard({name:'write',agent,arguments:{file_path:join(dir,'escape/source.py'),content:'source'}}),/owning workspace/);
symlinkSync('/tmp/missing-scisaurus-native-file',join(dir,'dangling'));
assert.match(policy.guard({name:'write',agent,arguments:{file_path:join(dir,'dangling'),content:'source'}}),/owning workspace/);
const mp=join(dir,'manifest.json');writeFileSync(mp,JSON.stringify(manifest));process.env.SCISAURUS_TASK_CONTRACT=mp;
let registered,inspect,created;apply({tools:{guard(){},register(value){registered=value;if(value.name==='inspect_workspace')inspect=value;}},on(name,callback){if(name==='agent/created')created=callback;}},{});
assert.equal(registered.name,'review_response');assert.match(registered.description,/No file or output format/);
assert.equal(registered.execute().notes_file_present,false);assert.equal(registered.execute().sha256,null);
let mask;created({agent:{ctx:{tools:{restrict(value){mask=value;}}}}});assert.deepEqual(mask.allow,nativeTools('research_work'));
assert(!mask.allow.includes('bash'));assert(!mask.allow.includes('todo_write'));
assert.equal(inspect.execute().response_exists,false);
writeFileSync(manifest.output,'{}');assert.equal(inspect.execute().response_exists,true);
symlinkSync(manifest.output,join(dir,'alias'));
assert.match(policy.guard({name:'write',agent,arguments:{file_path:join(dir,'alias'),content:'invalid'}}),/owning workspace/);
}finally{rmSync(dir,{recursive:true});}
'''.replace('PLUGIN',json.dumps(plugin))
        from scisaurus.runtime.software_workbench import run_arguments_schema
        code=code.replace('RUN_SCHEMA',json.dumps(run_arguments_schema()))
        result=subprocess.run([node,'--input-type=module','-e',code],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
