/** Native research tools and immutable input ownership; legacy publication is explicit. */
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

export const name = 'intake-contract';
export const inject = ['tools'];
export const marker = 'SCISAURUS_NATIVE_RESEARCH_READY:tools-feedback-checkpoints-1';
const hash = value => createHash('sha256').update(value).digest('hex');
const interfaceSpec = JSON.parse(readFileSync(new URL('./research-tools.json', import.meta.url), 'utf8'));

export function nativeTools(mode) {
  if(mode==='research_review')return [...interfaceSpec.review_tools];
  return [...interfaceSpec[mode === 'research_work' ? 'work_tools' : 'intake_tools']];
}

export function projectNativeToolResult(text, callId, records = []) {
  for (const item of [...records].reverse()) {
    if (item.call_id !== callId) continue;
    const raw = readFileSync(item.path);
    if (hash(raw) !== item.sha256) throw new Error('Retained tool result changed');
    const reply = JSON.parse(raw);
    if (reply.callId !== callId) throw new Error('Retained tool result lost its call owner');
    const value = reply.observation ?? {outcome:'failed',error:reply.error};
    if (JSON.stringify(value) !== text) continue;
    return '<retained-tool-result>\n'+JSON.stringify({path:item.path,sha256:item.sha256,
      receipt_sha256:item.receipt_sha256,call_id:callId,outcome:value.outcome,
      retrieval:'Use read_retained_input with path and sha256 for the complete original reply.'})+
      '\n</retained-tool-result>';
  }
  return text;
}

export function projectSessionInputs(session, project, inspected = new Set(), currentMessages = new Set(), historicalNodes) {
  const reads = new Set(session.events.filter(e => e.type === 'tool/call' && ['read', 'read_retained_input'].includes(e.data.name)).map(e => e.data.callId));
  const native = new Set(session.events.filter(e => e.type === 'tool/call' && ['research_tool','research_run'].includes(e.data.name)).map(e => e.data.callId));
  for (const seq of [...session.surface.nodes]) {
    if (historicalNodes && !historicalNodes.has(seq)) continue;
    const event = session.events[seq];
    if (event.type !== 'user/message' && event.type !== 'tool/result') continue;
    if (inspected.has(seq)) continue;
    if (event.type === 'user/message' && currentMessages.has(event.data.id)) { inspected.add(seq); continue; }
    const callId = event.type === 'tool/result' ? event.data.message.source.callId : undefined;
    if (event.type === 'tool/result' && !reads.has(callId) && !native.has(callId)) continue;
    let changed = false;
    const message = event.type === 'user/message' ? event.data : event.data.message;
    const projectBlock = (block, kind) => {
      if (block.type !== 'text' || (kind === 'inputs' && !block.text.includes('<current-inputs>'))) return block;
      const text = project(block.text, kind, callId);
      if (text === block.text) return block;
      changed = true;
      return { ...block, text };
    };
    const content = message.content.map(block => event.type === 'user/message' ? projectBlock(block, 'inputs') :
      (block.type === 'tool-result' && !block.isError ? { ...block, content: block.content.map(b => projectBlock(b, reads.has(callId) ? 'read' : 'tool')) } : block));
    if (changed) {
      const data = event.type === 'user/message' ? { ...event.data, content } : { ...event.data, message: { ...message, content } };
      const replacement = session.append(event.type, data, {
      surfaceOp: { op: 'replace', start: seq, end: seq }, sourceEventSeqs: [seq],
      });
      inspected.add(replacement.seq);
    }
    inspected.add(seq);
  }
}

