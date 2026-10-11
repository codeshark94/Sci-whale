# Persistent mission researcher

A stopped mission with an admitted question can transfer its remaining research
process to the existing project-owned DSH development session. The researcher
owns literature use, theoretical analysis, controls, source revisions, tool
selection, interpretation and final synthesis. The controller exposes tools,
records observed feedback and preserves checkpoints. It does not run an agenda
model or a pre-execution stage committee in this mode.

## Control registration and resume

Use `ComposerRunner(workflow, resume=True, control_only=True)` and call
`set_research_process("session_led")`. This registers an immutable mission-wide
policy bound to the workflow, original question, checkpoint and existing session.
It preserves the workflow, historical stage contexts, scientific inputs, receipts
and cumulative usage. The experiment-only policy remains available for workflows
that use the stage scheduler; the mission policy takes precedence over it.
Legacy producer, repair-panel, specialist-dispatch and stage-review entry points
reject execution under the mission policy, including direct API calls outside the
workflow router. Independent admission remains accessible through researcher tools.

Registration cannot execute research. `authorize_research_resume(evidence)` is a
separate stopped control operation requiring an externally observed recovery
source, a nonfuture timestamp with timezone, and official reconciliation references.
Unsettled researcher or foundry dispatches block authorization. An orchestration
change cannot settle a paid unknown outcome, refresh a exhausted generation
contract, or establish account capacity. No provider probe is part of this API.
New provider failures revoke the prior resume authorization.

## Research loop

The persistent researcher chooses one declared operation and receives the actual
controller observation in its next turn. Supported operations include literature
and source access, installed-runtime inspection, recorded computation, immutable
artifact access, research notes, frozen pilot description and explicit independent
pilot evaluation. The retained literature catalogue and original engineering
receipts are available through their verified references and immutable files.
Checkpoint notes are researcher statements with no scientific admission authority.

The frozen pilot assignment belongs to the current control-store head and retained
question. The researcher supplies complete executor source and scientific intent,
and dispositions with evidence for every retained theoretical/control objection.
The admission service consumes that already paid candidate once. It cannot launch
another author session, author a repair or fall back to a different author model.
A known rejection returns the observed repair evidence to the same researcher.

`run` records computation; its receipt alone is not validation. `evaluate_pilot`
requests the existing admission and experiment services: current preview,
separately authored blinded validator, fresh replay, independent recalculation,
scientific review and a fresh evaluation project. The validator receives no
producer development handle or history. A pending or unknown evaluation retains
its candidate and observations and blocks further execution or literature
expansion. An identical rejected request returns its retained feedback. Runtime
and result ownership are rechecked before successful evidence reuse.
Dependency bindings may update the retained literature gate, but cannot replace
the admitted executor, validator, input or scientific intent.

## Acceptance and recovery

The final proposal includes the current independently accepted result, summary
and limitations. A fresh isolated read-only scientific audit checks the original
question, theoretical scope, controls, uncertainty, novelty and usefulness against
current source and complete raw observations. A limited pilot or a successful
arithmetic reduction cannot complete the mission. A valid null may be accepted.
The original required workflow outputs and outstanding work orders remain gates.
Accepted completion preserves the previous stage context in an immutable artifact;
release approval follows the original workflow policy.
Reopening a completed checkpoint rechecks its current result files, ownership,
required outputs and exact retained independent audit before returning completion.
It does not dispatch another researcher or final auditor. Changed evidence or a
missing matching audit holds the checkpoint rather than replaying a stale verdict.
Fresh and cached final audits bind their immutable receipt hash, input assignment,
controller contract, output verdict, backend, usage and artifact owner. Independent
dispatch checks the same effective read grants used by the sandbox, including
command virtual environments, absolute file arguments, loader dependencies,
interpreter roots and laboratory dependencies. Those grants cannot expose the
producer project; the isolated job's own frozen inputs and workspace remain
accessible. Evidence custody errors stop the primary exchange rather than
requesting another scientific author turn.

Tool and final-audit unknown outcomes remain sealed, including their original paid
usage. `reconcile_research_operation(ref, recovery_ref)` requires the current exact
operation and an official `research-operation-reconciliation-1` proof binding its
reference, body digest, receipt and usage, with disposition
`paid_unknown_history_only`. It releases a hold without adopting an unknown answer.
Underlying dispatch reconciliation is required first. Recovery never overwrites
old execution projects, or changes an unknown receipt to scientific success.
`reconcile_research_primary(receipt_sha256)` settles a reaped primary unknown
transport only against its exact sealed paid checkpoint and original session
binding. Its immutable receipt and unknown scientific outcome stay in history;
accounting settlement does not authorize provider calls or adopt partial output.
Final audits are sealed as started before dispatch. Any unsettled tool or audit
head blocks new primary dispatches, computations and changed final proposals;
changing the requested operation or claims cannot bypass that hold.
Each audit attempt has a separate dispatch root. Its recovery proof also requires
`receipt_sha256`; the actual receipt must bind that root, backend and frozen input,
with complete nonnegative usage and a reaped, inactive process. Recorded usage is
checkpointed before the hold is released. Missing receipt or usage is unknown,
never evidence of zero cost.

The primary reserves one host model slot; nested independent experiment services
use at most the remaining slots. All dispatches retain finite watchdogs and the
original mission time policy. Registration, offline tests and controller contract
consumption are distinct from live primary/solver/validator consumption and from
scientific completion.

## Offline flow verification

`scisaurus.tests.test_mission_research_flow` connects the real structured client,
tool/final exchange, persistent session lease, mission controller, receipt-backed
result files, read-only result store, final audit gate and durable completion.
Provider dispatch, scientific admission and experiment execution are replaced at
their service boundaries. Network sockets, subprocess launch, general model
completion, alternate author completion and the legacy stage path are forbidden.
Fixtures use temporary projects and synthetic observations; their acceptance
verdicts prove control flow only. Other required workflow stages are pre-completed
in the fixtures, and history-catalog and repair-packet hydration are replaced.
The connected tests do not verify those stages' scientific outputs or a live
provider's restoration of the retained session history.

The scenarios cover external recovery admission, known rejection feedback, final
HOLD feedback, unknown evaluation/final audit, changed raw data, finite dispatch
interruption, cold continuation with the same session and completed checkpoint
reopening without new usage. Live recovery, installed solver behavior, independent
physical recalculation and the actual scientific conclusion require fresh runtime
evidence after account capacity is externally confirmed.
