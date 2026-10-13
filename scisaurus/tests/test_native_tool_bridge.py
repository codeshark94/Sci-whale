"""Offline ownership and installed-runtime checks for native controller tools."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import Mock,patch

from scisaurus.core.errors import ValidationError
from scisaurus.runtime.native_tool_bridge import NativeToolBridge,METHOD
from scisaurus.runtime.dsh_batch import DshBatchRunner,DshBatchError,sha256
from scisaurus.runtime.intake_guard import guard_backend,guarded_backend,GUARD_READY
from scisaurus.runtime.primary_work import work_assignment,native_tool_handler,work_instructions
from scisaurus.runtime.software_workbench import SoftwareWorkbench
from scisaurus.runtime.development_session import _verify_receipt_files
from scisaurus.runtime.software_workbench import run_arguments_schema,validate_run_arguments


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.state={'session_id':'owner'};self.handler=Mock(return_value={'outcome':'ok','raw':[1,2]})
        self.bridge=NativeToolBridge(self.root,self.state,self.handler,lambda:None,time.monotonic()+60)
        self.action={'operation':'check_environment','arguments':{}}
    def tearDown(self):self.temp.cleanup()
    def request(self):
        self.bridge.observe({'method':'session.event','params':{'sessionId':'owner','event':{'type':'tool/call',
            'data':{'name':'research_tool','callId':'call','arguments':json.dumps(self.action)}}}})
        return {'method':METHOD,'params':{'sessionId':'owner','channel':self.bridge.channel,'callId':'call','action':self.action}}
    def test_actual_reply_is_sealed_and_duplicate_requests_never_repeat_execution(self):
        request=self.request();self.bridge.respond(request);self.handler.assert_called_once_with(self.action)
        record=self.state['native_tool_calls'][0];path=self.bridge.directory/record['response_file'];raw=path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),record['response_sha256'])
        self.assertEqual(json.loads(raw)['observation'],self.handler.return_value)
        with self.assertRaises(ValidationError):self.bridge.respond(request)
        self.assertEqual(self.handler.call_count,1)
        receipt={'native_tool_calls':self.state['native_tool_calls']};rp=self.root/'receipt.json'
        _verify_receipt_files(rp,receipt);path.chmod(0o644);path.write_text('{}')
        with self.assertRaises(ValidationError):_verify_receipt_files(rp,receipt)
    def test_foreign_or_altered_requests_fail_before_the_handler(self):
        request=self.request();request['params']['sessionId']='foreign'
        with self.assertRaises(ValidationError):self.bridge.respond(request)
        self.handler.assert_not_called()
    def test_numeric_and_boolean_argument_identity_is_exact(self):
        self.action={'operation':'anything','arguments':{'value':1}};request=self.request()
        request['params']['action']=deepcopy(self.action);request['params']['action']['arguments']['value']=True
        with self.assertRaises(ValidationError):self.bridge.respond(request)
        self.handler.assert_not_called()
    def test_failure_reply_retains_the_error_without_retry(self):
        request=self.request();self.handler.side_effect=ValidationError('owned operation failed')
        self.bridge.respond(request);record=self.state['native_tool_calls'][0]
        reply=json.loads((self.bridge.directory/record['response_file']).read_bytes())
        self.assertIn('owned operation failed',reply['error']);self.assertEqual(record['outcome'],'error')
        self.assertEqual(self.handler.call_count,1)
    def test_expired_or_stopped_request_never_enters_the_handler(self):
        request=self.request();self.bridge.deadline=time.monotonic()-1
        with self.assertRaisesRegex(ValidationError,'deadline'):self.bridge.respond(request)
        self.handler.assert_not_called();self.assertNotIn('native_tool_calls',self.state)
        self.bridge.deadline=time.monotonic()+60
        with patch('scisaurus.runtime.native_tool_bridge.ensure_run_allowed',side_effect=ValidationError('stopped')):
            with self.assertRaisesRegex(ValidationError,'stopped'):self.bridge.respond(request)
        self.handler.assert_not_called();self.assertNotIn('native_tool_calls',self.state)
    def test_unobserved_null_request_cannot_enter_the_handler(self):
        request={'method':METHOD,'params':{'channel':self.bridge.channel,'sessionId':'owner','callId':'ghost','action':None}}
        with self.assertRaisesRegex(ValidationError,'owner'):self.bridge.respond(request)
        self.handler.assert_not_called()
    def test_first_failure_or_unknown_latches_before_another_buffered_request(self):
        request=self.request();self.handler.return_value={'outcome':'result_unknown','raw':'preserved'}
        self.bridge.respond(request)
        self.bridge.observe({'method':'session.event','params':{'sessionId':'owner','event':{'type':'tool/call',
            'data':{'name':'research_tool','callId':'second','arguments':json.dumps(self.action)}}}})
        second=deepcopy(request);second['params']['callId']='second'
        with self.assertRaisesRegex(ValidationError,'already stopped'):self.bridge.respond(second)
        self.assertEqual(self.handler.call_count,1);self.assertEqual(self.state['native_tool_calls'][0]['outcome'],'result_unknown')
    def test_direct_computation_arguments_keep_the_exact_bridge_owner(self):
        args={'source':'program','input':{},'purpose':'scientific_computation','expected':None,'runtime':'declared'}
        self.bridge.observe({'method':'session.event','params':{'sessionId':'owner','event':{'type':'tool/call',
            'data':{'name':'research_run','callId':'direct','arguments':json.dumps(args)}}}})
        self.bridge.respond({'method':METHOD,'params':{'channel':self.bridge.channel,'sessionId':'owner','callId':'direct',
            'action':{'operation':'run','arguments':args}}})
        self.handler.assert_called_once_with({'operation':'run','arguments':args})

    def test_known_error_and_failed_computation_leave_primary_running(self):
        for value in [ValidationError('wrong input'),{'outcome':'failed','stderr':'program error'}]:
            with self.subTest(value=value):
                self.bridge.calls.clear();self.bridge.failed=False
                self.action={'operation':'check_environment','arguments':{}}
                request=self.request();request['params']['callId']='call-'+str(len(self.state.get('native_tool_calls',[])))
                self.bridge.calls[request['params']['callId']]=self.bridge.calls.pop('call')
                self.handler.side_effect=value if isinstance(value,Exception) else None
                self.handler.return_value=value
                self.bridge.respond(request)
                self.assertFalse(self.bridge.failed)
                result=json.loads((self.bridge.directory/self.state['native_tool_calls'][-1]['response_file']).read_bytes())
                self.assertNotIn('terminal',result)

    def test_nested_unknown_keeps_its_receipt_usage_and_terminal_status(self):
        error=DshBatchError('unknown review',receipt='/owned/review/receipt.json',usage={'model_calls':3})
        self.handler.side_effect=error;self.bridge.respond(self.request())
        result=json.loads((self.bridge.directory/self.state['native_tool_calls'][-1]['response_file']).read_bytes())
        self.assertTrue(result['terminal']);self.assertEqual(result['dispatch']['receipt'],error.receipt)
        self.assertEqual(result['dispatch']['usage'],error.usage)


class RunArgumentTests(unittest.TestCase):
    def test_required_reference_artifact_and_runtime_contracts_are_checked_before_execution(self):
        valid={'source':'program','input':{},'purpose':'scientific_computation','expected':None,'runtime':'declared'}
        validate_run_arguments(valid)
        for change in ({'expected':{}},{'expected':{'value':{},'absolute_tolerance':-1,'relative_tolerance':0}},
                       {'inputs':[{}]},{'outputs':[{'name':'file','extra':'invalid'}]},
                       {'environment_ref':'also-set'}):
            with self.subTest(change=change),self.assertRaises(ValidationError):validate_run_arguments({**valid,**change})
        for field in ('input','expected'):
            missing=deepcopy(valid);del missing[field]
            with self.assertRaises(ValidationError):validate_run_arguments(missing)
        schema=run_arguments_schema();reference=schema['properties']['expected']['oneOf'][1]
        self.assertEqual(set(reference['required']),{'value','absolute_tolerance','relative_tolerance'})


@unittest.skipUnless(os.environ.get('SCISAURUS_DSH_CONFIG'),'explicit installed runtime configuration required')
class InstalledRuntimeTests(unittest.TestCase):
    def exercise(self,failure=False,run_program=False,review=False,resume=False):
        requests=[]
        laboratory=None;runtime_label=None
        if run_program:
            from scisaurus.runtime.laboratory import LaboratoryBinding,load_attestation
            laboratory=LaboratoryBinding.load(os.environ['SCISAURUS_LAB_CONFIG'])
            laboratory.attestation=load_attestation(os.environ['SCISAURUS_LAB_ATTESTATION'])
            runtime_label=next(r['label'] for r in laboratory.laboratory['runtimes'] if r['kind']=='venv')
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(body)
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                def event(data):self.wfile.write(('data: '+json.dumps(data)+'\n\n').encode());self.wfile.flush()
                event({'id':'offline-'+str(len(requests)),'model':body['model'],
                    'choices':[{'index':0,'delta':{'role':'assistant','content':None}}]})
                if resume and len(requests)==4:
                    text='\n'.join(message['content'] if isinstance(message.get('content'),str) else
                        '\n'.join(block.get('text','') for block in (message.get('content') or []) if block.get('type')=='text')
                        for message in body['messages'])
                    refs=json.loads(text.split('<retained-inputs>\n')[1].split('\n</retained-inputs>')[0])
                    name='read_retained_input';args={**refs['inputs']['assignment.json'],'start':0,'max_chars':32000}
                elif len(requests)==1:
                    name='research_tool';args={'operation':'check_environment','arguments':{}}
                    if run_program:name='research_run';args={'runtime':runtime_label,
                        'source':'import json\nprint(json.dumps({"value": 2 + 2}))\n','input':{},
                        'purpose':'scientific_computation','expected':None}
                elif review:name=None
                elif failure and len(requests)==2:
                    name='research_tool';args={'operation':'check_environment','arguments':{}}
                elif len(requests)==(3 if failure else 2):
                    name='write';args={'file_path':'response.json','content':'{"note":"Unadmitted local tool checkpoint"}'}
                else:name=None
                if name:
                    event({'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'offline-call-'+str(len(requests)),
                        'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}]});finish='tool_calls'
                else:event({'choices':[{'index':0,'delta':{'content':'Checkpoint saved.'}}]});finish='stop'
                event({'choices':[{'index':0,'delta':{},'finish_reason':finish}],
                    'usage':{'prompt_tokens':20,'completion_tokens':10}})
                self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ):
                root=Path(directory).resolve();config=json.loads(Path(os.environ['SCISAURUS_DSH_CONFIG']).read_bytes())
                composition=json.loads(Path(config['composition']).read_bytes())
                profile=next(x['config']['providers'][config['provider']] for x in composition if x.get('id')=='llm')
                profile.update(baseURL=f'http://127.0.0.1:{server.server_port}/v1',apiKeyEnv='DSH_OFFLINE_KEY')
                path=root/'composition.json';path.write_text(json.dumps(composition));config['pinned_files'].pop(config['composition'])
                config.update(composition=str(path),auth_env='DSH_OFFLINE_KEY');config['pinned_files'][str(path)]=sha256(path)
                config['read_roots'].append(str(root));config=guard_backend(config,root/'contract',sys.executable,work_mode=True)
                os.environ['DSH_OFFLINE_KEY']='synthetic-localhost-only'
                os.environ.pop('SCISAURUS_RUN_CONTROL',None);os.environ.pop('SCISAURUS_RUN_GENERATION',None)
                if review:
                    from scisaurus.runtime.requested_review import request_review
                    producer=root/'producer';producer.mkdir();(producer/'private-history.txt').write_text('private fixture history')
                    records=[]
                    result=request_review({'author_backend':config,'runtime_python':sys.executable},producer/'reviews',
                        {'task':'check current raw','evidence':{'raw':[1,2]}},deadline=time.monotonic()+30,
                        isolated_from=[producer],record=records.append)
                    self.assertIn('Checkpoint saved.',result['feedback']['notes'])
                    self.assertEqual([x['status'] for x in records],['started','completed'])
                    rp=Path(records[-1]['receipt']);receipt=json.loads(rp.read_bytes())
                    self.assertNotIn('development_session',receipt);self.assertTrue(receipt['process_reaped'])
                    _verify_receipt_files(rp,receipt)
                    manifest=json.loads(Path(receipt['task_contract']['path']).read_bytes())
                    self.assertNotIn('retained_inputs',manifest)
                    names={t['function']['name'] for t in requests[0]['tools']}
                    self.assertIn('research_run',names);self.assertNotIn('write',names);self.assertNotIn('edit',names)
                    return
                assignment=work_assignment({'objective':'Offline interface fixture'},{'kind':'research_work','checkpoint':{}})
                if resume:assignment['historical_science']='ARCHIVED_SCIENCE_MARKER'+(' original evidence'*1000)
                record={};bench=SoftwareWorkbench(root/'software',deadline=time.monotonic()+30,laboratory=laboratory)
                execute=native_tool_handler(assignment,bench,record)
                if failure:execute=Mock(side_effect=[ValidationError('offline controlled tool failure'),{'outcome':'ok','receipt_ref':'corrected-check'}])
                from scisaurus.runtime.development_session import development_session_binding,DevelopmentSession
                runner=DshBatchRunner(config,root=root/'jobs',runtime_read_roots=[str(Path(sys.executable).absolute().parent.parent)],
                    development_session=development_session_binding(root) if resume else None,
                    required_startup_marker=GUARD_READY,forbid_reasoning=True)
                runner.task_contract={**guarded_backend(config),'mode':'research_work','input_delivery':'inline','native_controller_tools':True}
                runner.native_tool_handler=execute
                result=runner.run(work_instructions(),inputs={'assignment.json':json.dumps(assignment)},outputs=['response.json'])
                if resume:
                    first=Path(result['receipt']);original=(first.parent/'input/assignment.json').read_bytes()
                    current=work_assignment({'objective':'Current research','current_science':'CURRENT_FULL_INPUT_MARKER'},
                        {'kind':'research_work','checkpoint':{}})
                    second=runner.run(work_instructions(),inputs={'assignment.json':json.dumps(current)},outputs=['research-transcript.md'])
                    receipt=json.loads(Path(second['receipt']).read_bytes());previous=json.loads(first.read_bytes())
                    self.assertEqual(receipt['session_id'],previous['session_id']);self.assertEqual(len(requests),5)
                    first_restored=json.dumps(requests[3]['messages'])
                    self.assertIn('<retained-inputs>',first_restored)
                    self.assertIn('CURRENT_FULL_INPUT_MARKER',first_restored)
                    self.assertNotIn('ARCHIVED_SCIENCE_MARKER',first_restored)
                    self.assertIn('ARCHIVED_SCIENCE_MARKER',json.dumps(requests[4]['messages']))
                    self.assertEqual((first.parent/'input/assignment.json').read_bytes(),original)
                    manifest=json.loads(Path(receipt['task_contract']['path']).read_bytes())
                    self.assertEqual(len(manifest['retained_inputs']),1)
                    self.assertEqual(manifest['retained_inputs'][0]['receipt_sha256'],sha256(first))
                    with DevelopmentSession(development_session_binding(root)).lease(config,time.monotonic()+2) as owner:
                        self.assertEqual(len(owner.state['calls']),2);owner.verify_history()
                    return
                self.assertEqual(len(requests),4 if failure else 3)
                if failure:
                    self.assertEqual(execute.call_count,2)
                    self.assertIn('offline controlled tool failure',json.dumps(requests[1]['messages']))
                    self.assertIn('corrected-check',json.dumps(requests[2]['messages']))
                else:
                    self.assertTrue(record['observations'])
                    self.assertEqual(record['observations'][0]['outcome'],'ok')
                self.assertEqual(json.loads(result['files']['response.json'])['note'],'Unadmitted local tool checkpoint')
                if run_program:
                    self.assertEqual(record['observations'][0]['result']['output'],{'value':4})
                rp=Path(result['receipt']);receipt=json.loads(rp.read_bytes());self.assertTrue(receipt['process_reaped']);self.assertTrue(receipt['outcome_known'])
                _verify_receipt_files(rp,receipt)
                names={t['function']['name'] for t in requests[0]['tools']};self.assertIn('research_tool',names);self.assertNotIn('bash',names)
                signature=next(t['function']['parameters'] for t in requests[0]['tools'] if t['function']['name']=='research_run')
                self.assertEqual(set(signature['required']),{'source'})
                self.assertIn('JSON object',signature['properties']['source']['description'])
                self.assertIn('receipt_ref',json.dumps(requests[2 if failure else 1]['messages']))
        finally:server.shutdown();server.server_close();thread.join()
    def test_installed_native_controller_roundtrip(self):self.exercise()
    def test_requested_isolated_review_uses_native_tools_without_author_or_schema(self):self.exercise(review=True)
    def test_known_tool_error_is_feedback_and_same_session_corrects_it(self):self.exercise(failure=True)
    def test_same_primary_restores_archives_and_reads_full_original_in_real_sandbox(self):self.exercise(resume=True)
    @unittest.skipUnless(os.environ.get('SCISAURUS_LAB_CONFIG') and os.environ.get('SCISAURUS_LAB_ATTESTATION'),
        'explicit attested laboratory configuration required')
    def test_native_computation_executes_in_attested_runtime(self):self.exercise(run_program=True)