export function createPolicy(manifest, check) {
  const inputs = new Map(Object.entries(manifest.inputs).map(([path, digest]) => {
    const raw = readFileSync(path);
    if (hash(raw) !== digest) throw new Error('sealed input changed: ' + path);
    const lines = raw.toString('utf8').split(/\r?\n/);
    if (lines.at(-1) === '') lines.pop();
    return [realpathSync(path), { digest, lines }];
  }));
  const output = resolve(manifest.output);
  const workspace = realpathSync(manifest.workspace);
  function ownedFile(path) {
    let ancestor = resolve(manifest.workspace, path ?? '');
    while (true) {
      try { lstatSync(ancestor); break; } catch (error) {
        if (error.code !== 'ENOENT') return false;
      }
      const parent = dirname(ancestor);
      if (parent === ancestor) return false;
      ancestor = parent;
    }
    let actual;
    try { actual = realpathSync(ancestor); } catch { return false; }
    if (actual !== ancestor) return false;
    const target = relative(workspace, actual);
    const lexical = relative(workspace, resolve(manifest.workspace, path ?? ''));
    return [target, lexical].every(value => value !== '..' && !value.startsWith('..' + sep) && !value.startsWith(sep));
  }
  const owners = new WeakMap();
  function state(agent) {
    if (!agent) throw new Error('intake tool requires its owning agent');
    if (!owners.has(agent)) owners.set(agent, new Map([...inputs].map(([p,input]) =>
      [p,new Set(manifest.input_delivery==='inline' ? input.lines.map((_,i)=>i+1) : [])])));
    return owners.get(agent);
  }
  function pending(agent) {
    const coverage = state(agent);
    return [...inputs].filter(([path, input]) => coverage.get(path).size !== input.lines.length)
      .map(([path, input]) => ({ path, missing: input.lines.map((_, i) => i + 1).filter(i => !coverage.get(path).has(i)) }));
  }
  function guard(exec) {
    const missing = ['research_work','research_review'].includes(manifest.mode) ? [] : pending(exec.agent);
    if (missing.length) {
      let path;
      try { path = realpathSync(resolve(manifest.workspace, exec.arguments.file_path ?? '')); } catch { /* No owned target. */ }
      const first = manifest.first_input && pending(exec.agent).find(x => x.path === manifest.first_input);
      if (exec.name !== 'read' || !inputs.has(path) || first && path !== manifest.first_input)
        return 'Read every current input line before work: ' + JSON.stringify(missing);
      if (hash(readFileSync(path)) !== inputs.get(path).digest) return 'sealed input changed: ' + path;
      return;
    }
    if (!nativeTools(manifest.mode).includes(exec.name))
      return 'Tool is outside the declared native interface: ' + exec.name;
    if (['read', 'read_retained_input', 'read_image', 'inspect_workspace'].includes(exec.name)) return;
    if (['research_tool','research_run'].includes(exec.name) || exec.name === (manifest.mode==='research_work' ? 'review_response' : 'validate_response')) return;
    if (manifest.mode === 'research_work' && ['write', 'edit'].includes(exec.name)) {
      if (!ownedFile(exec.arguments.file_path)) return 'Research files must remain in the owning workspace';
      return;
    }
    if (exec.name !== 'write' || resolve(manifest.workspace, exec.arguments.file_path ?? '') !== output)
      return 'Intake publication requires a whole response.json write';
    try { check(exec.arguments.content); } catch (error) { return 'Exact response contract rejected publication: ' + error.message; }
  }
  function observe(exec, result) {
    if (exec.name !== 'read' || result.isError) return;
    let path;
    try { path = realpathSync(resolve(manifest.workspace, exec.arguments.file_path)); } catch { return; }
    const input = inputs.get(path);
    if (!input) return;
    const coverage = state(exec.agent).get(path);
    for (const item of result.content ?? []) {
      if (item.type !== 'text' || !item.text.includes('<path>' + path + '</path>')) continue;
      for (const match of item.text.matchAll(/^([0-9]+): (.*)$/gm)) {
        const number = Number(match[1]);
        if (number >= 1 && number <= input.lines.length && match[2] === input.lines[number - 1]) coverage.add(number);
      }
    }
  }
  return { guard, observe, pending };
}

