/** Require current input delivery before work and exact response validation before publication. */
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { execFileSync } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';

export const name = 'intake-contract';
export const inject = ['tools'];
export const marker = 'SCISAURUS_INTAKE_CONTRACT_READY:read-before-work-exact-publication-1';
const hash = value => createHash('sha256').update(value).digest('hex');
const interfaceSpec = JSON.parse(readFileSync(new URL('./research-tools.json', import.meta.url), 'utf8'));

export function nativeTools(mode) {
  return [...interfaceSpec[mode === 'research_work' ? 'work_tools' : 'intake_tools']];
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
    const missing = pending(exec.agent);
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
    if (['read', 'read_image', 'inspect_workspace'].includes(exec.name)) return;
    if (['research_tool','research_run'].includes(exec.name) || exec.name === (manifest.mode==='research_work' ? 'review_response' : 'validate_response')) return;
    if (manifest.mode === 'research_work' && ['write', 'edit'].includes(exec.name)) {
      if (!ownedFile(exec.arguments.file_path)) return 'Research files must remain in the owning workspace';
      if (resolve(manifest.workspace, exec.arguments.file_path ?? '') !== output) return;
      if (exec.name === 'edit') return 'Publish response.json as a complete document with write';
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
  if (manifest.mode === 'research_work') {
    const assignment = JSON.parse(readFileSync(manifest.assignment, 'utf8'));
    function registerController(name, parameters, actionFor, description) {
      ctx.tools.register({ name, description, parameters,
      output: { schema: { type: 'object', additionalProperties: true },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute(args, exec) {
        const action = actionFor(args);
        if (name === 'research_tool' && action.operation === 'run') throw new Error('Use research_run with its explicit computation fields');
        check(undefined, action);
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
            if (reply.error) throw new Error(reply.error);
            if (reply.observation?.outcome !== 'ok')
              throw new Error('Controller tool failed or is unknown: ' + JSON.stringify(reply.observation));
            return reply.observation;
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
      'Execute complete source in exactly one declared runtime or acquired environment. Mandatory source/input/purpose/expected fields and JSON stdout format are specified in this tool schema. Supply input={} and expected=null when appropriate. Returns the full actual receipt; execution is not scientific validation.');
  }
  ctx.tools.register({ name: 'inspect_workspace',
    description: 'List the current writable workspace and check whether response.json exists. An absent response is normal before publication; this does not read file contents.',
    parameters: {},
    output: { schema: { type: 'object', additionalProperties: false, required: ['workspace', 'entries', 'response_exists'],
      properties: { workspace: { type: 'string' }, entries: { type: 'array', items: { type: 'string' } }, response_exists: { type: 'boolean' } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: () => ({ workspace: manifest.workspace, entries: readdirSync(manifest.workspace).sort(), response_exists: existsSync(manifest.output) }),
  });
  ctx.tools.register({ name: manifest.mode==='research_work' ? 'review_response' : 'validate_response',
    description: manifest.mode==='research_work'
      ? 'Check the publication and tool interface only. Research content is unadmitted; independent scientific review is separate.'
      : 'Validate response.json against the exact sealed assignment using the authoritative controller checker. No scientific validation or admission.',
    parameters: {},
    output: { schema: { type: 'object', additionalProperties: false, required: ['validated','sha256'],
      properties: { validated: { type: 'boolean' }, sha256: { type: 'string' } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: () => check(readFileSync(manifest.output, 'utf8')),
  });
  process.stderr.write(marker + '\n');
}
