"""Owned archive retrieval replaces duplication, not scientific evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import shutil
import sys

from scisaurus.core.errors import StateError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.inline_inputs import inline_inputs
from scisaurus.runtime.research_context import input_archive,input_identity,project_inline_research_inputs,research_context_projection,project_archived_input_read


class ResearchContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.assignment={'research_work':{'checkpoint':{'observations':[{'source':'x = 1\n','raw':[0,1,None]}],
            'review_dispatches':[{'backend':{'pinned_files':{'pin':'abc'}},'feedback':'unresolved','usage':{'model_calls':3}}]}},
            'original_question':'Unresolved mechanism','coefficients':{'E':1e9,'nu':.3}}
        self.files={'assignment.json':json.dumps(self.assignment,indent=2),'system-contract.txt':'Retain scientific failures.'}
        self.packet=self.archive('old',self.files,'a'*64)
    def tearDown(self):self.temp.cleanup()
    def archive(self,name,files,receipt):
        directory=self.root/name;directory.mkdir();hashes={}
        for file,text in files.items():
            raw=text.encode();(directory/file).write_bytes(raw);hashes[file]=hashlib.sha256(raw).hexdigest()
        return input_archive(directory,hashes,receipt_sha256=receipt)

    def test_historical_packets_reference_complete_originals_and_current_is_exact(self):
        current_files={**self.files,'assignment.json':json.dumps({**self.assignment,'feedback':'current'})}
        current=self.archive('current',current_files,'b'*64)
        original=deepcopy(self.assignment);text='history'+inline_inputs(self.files)+'latest'+inline_inputs(current_files)
        actual='history'+project_inline_research_inputs(inline_inputs(self.files),retained_inputs=[self.packet])+'latest'+inline_inputs(current_files)
        self.assertIn('<retained-inputs>',actual)
        self.assertIn(inline_inputs(current_files),actual)
        self.assertEqual(actual.count('<current-inputs>'),1)
        refs=json.loads(actual.split('<retained-inputs>\n')[1].split('\n</retained-inputs>')[0])
        self.assertEqual(refs['receipt_sha256'],'a'*64)
        for name,item in refs['inputs'].items():
            raw=Path(item['path']).read_bytes();self.assertEqual(raw.decode(),self.files[name])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
        self.assertEqual(json.loads(Path(refs['inputs']['assignment.json']['path']).read_bytes()),original)
        self.assertEqual(project_inline_research_inputs(actual.split('latest')[0],retained_inputs=[self.packet]),actual.split('latest')[0])

    def test_equal_historical_packets_both_reference_original(self):
        text=inline_inputs(self.files)*2
        self.assertEqual(project_inline_research_inputs(text,retained_inputs=[self.packet]).count('<retained-inputs>'),2)

    def test_prior_transport_projection_and_json_formatting_resolve_original_archive(self):
        projected={**self.files,'assignment.json':json.dumps(research_context_projection(self.assignment))}
        result=project_inline_research_inputs(inline_inputs(projected),retained_inputs=[self.packet])
        self.assertIn('<retained-inputs>',result)
        self.assertIn(self.packet['inputs']['assignment.json']['sha256'],result)

    def test_changed_science_or_unregistered_content_is_not_archived(self):
        changed=deepcopy(self.assignment);changed['coefficients']['E']=2e9
        files={**self.files,'assignment.json':json.dumps(changed)}
        result=project_inline_research_inputs(inline_inputs(files),retained_inputs=[self.packet])
        self.assertNotIn('<retained-inputs>',result)
        self.assertIn('2000000000.0',result)
        self.assertEqual(project_inline_research_inputs('plain research text',retained_inputs=[self.packet]),'plain research text')

    def test_archive_tampering_is_visible_without_a_replacement(self):
        Path(self.packet['inputs']['assignment.json']['path']).write_text('{}')
        with self.assertRaisesRegex(StateError,'changed during context'):
            project_inline_research_inputs(inline_inputs(self.files),retained_inputs=[self.packet])

    def test_archive_rejects_escape_and_hash_changes(self):
        with self.assertRaises(StateError):input_archive(self.root/'old',{'../other.json':'a'*64})
        with self.assertRaises(StateError):input_archive(self.root/'old',{'assignment.json':'a'*64})

    def test_opaque_legacy_assignment_is_preserved_in_archive(self):
        for i,text in enumerate(['null','[1,2]','{"old_raw":NaN}','not JSON',
                                '{"research_work": null}','{"research_work": []}',
                                '{"research_work":{"checkpoint":null}}',
                                '{"research_work":{"checkpoint":{"review_dispatches":null}}}',
                                '{"research_work":{"checkpoint":{"review_dispatches":[null]}}}']):
            archive=self.archive('opaque-'+str(i),{'assignment.json':text},'c'*64)
            self.assertEqual(Path(archive['inputs']['assignment.json']['path']).read_text(),text)
            self.assertEqual(input_identity('assignment.json',text.encode()),hashlib.sha256(text.encode()).hexdigest())

    def test_owned_input_reads_are_referenced_and_other_observations_are_unchanged(self):
        path=self.packet['inputs']['assignment.json']['path'];text='<path>'+path+'</path>\n<content>1: original</content>'
        actual=project_archived_input_read(text,retained_inputs=[self.packet]);self.assertIn('<retained-input>',actual)
        for text in ('ordinary numerical stdout','<path>/unregistered/solver.py</path>'):
            self.assertEqual(project_archived_input_read(text,retained_inputs=[self.packet]),text)

    def test_installed_surface_keeps_current_event_even_with_identical_content(self):
        module=Path('/Users/seungyeop/workspace/dsh/packages/core/session/lib/index.js')
        if not module.is_file():self.skipTest('installed DSH session unavailable')
        root=Path(__file__).resolve().parents[2]
        code=r'''
import assert from 'node:assert/strict';import {execFileSync} from 'node:child_process';
const {Session,SessionId}=await import(MODULE);const {projectSessionInputs}=await import(PLUGIN);
const s=Session.create(SessionId('owned-context'));
const text=TEXT, packet=PACKET;
const message=id=>({id,role:'user',source:{kind:'user'},content:[{type:'text',text}]});
s.append('user/message',message('old'),{surfaceOp:'append'});s.append('user/message',message('current'),{surfaceOp:'append'});
s.append('tool/call',{turn:1,step:1,callId:'old-read',name:'read',arguments:JSON.stringify({file_path:packet.inputs['assignment.json'].path})});
const read={id:'read-result',role:'user',source:{kind:'tool',callId:'old-read'},content:[{type:'tool-result',toolCallId:'old-read',isError:false,content:[{type:'text',text:'<path>'+packet.inputs['assignment.json'].path+'</path>\n1: original'}]}]};
s.append('tool/result',{turn:1,step:1,message:read},{surfaceOp:'append'});
const prefix=s.events.length,original=JSON.stringify(s.events);
const project=(text,kind)=>JSON.parse(execFileSync(PYTHON,[CHECKER,BUNDLE],{input:JSON.stringify({[kind==='read'?'project_research_read':'project_research_inputs']:text,retained_inputs:[packet]}),encoding:'utf8'})).text;
const historical=new Set(s.surface.nodes),inspected=new Set();projectSessionInputs(s,project,inspected,new Set(['current']),historical);
assert.equal(JSON.stringify(s.events.slice(0,prefix)),original);
const derived=s.deriveMessages();assert.match(derived[0].content[0].text,/<retained-inputs>/);
assert.equal(derived[1].content[0].text,text);assert.match(derived[2].content[0].content[0].text,/<retained-input>/);
const count=s.events.length;projectSessionInputs(s,()=>{throw new Error('duplicate work')},inspected,new Set(['current']),historical);assert.equal(s.events.length,count);
// A newly requested read must expose its contents through the active dispatch.
s.append('tool/call',{turn:2,step:1,callId:'fresh-read',name:'read',arguments:JSON.stringify({file_path:packet.inputs['assignment.json'].path})});
const fresh={...read,id:'fresh-result',source:{kind:'tool',callId:'fresh-read'},content:[{...read.content[0],toolCallId:'fresh-read'}]};
s.append('tool/result',{turn:2,step:1,message:fresh},{surfaceOp:'append'});
projectSessionInputs(s,project,inspected,new Set(['current']),historical);
assert.deepEqual(s.deriveMessages().at(-1),fresh);
const restored=Session.create(SessionId('owned-context'),[...s.events]);assert.deepEqual(restored.deriveMessages(),s.deriveMessages());
'''
        values={'MODULE':module.as_uri(),'PLUGIN':(root/'scripts/dsh/intake-contract.mjs').as_uri(),
            'TEXT':inline_inputs(self.files),'PACKET':self.packet,'PYTHON':sys.executable,
            'CHECKER':str(root/'scripts/dsh/check-intake-response.py'),'BUNDLE':str(root)}
        for key,value in values.items():code=code.replace(key,json.dumps(value))
        result=subprocess.run([shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node',
            '--input-type=module','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_retained_reader_preserves_long_unicode_lines_and_checks_hash(self):
        root=Path(__file__).resolve().parents[2];text='\ufefforiginal\r\n'+('🧪σ long original line '*3000)
        packet=self.archive('unicode',{'note.txt':text},'d'*64)
        code=r'''
import assert from 'node:assert/strict';import {writeFileSync,readFileSync} from 'node:fs';
const {apply}=await import(PLUGIN);const packet=PACKET,root=ROOT;
const assignment=ASSIGNMENT;writeFileSync(root+'/assignment.json',assignment);writeFileSync(root+'/manifest.json',JSON.stringify({mode:'research_work',input_delivery:'inline',workspace:root,assignment:root+'/assignment.json',output:root+'/notes.md',inputs:{[root+'/assignment.json']:HASH},retained_inputs:[packet]}));
process.env.SCISAURUS_TASK_CONTRACT=root+'/manifest.json';const defs=new Map();
apply({tools:{guard(){},register(def){defs.set(def.name,def)}},on(){}},{});
const tool=defs.get('read_retained_input'),item=packet.inputs['note.txt'];let start=0,joined='';
do {const v=tool.execute({...item,start,max_chars:10001});joined+=v.content;start=v.end;if(start===v.total_chars)break;} while(true);
assert.equal(joined,readFileSync(item.path,'utf8'));assert(joined.includes('🧪'));
assert.throws(()=>tool.execute({...item,sha256:'wrong'}),/outside/);
writeFileSync(item.path,'tampered');assert.throws(()=>tool.execute(item),/changed/);
'''
        from scisaurus.runtime.primary_work import work_assignment
        assignment=json.dumps(work_assignment({}, {'kind':'research_work','checkpoint':{}}))
        for key,value in {'PLUGIN':(root/'scripts/dsh/intake-contract.mjs').as_uri(),'PACKET':packet,
                'ROOT':str(self.root),'ASSIGNMENT':assignment,'HASH':hashlib.sha256(assignment.encode()).hexdigest()}.items():code=code.replace(key,json.dumps(value))
        result=subprocess.run([shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node',
            '--input-type=module','-e',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