export function apply(ctx, config) {
  const manifest = JSON.parse(readFileSync(process.env.SCISAURUS_TASK_CONTRACT, 'utf8'));
  function check(document, action) {
    try {
      return JSON.parse(execFileSync(config.python, [config.checker, config.bundle], {
        input: JSON.stringify({ assignment: manifest.assignment,
          assignment_sha256: manifest.inputs[manifest.assignment], ...(action ? { action } : { document }) }),
        encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024,
      }));
    } catch (error) {
      throw new Error(error.stderr?.toString().trim() || error.message);
    }
  }
  const policy = createPolicy(manifest, check);
  ctx.tools.guard(exec => policy.guard(exec));
  ctx.on('tools/result', (exec, result) => policy.observe(exec, result));
  ctx.on('agent/created', ({ agent }) => {
    agent.ctx.tools.restrict({ allow: nativeTools(manifest.mode) });
  });
  if (['research_work','research_review'].includes(manifest.mode)) {
    if (manifest.mode === 'research_work') ctx.tools.register({ name: 'read_retained_input',
      description: 'Read exact Unicode text pages from a retained immutable input or native reply. No line truncation. Use the path and sha256 in the archive reference; continue at end until total_chars.',
      parameters: { type: 'object', additionalProperties: false, required: ['path','sha256'], properties: {
        path: {type:'string'}, sha256: {type:'string'}, start: {type:'integer',minimum:0}, max_chars: {type:'integer',minimum:1} } },
      output: { schema: { type:'object', additionalProperties:true },
        render: (_args, value) => [{type:'text',text:'<path>'+value.path+'</path>\n'+JSON.stringify(value)}] },
      execute(args) {
        const retained=[...(manifest.retained_inputs ?? []).flatMap(packet => Object.values(packet.inputs)),
          ...(manifest.retained_tool_results ?? [])];
        const item=retained.find(input => input.path === args.path && input.sha256 === args.sha256);
        if (!item) throw new Error('Input is outside this primary\'s retained archive catalog');
        const raw=readFileSync(item.path);
        if (hash(raw) !== item.sha256) throw new Error('Retained input changed');
        const points=Array.from(new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(raw));
        const start=args.start ?? 0, length=args.max_chars ?? 32000;
        if (!Number.isSafeInteger(start) || start < 0 || !Number.isSafeInteger(length) || length < 1)
          throw new Error('Retained input offsets must be nonnegative integers and page length positive');
        const end=Math.min(points.length,start+length);
        if (start > points.length) throw new Error('Retained input offset exceeds its length');
        return {path:item.path,sha256:item.sha256,start,end,total_chars:points.length,content:points.slice(start,end).join('')};
      },
    });
    const inspected = new Set();
    const currentMessages = new Set();
    let historicalNodes;
    ctx.on('agent/pre-step', async ({ agent, messages }, next) => {
      historicalNodes ??= new Set(agent.session.surface.nodes);
      for (const message of messages ?? []) currentMessages.add(message.id);
      projectSessionInputs(agent.session, (text, kind, callId) => kind === 'tool' ?
        projectNativeToolResult(text,callId,manifest.retained_tool_results) : JSON.parse(execFileSync(config.python,
        [config.checker, config.bundle], {
          input: JSON.stringify({ [kind === 'read' ? 'project_research_read' : 'project_research_inputs']: text,
            retained_inputs: manifest.retained_inputs }), encoding: 'utf8',
          maxBuffer: Math.max(Buffer.byteLength(text) * 2, 1024 * 1024),
        })).text, inspected, currentMessages, historicalNodes);
      return next();
    });
    const assignment = JSON.parse(readFileSync(manifest.assignment, 'utf8'));
    function registerController(name, parameters, actionFor, description) {
      ctx.tools.register({ name, description, parameters,
      output: { schema: { type: 'object', additionalProperties: true },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute(args, exec) {
        const action = actionFor(args);
        if (name === 'research_tool' && action.operation === 'run') throw new Error('Use research_run with its explicit computation fields');
        const bridge = manifest.controller_bridge;
        if (!bridge) throw new Error('Native controller tool has no owning execution channel');
        const callId = String(exec.callId), sessionId = String(exec.agent.id);
        const target = resolve(bridge.directory, hash(callId) + '.json');
        await delay(0, undefined, { signal: exec.signal });
        process.stdout.write(JSON.stringify({ jsonrpc: '2.0', method: 'scisaurus/research-tool',
          params: { channel: bridge.channel, sessionId, callId, action } }) + '\n');
        while (Date.now() < bridge.deadline_ms) {
          if (existsSync(target)) {
            const reply = JSON.parse(readFileSync(target, 'utf8'));
            if (reply.channel !== bridge.channel || reply.sessionId !== sessionId || reply.callId !== callId)
              throw new Error('Native controller result lost its exact session/call owner');
            if (reply.terminal) exec.agent.cancel({kind:'parent'});
            return reply.observation ?? {outcome:'failed',error:reply.error};
          }
          await delay(50, undefined, { signal: exec.signal });
        }
        throw new Error('Native controller tool reached the owning attempt deadline');
      },
      });
    }
    registerController('research_tool', { type: 'object', additionalProperties: false, required: ['operation', 'arguments'],
      properties: { operation: { type: 'string', enum: Object.keys(assignment.scientific_software_tools.actions).filter(x => x !== 'run') },
        arguments: { type: 'object', additionalProperties: true } } }, args => args, interfaceSpec.controller_description);
    registerController('research_run', assignment.scientific_software_tools.native_arguments_schema.run,
      args => ({ operation: 'run', arguments: args }),
      'Execute source in a declared runtime or acquired environment. Defaults are input={}, purpose=scientific_computation, expected=null. Raw stdout, stderr and files are returned; JSON output is required only for a requested expected-value comparison. Execution is not scientific validation.');
  }
  ctx.tools.register({ name: 'inspect_workspace',
    description: 'List the current writable workspace and check whether the checkpoint notes file exists. An absent file is normal before writing; this does not read file contents.',
    parameters: {},
    output: { schema: { type: 'object', additionalProperties: false, required: ['workspace', 'entries', 'response_exists'],
      properties: { workspace: { type: 'string' }, entries: { type: 'array', items: { type: 'string' } }, response_exists: { type: 'boolean' } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: () => ({ workspace: manifest.workspace, entries: readdirSync(manifest.workspace).sort(), response_exists: existsSync(manifest.output) }),
  });
  if (manifest.mode !== 'research_review') ctx.tools.register({ name: manifest.mode==='research_work' ? 'review_response' : 'validate_response',
    description: manifest.mode==='research_work'
      ? 'Report automatic checkpoint recording and optional notes-file identity. No file or output format is required. Scientific review is separate.'
      : 'Validate response.json against the exact sealed assignment using the authoritative controller checker. No scientific validation or admission.',
    parameters: {},
    output: { schema: manifest.mode==='research_work' ? {
      type:'object',additionalProperties:false,required:['recording','notes_file_present','sha256','scientific_validation'],
      properties:{recording:{type:'string'},notes_file_present:{type:'boolean'},sha256:{oneOf:[{type:'string'},{type:'null'}]},scientific_validation:{type:'string'}}
      } : { type: 'object', additionalProperties: false, required: ['validated','sha256'],
      properties: { validated: { type: 'boolean' }, sha256: { type: 'string' } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: () => {
      if (manifest.mode !== 'research_work') return check(readFileSync(manifest.output, 'utf8'));
      let raw;
      try { raw=readFileSync(manifest.output); } catch (error) { if(error.code!=='ENOENT')throw error; }
      return {recording:'automatic',notes_file_present:raw!==undefined,sha256:raw===undefined?null:hash(raw),scientific_validation:'not_evaluated'};
    },
  });
  process.stderr.write(marker + '\n');
}
