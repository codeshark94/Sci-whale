"""Offline native compaction composition, lifecycle and billing contracts."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scisaurus.core.errors import StateError
from scisaurus.runtime.dsh_context import context_composition, context_entries
from scisaurus.tests.context_fixtures import context_backend


class ContextCompositionTests(unittest.TestCase):
    def test_installed_native_engine_compacts_preserves_history_and_stops_after_failed_summary(self):
        root=Path('/Users/seungyeop/workspace/dsh/packages')
        if not (root/'compaction/compaction-basic/lib/index.js').exists():
            self.skipTest('installed native compaction runtime unavailable')
        node=shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node'
        script=Path(__file__).with_name('dsh_context_runtime.mjs')
        policy=Path(__file__).resolve().parents[2]/'scripts/dsh/context-policy.mjs'
        result=subprocess.run([node,str(script),str(root),str(policy)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        proof=json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(proof['native_success']['compacted'])
        self.assertTrue(proof['native_success']['original_prefix_preserved'])
        self.assertEqual(len(proof['native_failure']['calls']),1)
        self.assertEqual(proof['live_provider_calls'],0)

    def test_missing_services_are_repaired_without_changing_the_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = context_backend(directory, {'pinned_files': {}})
            original = [{'id': 'llm', 'name': 'fixed-provider', 'config': {'model': 'owned'}},
                        {'id': 'sessions', 'name': 'durable'}]
            result = context_composition(backend, original)
            self.assertEqual(result[:2], original)
            self.assertEqual(original, result[:2])
            self.assertEqual(context_composition(backend, result), result)
            compaction = result[-1]['config']
            self.assertEqual(compaction, {'auto': False, 'maxTokens': 32768,
                                         'compactionRetries': 0, 'maxOverflowRetries': 0})

    def test_missing_or_changed_runtime_is_never_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = context_backend(directory, {'pinned_files': {}})
            entry = Path(directory) / 'context-packages/token-meter/index.js'
            entry.write_text('changed')
            with self.assertRaisesRegex(StateError, 'entry changed'):
                context_composition(backend, [])
            with self.assertRaisesRegex(StateError, 'missing or ambiguous'):
                context_composition({'pinned_files': {}, 'max_output_tokens': 32768}, [])

    def test_generation_capacity_is_preserved(self):
        self.assertEqual(context_entries(65536)[-1]['config']['maxTokens'], 65536)
        with self.assertRaises(StateError):
            context_entries(0)

    def test_distinct_package_entries_are_rejected_even_with_identical_bytes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            backend = context_backend(directory, {'pinned_files': {}})
            package = Path(directory) / 'context-packages/token-meter'
            duplicate = Path(directory) / 'duplicate'
            duplicate.mkdir()
            for name in ('package.json', 'index.js'):
                raw = (package / name).read_bytes()
                path = duplicate / name
                path.write_bytes(raw)
                backend['pinned_files'][str(path.resolve())] = hashlib.sha256(raw).hexdigest()
            with self.assertRaisesRegex(StateError, 'missing or ambiguous'):
                context_composition(backend, [{'id': 'custom-meter', 'name': str(duplicate / 'index.js')}])

    def test_policy_runs_native_compaction_before_dispatch_and_stops_on_failure(self):
        node = shutil.which('node') or '/Users/seungyeop/.hermes/node/bin/node'
        plugin = (Path(__file__).resolve().parents[2] / 'scripts/dsh/context-policy.mjs').as_uri()
        code = r'''
import assert from 'node:assert/strict';
const {apply, assertRequestCapacity}=await import(PLUGIN);
const handlers=new Map(), log=[];
const session={id:'same-owner',events:[{type:'compaction/start',data:{compactionId:'c1'}}],
 requestHeader:()=>undefined,append:(type,data)=>log.push({type,data})};
const ctx={on:(name,f)=>handlers.set(name,f),get:()=>undefined,sessions:{get:()=>session},
 llm:{resolveModelInfo:async()=>({context:{contextWindow:1000}})},
 tokenMeter:{estimateMessage:()=>100,estimateHeader:()=>0,measure:()=>({totalTokens:900,surfaceTokens:900})},
 compaction:{config:{thresholdRatio:.8},compactIfNeeded:async()=>{log.push('native-compaction');ctx.tokenMeter.measure=()=>({totalTokens:100,surfaceTokens:100});return {};}}};
apply(ctx,{maxOutputTokens:100});
const e={agent:{session,options:{provider:'fixed',model:'owned'}},signal:new AbortController().signal,turn:2,step:3};
await handlers.get('agent/pre-step')(e,async()=>{log.push('project-inputs');return {kind:'enter',messages:[]};});
assert.deepEqual(log.slice(0,2),['project-inputs','native-compaction']);
const options={sessionId:session.id,provider:'fixed',model:'owned',messages:[{}],maxTokens:100};
let dispatch=0;
for await(const chunk of handlers.get('llm/stream')(options,async function*(){dispatch++;yield {type:'text-delta',text:'ok'};})){}
assert.equal(dispatch,1);
ctx.tokenMeter.measure=()=>({totalTokens:900,surfaceTokens:900});
ctx.compaction.compactIfNeeded=async()=>{throw new Error('weekly usage limit');};
await assert.rejects(()=>handlers.get('agent/pre-step')(e,async()=>({kind:'enter',messages:[]})),/weekly/);
assert.equal(dispatch,1);
ctx.tokenMeter.measure=()=>({totalTokens:950,surfaceTokens:950});
await assert.rejects(()=>assertRequestCapacity(ctx,session,options,100),/950 input \+ 100 output/);
const summary={...options,purpose:'compaction'};
await assertRequestCapacity(ctx,session,summary,100);
const stream=handlers.get('llm/stream')(summary,async function*(){dispatch++;yield {type:'usage',usage:{inputTokens:10,outputTokens:3}};yield {type:'finish',reason:{kind:'error',failure:{code:'RATE_LIMIT',message:'weekly usage limit'}}};});
for await(const _ of stream){}
const records=log.filter(x=>x.type==='scisaurus/compaction-call');
assert.deepEqual(records.map(x=>x.data.phase),['start','chunk','chunk','end']);
assert.ok(records.every(x=>x.data.compactionId==='c1'&&x.data.turn===2));
console.log('native-context-policy-pass');
'''.replace('PLUGIN', json.dumps(plugin))
        result = subprocess.run([node, '--input-type=module', '-e', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('native-context-policy-pass', result.stdout)
