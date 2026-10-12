/** Require current input delivery before work and exact response validation before publication. */
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

export const name = 'intake-contract';
export const inject = ['tools'];
export const marker = 'SCISAURUS_INTAKE_CONTRACT_READY:read-before-work-exact-publication-1';
const hash = value => createHash('sha256').update(value).digest('hex');

export function createPolicy(manifest, check) {
  const inputs = new Map(Object.entries(manifest.inputs).map(([path, digest]) => {
    const raw = readFileSync(path);
    if (hash(raw) !== digest) throw new Error('sealed input changed: ' + path);
    const lines = raw.toString('utf8').split(/\r?\n/);
    if (lines.at(-1) === '') lines.pop();
    return [realpathSync(path), { digest, lines }];
  }));
  const output = resolve(manifest.output);
  const owners = new WeakMap();
  function state(agent) {
    if (!agent) throw new Error('intake tool requires its owning agent');
    if (!owners.has(agent)) owners.set(agent, new Map([...inputs.keys()].map(p => [p, new Set()])));
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
    if (exec.name === 'read') return;
    if (exec.name === 'validate_response') return;
    if (exec.name !== 'write' || resolve(manifest.workspace, exec.arguments.file_path ?? '') !== output)
      return 'Intake work permits reading, whole response.json publication and validate_response only';
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
  function check(document) {
    try {
      return JSON.parse(execFileSync(config.python, [config.checker, config.bundle], {
        input: JSON.stringify({ assignment: manifest.assignment,
          assignment_sha256: manifest.inputs[manifest.assignment], document }),
        encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024,
      }));
    } catch (error) {
      throw new Error(error.stderr?.toString().trim() || error.message);
    }
  }
  const policy = createPolicy(manifest, check);
  ctx.tools.guard(exec => policy.guard(exec));
  ctx.on('tools/result', (exec, result) => policy.observe(exec, result));
  ctx.tools.register({ name: 'validate_response',
    description: 'Validate response.json against the exact sealed assignment using the authoritative controller checker. No scientific validation or admission.',
    parameters: {},
    output: { schema: { type: 'object', additionalProperties: false, required: ['validated','sha256'],
      properties: { validated: { type: 'boolean' }, sha256: { type: 'string' } } },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    execute: () => check(readFileSync(manifest.output, 'utf8')),
  });
  process.stderr.write(marker + '\n');
}
