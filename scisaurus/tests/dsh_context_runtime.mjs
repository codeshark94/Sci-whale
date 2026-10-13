/** Exercise native compaction against an in-process deterministic adapter. */
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const [base, policyPath] = process.argv.slice(2);
const load = path => import(pathToFileURL(base + '/' + path).href);
const {Context} = await load('core/tools/node_modules/@deepseek-ai/cordis/lib/index.js');
const {default:LlmRuntime,LlmAdapter,createUserMessage,createMessage,createToolResultMessage,CallId} = await load('llm/llm/lib/index.js');
const {default:SessionStore,SessionId} = await load('core/session/lib/index.js');
const {default:SystemPrompt} = await load('core/system-prompt/lib/index.js');
const {default:Tools} = await load('core/tools/lib/index.js');
const {default:Agents} = await load('core/agent/lib/index.js');
const {default:Loop} = await load('core/agent-loop/lib/index.js');
const {default:Meter} = await load('llm/token-meter/lib/index.js');
const {default:Compaction} = await load('compaction/compaction-basic/lib/index.js');
const {default:Pruner} = await load('compaction/compaction-tool-result-pruner/lib/index.js');
const policy = await import(pathToFileURL(policyPath).href);

async function exercise(failed, ratio=.89, pendingText="Continue the same frozen assignment.", mode="summary") {
  const calls = [];
  class Adapter extends LlmAdapter {
    async resolveModel(provider,model) {return {provider,id:model,name:model,context:{contextWindow:131072}};}
    async *stream(options) {
      calls.push({purpose:options.purpose,provider:options.provider,model:options.model,maxTokens:options.maxTokens});
      yield {type:'usage',usage:{inputTokens:37,outputTokens:11,cacheReadTokens:13}};
      if (failed) {
        yield {type:'finish',reason:{kind:'error',failure:{code:'RATE_LIMIT',message:'weekly usage limit'}}};
        return;
      }
      yield {type:'block-start',index:0,blockType:'text'};
      yield {type:'block-end',index:0,block:{type:'text',text:options.purpose==='compaction'?'Saved checkpoint: preserve frozen inputs and source hashes.':'Continued from checkpoint.'}};
      yield {type:'finish',reason:{kind:'stop'}};
    }
  }
  const ctx = new Context();
  try {
    for (const plugin of [LlmRuntime,SessionStore,SystemPrompt,Tools,Agents]) await ctx.plugin(plugin);
    await ctx.plugin(Loop,{agents:[]});
    await ctx.plugin(Meter);
    await ctx.plugin(Pruner);
    await ctx.plugin(Compaction,{auto:false,maxTokens:32768,compactionRetries:0,maxOverflowRetries:0});
    await ctx.plugin(policy,{maxOutputTokens:32768});
    ctx.llm.registerAdapter(['offline'],new Adapter());
    const agent=ctx.agentLoop.create(SessionId(failed?'offline-failed':'offline-preserved'),
      {provider:'offline',model:'fixed',maxTokens:32768});
    agent.session.append('request/header',{header:{config:{provider:'offline',model:'fixed',maxTokens:32768}},reason:'initial'});
    while (ctx.tokenMeter.measure(agent.session).totalTokens<131072*ratio)
      agent.session.append('user/message',createUserMessage({content:[{type:'text',text:'immutable-evidence '.repeat(1200)}],source:{kind:'user'}}),{surfaceOp:'append'});
    if (mode==='prune') {
      agent.session.append('user/message',createUserMessage({content:[{type:'text',text:'Old assignment'}],source:{kind:'user'}}),{surfaceOp:'append'});
      const callId=CallId('huge-old-tool');
      agent.session.append('step/start',{turn:0,step:1});
      agent.session.append('assistant/message',{turn:0,step:1,message:createMessage({role:'assistant',content:[{type:'tool-call',id:callId,name:'bash',arguments:'{}'}],source:{kind:'model',provider:'offline',model:'fixed'}})},{surfaceOp:'append'});
      agent.session.append('tool/call',{turn:0,step:1,callId,name:'bash',arguments:'{}'});
      agent.session.append('tool/result',{turn:0,step:1,message:createToolResultMessage({callId,content:[{type:'text',text:'Huge evidence '.repeat(40000)}],isError:false})},{surfaceOp:'append'});
      agent.session.append('step/end',{turn:0,step:1});
    }
    const original=JSON.stringify(agent.session.events);
    const count=agent.session.events.length;
    const idle=new Promise(resolve=>{const off=ctx.on('agent/status',({agent:subject,status})=>{
      if(subject===agent&&status==='idle'){off();resolve();}
    });});
    agent.followup(createUserMessage({content:[{type:'text',text:pendingText}],source:{kind:'user'}}));
    await Promise.race([idle,new Promise((_,reject)=>setTimeout(()=>reject(new Error('offline loop timeout')),5000).unref())]);
    assert.equal(JSON.stringify(agent.session.events.slice(0,count)),original);
    assert.ok(calls.every(c=>c.provider==='offline'&&c.model==='fixed'&&c.maxTokens===32768));
    const records=agent.session.events.filter(e=>e.type==='scisaurus/compaction-call');
    if (mode==='prune') {
      assert.equal(calls.length,1);assert.notEqual(calls[0].purpose,'compaction');
      assert.ok(agent.session.events.some(e=>e.type==='compaction/prune'));
      assert.equal(agent.session.events.at(-1).data.reason.kind,'completed');
    } else if (mode==='oversized') {
      assert.equal(calls.length,0);assert.equal(agent.session.events.at(-1).data.reason.kind,'error');
    } else {
    assert.ok(records.some(e=>e.data.phase==='start'),JSON.stringify({calls,events:agent.session.events.slice(count)}));
    assert.ok(records.some(e=>e.data.chunk?.type==='usage'));
    if(failed) {
      assert.equal(calls.length,1);
      assert.equal(calls[0].purpose,'compaction');
      assert.ok(agent.session.events.some(e=>e.type==='compaction/end'&&e.data.error));
      assert.equal(agent.session.events.at(-1).data.reason.kind,'error');
    } else {
      assert.ok(calls.length>=2);
      assert.equal(calls.filter(c=>c.purpose!=='compaction').length,1);
      assert.equal(calls[0].purpose,'compaction');
      assert.ok(agent.session.events.some(e=>e.type==='compaction/summary'));
      assert.ok(agent.session.surface.nodes.length<count);
      assert.equal(agent.session.events.at(-1).data.reason.kind,'completed');
    }
    }
    return {failed,session_id:agent.session.id,original_events:count,original_prefix_preserved:true,
      compacted:agent.session.events.some(e=>e.type==='compaction/summary'),calls};
  } finally {await ctx.fiber.dispose();}
}
console.log(JSON.stringify({native_success:await exercise(false),native_failure:await exercise(true),low_reservation:await exercise(false,.78),high_pressure:await exercise(false,.958),pending_input:await exercise(false,.69,'Current immutable evidence '.repeat(2000)),prune_only:await exercise(false,0,'Continue.','prune'),oversized_input:await exercise(false,0,'oversized evidence '.repeat(40000),'oversized'),live_provider_calls:0}));
