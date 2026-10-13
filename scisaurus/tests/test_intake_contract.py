"""Native input admission and exact publication share the controller's contract."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scisaurus.tests.context_fixtures import context_backend
from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.intake_contract import delivered_assignment, strict_document, validate_response
from scisaurus.runtime.intake_guard import guard_backend, guarded_backend, verify_publication
from scisaurus.runtime.structured_recovery import prepare_guarded_continuation
from scisaurus.tests.test_structured_recovery import StructuredRecoveryTests


class IntakeContractTests(unittest.TestCase):
    def setUp(self):
        self.f=StructuredRecoveryTests('test_accounting_retains_unknown_usage_backend_and_exact_draft')
        self.f.setUp();self.owner=self.f.owner
        self.ticket=self.f.prepare();record=self.f.run_mocked(self.ticket)
        state=json.loads(self.owner.session.path.read_bytes());self.receipt=Path(record['receipt'])
        self.response=deepcopy(record['response'])
        self.response['concept_design']['concepts'][1]['working_principle']['mechanism_development']='unaccepted draft prose'
        job=self.receipt.parent;(job/'outputs').mkdir(exist_ok=True)
        (job/'outputs/response.json').write_bytes(canonical_bytes(self.response))
        body=json.loads(self.receipt.read_bytes());body.update(outputs={'response.json':sha((job/'outputs/response.json').read_bytes())},
            development_session={'binding':self.owner.binding,'prior_receipts':state['calls']})
        self.receipt.write_bytes(canonical_bytes(body));self.identity=sha(self.receipt.read_bytes())
        state['calls'].append({'receipt':str(self.receipt),'sha256':self.identity,'usage':body['usage']})
        self.owner.session.state=state;self.owner.session.save()
        record.update(status='stopped_on_error',response=self.response,error='ValidationError: exact fields rejected')
        self.marker=self.ticket.with_suffix('.attempt.json');raw=canonical_bytes(record)
        self.marker.write_bytes(raw);(self.owner.project/'intake-controller'/(sha(raw)+'.json')).write_bytes(raw)
        self.before=self.owner.session.path.read_bytes();self.original=self.receipt.read_bytes()
        self.assignment=delivered_assignment(job/'input/assignment.json')

    def tearDown(self):self.f.tearDown()

    def prepare(self):
        return prepare_guarded_continuation(self.owner.root,receipt_sha256=self.identity,
            attempt_path=self.marker,runtime_python=sys.executable)

    def test_exact_nested_fields_cannot_be_widened(self):
        with self.assertRaisesRegex(ValidationError,r"concepts\[1\].working_principle.*extra.*mechanism_development"):
            validate_response(self.assignment,self.response)
        good=deepcopy(self.response);del good['concept_design']['concepts'][1]['working_principle']['mechanism_development']
        validate_response(self.assignment,good)

    def test_duplicate_and_nonfinite_json_refuse(self):
        for body in ('{"a":1,"a":2}','{"a":NaN}','{"a":1e999}'):
            with self.assertRaises(ValidationError):strict_document(body)

    def test_guarded_recovery_preserves_history_draft_route_and_is_idempotent(self):
        ticket=self.prepare();value=json.loads(ticket.read_bytes());state=json.loads(self.owner.session.path.read_bytes())
        old=json.loads(self.before)
        self.assertEqual(state['session_id'],old['session_id']);self.assertEqual(state['calls'],old['calls'])
        self.assertEqual(self.receipt.read_bytes(),self.original)
        self.assertEqual(len(state.get('backend_revisions',[])),len(old.get('backend_revisions',[]))+1)
        self.assertEqual(value['prior_receipt_status'],'completed')
        self.assertEqual(value['step_request']['draft_status'],'completed_unadmitted')
        self.assertIn('mechanism_development',value['step_request']['prior_contract_error'])
        guarded_backend(value['backend']);self.f.preflight(ticket)
        self.assertEqual(self.prepare(),ticket)

    def test_bundled_checker_is_the_same_authoritative_check(self):
        ticket=self.prepare();backend=json.loads(ticket.read_bytes())['backend'];config=guarded_backend(backend)
        request={'assignment':str(self.receipt.parent/'input/assignment.json'),
                 'assignment_sha256':sha((self.receipt.parent/'input/assignment.json').read_bytes()),
                 'document':json.dumps(self.response)}
        result=subprocess.run([config['python'],config['checker'],config['bundle']],input=json.dumps(request),
            text=True,capture_output=True)
        self.assertEqual(result.returncode,1);self.assertIn('mechanism_development',result.stderr)
        del self.response['concept_design']['concepts'][1]['working_principle']['mechanism_development']
        request['document']=json.dumps(self.response)
        result=subprocess.run([config['python'],config['checker'],config['bundle']],input=json.dumps(request),
            text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(json.loads(result.stdout)['validated'])

    def test_multiple_transport_revisions_retain_the_same_stopped_frontier(self):
        ticket=self.prepare();old=json.loads(ticket.read_bytes())['backend']
        state_raw=self.owner.session.path.read_bytes();state=json.loads(state_raw)
        directory=self.owner.project/'intake-controller/alternate-policy';directory.mkdir()
        active=guard_backend(old,directory,sys.executable)
        snapshot=directory/'session.json';snapshot.write_bytes(state_raw)
        proof={'schema_version':'recovered-development-backend-1','development_session':self.owner.binding,
            'session_id':state['session_id'],'previous_session':str(snapshot),
            'previous_session_sha256':sha(state_raw),'previous_config_sha256':state['config_sha256'],
            'previous_backend':old,'backend':active,'backend_sha256':sha(canonical_bytes(active))}
        raw=canonical_bytes(proof);path=directory/(sha(raw)+'.json');path.write_bytes(raw)
        self.owner.session.bind_recovered_backend(active,expected_state_sha256=sha(state_raw),binding_ref=path)
        next_ticket=self.prepare();self.f.preflight(next_ticket)
        self.assertEqual(self.prepare(),next_ticket)
        self.assertEqual(json.loads(self.owner.session.path.read_bytes())['calls'],state['calls'])
        self.assertEqual(self.receipt.read_bytes(),self.original)

    def test_publication_requires_current_authoritative_result(self):
        with self.assertRaisesRegex(StateError,'exact successful native contract check'):verify_publication(self.receipt)
        path=self.receipt.parent/'events.jsonl';rows=[json.loads(x) for x in path.read_text().splitlines()]
        body=json.loads(self.receipt.read_bytes());proof={'validated':True,'sha256':body['outputs']['response.json']}
        for kind,data in [('tool/call',{'name':'validate_response','callId':'check','arguments':'{}'}),
            ('tool/result',{'message':{'content':[{'toolCallId':'check','isError':False,
                'content':[{'type':'text','text':json.dumps(proof)}]}]}})]:
            rows.append({'params':{'sessionId':body['session_id'],'event':{'type':kind,'data':{'turn':1,**data}}}})
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        self.assertEqual(verify_publication(self.receipt)['response_sha256'],proof['sha256'])
        rows.append({'params':{'sessionId':body['session_id'],'event':{'type':'tool/call','data':
            {'turn':1,'name':'write','callId':'later','arguments':'{}'}}}})
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        with self.assertRaises(StateError):verify_publication(self.receipt)


class NativeInputPolicyTests(unittest.TestCase):
    def test_batch_manifest_uses_a_directory_grant_and_setup_failure_keeps_owned_receipt(self):
        from scisaurus.runtime.dsh_batch import DshBatchRunner,DshBatchError
        from scisaurus.tests.test_development_session import DevelopmentSessionTests
        fixture=DevelopmentSessionTests('test_history_workspace_and_usage_continue_across_producer_boundaries')
        fixture.setUp()
        try:
            runner=DshBatchRunner(fixture.config,root=fixture.project/'contract-jobs',development_session=fixture.binding)
            runner.task_contract={}
            result=runner.run('offline fixture',inputs={'assignment.json':b'{}'},outputs=['answer.txt'])
            receipt=json.loads(Path(result['receipt']).read_bytes());manifest=Path(receipt['task_contract']['path'])
            self.assertTrue(manifest.parent.is_dir());self.assertEqual(manifest.name,'manifest.json')
            before=(fixture.session.path if hasattr(fixture,'session') else Path(fixture.binding['root'])/'session.json')
            state=json.loads(before.read_bytes());archives={Path(c['receipt']):Path(c['receipt']).read_bytes() for c in state['calls']}
            failed=DshBatchRunner(fixture.config,root=fixture.project/'failed-contract-jobs',development_session=fixture.binding)
            failed.task_contract={}
            with patch('scisaurus.runtime.dsh_batch.sandbox_profile',side_effect=ValidationError('profile setup failed')), \
                 patch('scisaurus.runtime.dsh_batch.start_process') as start:
                with self.assertRaises(DshBatchError) as error:
                    failed.run('offline fixture',inputs={'assignment.json':b'{}'},outputs=['answer.txt'])
                start.assert_not_called()
            path=Path(error.exception.receipt);body=json.loads(path.read_bytes())
            self.assertEqual(body['status'],'not_dispatched');self.assertEqual(body['usage']['model_calls'],0)
            self.assertEqual(body['development_session']['binding'],fixture.binding)
            self.assertEqual(len(list(failed.root.glob('*/receipt.json'))),1)
            after=json.loads(before.read_bytes());self.assertIsNone(after['active_job']);self.assertEqual(after['calls'],state['calls'])
            self.assertTrue(all(p.read_bytes()==raw for p,raw in archives.items()))
        finally:fixture.tearDown()

    def test_native_registration_uses_the_installed_raw_json_schema_contract(self):
        node=shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node'
        tools=Path('/Users/seungyeop/workspace/dsh/packages/core/tools/lib/index.js')
        if not tools.is_file():self.skipTest('installed DSH schema validator unavailable')
        plugin=(Path(__file__).resolve().parents[2]/'scripts/dsh/intake-contract.mjs').as_uri()
        code=r'''
import {mkdtempSync,writeFileSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';import {join} from 'node:path';import {createHash} from 'node:crypto';
const {assertSupportedJsonSchema}=await import(TOOLS);const {apply}=await import(PLUGIN);
const root=realpathSync(mkdtempSync(join(tmpdir(),'intake-register-')));const input=join(root,'assignment.json');
writeFileSync(input,'{}\n');const manifest=join(root,'manifest.json');
writeFileSync(manifest,JSON.stringify({workspace:root,output:join(root,'response.json'),inputs:{[input]:createHash('sha256').update('{}\n').digest('hex')}}));
process.env.SCISAURUS_TASK_CONTRACT=manifest;let registered=false;
try{apply({tools:{guard(){},register(definition){assertSupportedJsonSchema(definition.output.schema);registered=true;}},on(){}},{});
 if(!registered)throw new Error('native validator not registered');
}finally{rmSync(root,{recursive:true});}
'''.replace('TOOLS',json.dumps(tools.as_uri())).replace('PLUGIN',json.dumps(plugin))
        result=subprocess.run([node,'--input-type=module','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_tool_action_shapes_and_literature_owners_are_checked_before_publication(self):
        from scisaurus.runtime.software_workbench import tool_contract
        assignment={'scientific_software_tools':tool_contract()}
        validate_response(assignment,{'tool_action':{'operation':'check_environment','arguments':{}}})
        for action in ({'operation':'check_environment','extra':'undeclared','arguments':{}},
                       {'operation':'check_environment'},
                       {'operation':'check_environment','arguments':{'undeclared':True}}):
            with self.assertRaises(ValidationError):validate_response(assignment,{'tool_action':action})
        assignment={'scientific_software_tools':{'actions':{'collect_literature':{}}},
                    'literature_acquisition':{'candidate_ids':['same-owner']}}
        action={'operation':'collect_literature','arguments':{'requests':[
            {'candidate_id':'same-owner','query':'physical mechanism','limit':5}]}}
        validate_response(assignment,{'tool_action':action})
        action['arguments']['requests'][0]['candidate_id']='another-owner'
        with self.assertRaises(ValidationError):validate_response(assignment,{'tool_action':action})

    def test_noop_stopper_is_replaced_with_pinned_authoritative_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);noop=root/'noop.mjs';noop.write_text('export function apply() {}')
            composition=root/'composition.json';composition.write_text(json.dumps([
                {'id':'stop-on-tool-error','name':str(noop)}]))
            backend=context_backend(root,{'composition':str(composition),'read_roots':[],'pinned_files':{}})
            active=guard_backend(backend,root/'sealed',sys.executable);guarded_backend(active)
            stopper=next(x for x in json.loads(Path(active['composition']).read_bytes()) if x['id']=='stop-on-tool-error')
            self.assertNotEqual(stopper['name'],str(noop));self.assertIn(stopper['name'],active['pinned_files'])

    def test_missing_wrong_partial_failed_and_foreign_reads_cannot_unlock_work(self):
        node=shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node'
        plugin=(Path(__file__).resolve().parents[2]/'scripts/dsh/intake-contract.mjs').as_uri()
        code=r'''
import {mkdtempSync,writeFileSync,rmSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';import {join} from 'node:path';
import {createHash} from 'node:crypto';import assert from 'node:assert/strict';
const {createPolicy}=await import(PLUGIN);
const root=realpathSync(mkdtempSync(join(tmpdir(),'intake-policy-')));const path=join(root,'held-candidates.json');
writeFileSync(path,'one\ntwo\n');const digest=createHash('sha256').update('one\ntwo\n').digest('hex');
const manifest={workspace:root,output:join(root,'response.json'),inputs:{[path]:digest}};
let checks=0;const policy=createPolicy(manifest,document=>{checks++;if(document==='bad')throw new Error('extra field');});
const agent={};const read={name:'read',arguments:{file_path:path},agent};
const work={name:'write',arguments:{file_path:manifest.output,content:'good'},agent};
function shown(text,isError=false,owner=agent){policy.observe({...read,agent:owner},{isError,content:[{type:'text',text:`<path>${path}</path>\n${text}`}]});}
try{
 assert.match(policy.guard(work),/Read every/);assert.equal(checks,0);
 shown('1: wrong\n2: two');assert.match(policy.guard(work),/Read every/);
 shown('1: one',true);assert.match(policy.guard(work),/Read every/);
 shown('1: one',false,{});assert.match(policy.guard(work),/Read every/);
 shown('1: one');assert.equal(policy.guard(work),undefined);assert.equal(checks,1);
 assert.match(policy.guard({...work,arguments:{...work.arguments,content:'bad'}}),/extra field/);
 assert.match(policy.guard({name:'bash',arguments:{command:'echo passed'},agent}),/declared native interface/);
 assert.match(policy.guard({name:'edit',arguments:{file_path:manifest.output},agent}),/declared native interface/);
 assert.equal(policy.guard({name:'validate_response',arguments:{},agent}),undefined);
 const first=createPolicy({...manifest,first_input:path},()=>{});
 assert.match(first.guard({name:'read',arguments:{file_path:manifest.output},agent}),/Read every/);
 writeFileSync(path,'changed');assert.match(first.guard(read),/sealed input changed/);
}finally{rmSync(root,{recursive:true});}
'''.replace('PLUGIN',json.dumps(plugin))
        result=subprocess.run([node,'--input-type=module','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
