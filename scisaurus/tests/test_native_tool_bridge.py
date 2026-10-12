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


@unittest.skipUnless(os.environ.get('SCISAURUS_DSH_CONFIG'),'explicit installed runtime configuration required')
class InstalledRuntimeTests(unittest.TestCase):
    def exercise(self,failure=False,run_program=False):
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
                if len(requests)==1:
                    name='research_tool';args={'operation':'check_environment','arguments':{}}
                    if run_program:args={'operation':'run','arguments':{'runtime':runtime_label,
                        'source':'import json\nprint(json.dumps({"value": 2 + 2}))\n','input':{},
                        'purpose':'scientific_computation','expected':None}}
                elif len(requests)==2:
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
                assignment=work_assignment({'objective':'Offline interface fixture'},{'kind':'research_work','checkpoint':{}})
                record={};bench=SoftwareWorkbench(root/'software',deadline=time.monotonic()+30,laboratory=laboratory)
                execute=native_tool_handler(assignment,bench,record)
                if failure:execute=Mock(side_effect=ValidationError('offline controlled tool failure'))
                runner=DshBatchRunner(config,root=root/'jobs',runtime_read_roots=[str(Path(sys.executable).absolute().parent.parent)],
                    required_startup_marker=GUARD_READY,forbid_reasoning=True)
                runner.task_contract={**guarded_backend(config),'mode':'research_work','input_delivery':'inline','native_controller_tools':True}
                runner.native_tool_handler=execute
                try:result=runner.run(work_instructions(),inputs={'assignment.json':json.dumps(assignment)},outputs=['response.json'])
                except DshBatchError as error:
                    if not failure:raise
                    self.assertEqual(len(requests),1);self.assertEqual(error.usage['model_calls'],1)
                    receipt=json.loads(Path(error.receipt).read_bytes());self.assertTrue(receipt['process_reaped'])
                    self.assertEqual(receipt['native_tool_calls'][0]['outcome'],'error');return
                self.assertFalse(failure);self.assertEqual(len(requests),3);self.assertTrue(record['observations'])
                self.assertEqual(record['observations'][0]['outcome'],'ok');self.assertEqual(json.loads(result['files']['response.json'])['note'],'Unadmitted local tool checkpoint')
                if run_program:
                    self.assertEqual(record['observations'][0]['result']['output'],{'value':4})
                rp=Path(result['receipt']);receipt=json.loads(rp.read_bytes());self.assertTrue(receipt['process_reaped']);self.assertTrue(receipt['outcome_known'])
                _verify_receipt_files(rp,receipt)
                names={t['function']['name'] for t in requests[0]['tools']};self.assertIn('research_tool',names);self.assertNotIn('bash',names)
                self.assertIn('receipt_ref',json.dumps(requests[1]['messages']))
        finally:server.shutdown();server.server_close();thread.join()
    def test_installed_native_controller_roundtrip(self):self.exercise()
    def test_failed_native_controller_stops_before_another_request(self):self.exercise(failure=True)
    @unittest.skipUnless(os.environ.get('SCISAURUS_LAB_CONFIG') and os.environ.get('SCISAURUS_LAB_ATTESTATION'),
        'explicit attested laboratory configuration required')
    def test_native_computation_executes_in_attested_runtime(self):self.exercise(run_program=True)
