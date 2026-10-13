/** Native compaction lifecycle, generation reservation and owned summary accounting. */
export const name = 'context-policy';
export const inject = ['llm', 'sessions', 'tokenMeter', 'compaction'];
export const marker = 'SCISAURUS_CONTEXT_READY:native-compaction-1';

function capacityError(message) {
  const error = new Error(message);
  error.code = 'CONTEXT_WINDOW_EXCEEDED';
  return error;
}

export async function assertRequestCapacity(ctx, session, options, outputReservation) {
  const info = await ctx.llm.resolveModelInfo(options.provider, options.model, options.signal);
  const capacity = info.context?.contextWindow;
  const output = Math.max(outputReservation, options.maxTokens ?? outputReservation);
  if (!Number.isSafeInteger(capacity) || capacity <= output)
    throw capacityError('Model context capacity does not accommodate its generation reservation');
  const header = {config: {provider: options.provider, model: options.model, maxTokens: options.maxTokens},
    ...(options.system === undefined ? {} : {system: options.system}),
    ...(options.tools === undefined ? {} : {tools: options.tools})};
  const estimate = options.messages.reduce((sum, message) => sum + ctx.tokenMeter.estimateMessage(message),
    ctx.tokenMeter.estimateHeader(header));
  const measurement = ctx.tokenMeter.measure(session);
  const surfaceEstimate = ctx.tokenMeter.estimateHeader(session.requestHeader()) + measurement.surfaceTokens;
  const scale = surfaceEstimate > 0 ? Math.max(1, measurement.totalTokens / surfaceEstimate) : 1;
  // A summary replays a prefix, so calibrate that prefix without charging the
  // complete uncompressed surface against the summary's smaller input.
  const tokens = options.purpose === 'compaction' ? Math.ceil(estimate * scale) : Math.max(estimate, measurement.totalTokens);
  if (tokens + output > capacity)
    throw capacityError(`Request reservation exceeds context: ${tokens} input + ${output} output > ${capacity}`);
  return {tokens, output, capacity};
}

export function apply(ctx, config) {
  if (!Number.isSafeInteger(config.maxOutputTokens) || config.maxOutputTokens <= 0)
    throw new Error('Context policy requires a positive generation reservation');
  const owners = new Map();
  ctx.on('agent/pre-step', async (event, next) => {
    const decision = await next();
    if (decision.kind !== 'enter' || event.signal.aborted) return decision;
    const session = event.agent.session;
    owners.set(session.id, {session, turn: event.turn, step: event.step});
    const route = session.requestHeader()?.config ?? event.agent.options;
    const info = await ctx.llm.resolveModelInfo(route.provider, route.model, event.signal);
    const capacity = info.context?.contextWindow;
    const output = Math.max(config.maxOutputTokens, route.maxTokens ?? config.maxOutputTokens);
    if (!Number.isSafeInteger(capacity) || capacity <= output)
      throw capacityError('Model context capacity does not accommodate its generation reservation');
    const pending = decision.messages.reduce((sum, message) => sum + ctx.tokenMeter.estimateMessage(message), 0);
    if (pending + ctx.tokenMeter.estimateHeader(session.requestHeader()) + output > capacity)
      throw capacityError('Pending input alone exceeds the available context reservation');
    const threshold = Math.min(capacity * ctx.compaction.config.thresholdRatio, capacity - output);
    // The bounded native path is proactive maintenance, not a replay of a
    // failed research call. Include inbox/context projection before dispatch.
    let pressure = ctx.tokenMeter.measure(session).totalTokens + pending;
    if (pressure >= threshold) {
      ctx.get('toolResultPruner')?.pruneSession(session);
      pressure = ctx.tokenMeter.measure(session).totalTokens + pending;
    }
    while (pressure >= threshold) {
      const result = await ctx.compaction.compactIfNeeded(event.agent, 'context-overflow', event.signal);
      const after = ctx.tokenMeter.measure(session).totalTokens + pending;
      if (!result || after >= pressure)
        throw capacityError('Context cannot be reduced further while preserving the pending input');
      pressure = after;
      if (pressure + output <= capacity) break;
    }
    return decision;
  });
  ctx.on('llm/stream', async function* (options, next) {
    const owner = owners.get(options.sessionId);
    const session = owner?.session ?? ctx.sessions.get(options.sessionId);
    if (!session) throw new Error('Context request lost its owning session');
    await assertRequestCapacity(ctx, session, options, config.maxOutputTokens);
    if (options.purpose !== 'compaction') {
      yield* next();
      return;
    }
    const open = [...session.events].reverse().find(event => event.type === 'compaction/start');
    if (!owner || !open) throw new Error('Summary request lost its compaction owner');
    const identity = {compactionId: open.data.compactionId, turn: owner.turn, step: owner.step};
    session.append('scisaurus/compaction-call', {...identity, phase: 'start',
      provider: options.provider, model: options.model, maxTokens: options.maxTokens});
    try {
      for await (const chunk of next()) {
        session.append('scisaurus/compaction-call', {...identity, phase: 'chunk', chunk});
        yield chunk;
      }
    } finally {
      session.append('scisaurus/compaction-call', {...identity, phase: 'end'});
    }
  });
  process.stderr.write(marker + '\n');
}
