/** Stop the owning agent before a failed tool can enter another model step. */
export const name = 'stop-on-tool-error';
export const inject = ['tools'];
export const failurePolicy = 'cancel-owning-agent-on-tool-error-1';

export function shouldStop(result) {
  const value = result.value;
  return result.isError || (value?.kind === 'foreground' && (
    value.exitCode !== 0 || value.signal != null || value.timedOut === true ||
    value.aborted === true || value.sandbox?.denied === true ||
    value.sandbox?.runnerFailed === true
  ));
}

export function apply(ctx) {
  ctx.on('tools/result', (exec, result) => {
    if (shouldStop(result) && exec.agent) {
      exec.agent.cancel({ kind: 'parent' });
    }
  });
  process.stderr.write('SCISAURUS_TOOL_FAILURE_POLICY_READY:cancel-owning-agent-on-tool-error-1\n');
}
