# Explicit intake recovery

A stopped primary intake retains its original session, receipt, task inputs, workspace and history. Reported usage is recorded as `partial_unfinalized`; accounting releases the transport owner without accepting an unknown answer or certifying a final invoice.

`prepare-intake-recovery.py --root MISSION --receipt-sha256 SHA [--attempt MARKER]` verifies the sealed stopped attempt, reaped process, original configuration, task hashes and paid ownership. It publishes accounting and reconciliation artifacts and a content-addressed continuation ticket. Later explicit steps name their exact stopped marker and latest owned receipt; earlier attempts cannot authorize another recovery. This operation makes no provider request. Local preparation can resume after interruption using the same ticket; a consumed dispatch cannot be repeated.

Backend revision proofs retain each prior configuration and its exact call boundary. Recovery changes the transport composition only, preserving the provider, model, command and output capacity. The Ollama OpenAI-compatible composition declares `reasoning_effort: "none"` explicitly. Other runtimes keep their existing configuration.

After external account-capacity confirmation, `run-prepared-research-turn.py --root MISSION --runtime-python PYTHON --recovery CAPACITY_JSON --continuation TICKET` consumes one persisted attempt marker. It resumes the existing session and records one response and at most one declared source/runtime observation. No tool repair, installation, scientific computation or automatic redispatch is admitted. Credential files are supplied separately through `--runtime-env-file` and remain outside task inputs.

The transport excludes historical replay from new usage and reasoning checks. Unexpected new reasoning output, a tool failure, provider failure, invalid response or lost execution permission stops the attempt. The original paid receipt remains immutable; unsettled computation remains unknown. Proposals require separate scientific admission and validation.

Offline tests prove lifecycle, ownership, interruption recovery and request serialization. They do not establish that a remote model honors reasoning suppression or that a proposed scientific topic is valid; those require actual observations.

## Literature continuation from a completed proposal

`prepare-literature-step.py --root MISSION --receipt-sha256 SHA --attempt MARKER` binds the exact latest completed proposal, archived output digest and existing session. It preserves the original candidate strings as lossless literal chunks so long fields remain readable. Preparation leaves prior session receipts and their accounting unchanged.

The primary returns one `collect_literature` action with one OpenAlex query per existing candidate. The controller validates every candidate and query before acquisition, performs the requests sequentially with zero retries and no anonymous fallback, and stops at the first failure. Raw provider captures and partial batch receipts remain available even when a later candidate fails. Repeated collection cannot dispatch again under the same attempt.

The acquisition returns scholarly metadata, reconstructed abstracts and source locations. It does not acquire full text, select a new topic, evaluate novelty, or admit a scientific claim. The controller stops after this batch; reading results or further retrieval requires another explicit step.

## Function-first invention from retained evidence

`prepare-invention-step.py --root MISSION --receipt-sha256 SHA --attempt MARKER` seals one invention step from the exact latest completed literature observation. It verifies the primary receipt, complete session lineage, archived proposal, observation ownership and raw source captures. The retained candidates are marked `held` when the ticket is activated; their original text, receipts and usage remain immutable.

The same primary develops unmet functions, causally distinct mechanisms, distinguishing predictions and counterexamples before considering solver or fabrication feasibility. Each direction includes a matched baseline, null, measurable quantity and units, falsifier, physical limits, strongest conventional explanation and functional separation from every held candidate. Captured literature is unreviewed evidence; missing hits cannot establish novelty. The primary may abstain.

This phase permits one invention response and no controller tool operation. Invalid output, tool error, provider failure or unexpected reasoning stops without repair or redispatch. The controller validates custody and response structure only. It cannot certify novelty, physical consistency, usefulness or scientific admission. A completed response remains `unverified` until separate source-backed assessment and an authorized frozen experiment with independent validation.

Native read events must show every line of the exact receipt-bound input files in the current transport turn. Matching call identity, session, path and displayed source text exclude historical or unrelated reads from coverage. Skipped or truncated ranges leave the paid response preserved but stop the controller on error; they do not authorize a repair call. Coverage establishes delivery through the read tool, not understanding or scientific correctness.

## Explicit self-review after incomplete input delivery

`prepare-invention-review.py --root MISSION --receipt-sha256 SHA --attempt MARKER` binds the current stopped frontier and the exact completed predecessor. Its receipt, output, assignment and native read events determine the review inputs. The original response, failure and paid usage remain unchanged; a new explicit ticket does not automatically release another attempt.

`unread-input.txt` contains the original missing assignment lines, in their original order and bytes. `hypotheses.json` preserves the complete authored response with lossless string chunks. The primary must finish the unread fragment before any other input or operation, then read every remaining current input line. The controller checks native call/result order and displayed text. Complete reads in the wrong order fail, as do omitted lines. Failure preserves the paid response and stops without repair or redispatch.

The same primary reviews only the retained hypothesis identities: causal derivation, physical limits, predictions, matched controls, conventional alternatives and prior-art scope. Source references must belong to retained captures and keep their actual metadata/abstract scope. The self-review may hold or reject hypotheses; it cannot certify novelty, admit a topic, substitute a new hypothesis, retrieve sources or execute a solver. Independent scientific validation remains a separate boundary.

## Capability imagination and structural concept design

`prepare-concept-step.py --root MISSION --receipt-sha256 SHA --attempt MARKER` verifies the current completed frontier and its full native input delivery, then seals one concept-design ticket for the same primary. The versioned held catalog derives only from owned completed authored outputs. It retains each candidate's function and mechanism as negative constraints, including separate versions that reuse an identity. Original responses, reviews, failed/unknown receipts, usage and backend bindings remain unchanged.

The primary imagines unfamiliar useful capabilities before choosing effects, materials, geometries or solvers. It then develops selected capabilities into concrete mechanisms and structures: components, arrangement, interfaces, state transitions and a text schematic. Energy, conservation, units and causality are checked after the design takes shape. A future minimal model or prototype supplies a matched baseline, distinguishing prediction, null, falsifier and stop condition.

An unverified `concept_only` proposal is admissible as a draft without prior full-text novelty certification or executed results. This permission does not admit a scientific claim or certify novelty, usefulness or physical feasibility. The controller supplies no positive concept, coefficient or success target and executes no scientific tool during this step. Full input delivery and response ownership remain mandatory. Errors stop with paid evidence preserved, and a consumed attempt cannot redispatch automatically.

## Interrupted structured documents

Structured intake responses use parsed JSON objects and complete-document publication. Updates address structural keys and array positions, followed by serialization and a complete native write. Substring edits cannot reliably identify repeated JSON fields. Local validation must assert its conditions and return a nonzero exit on failure so the native error policy can stop before another model step.

`prepare-structured-recovery.py --root MISSION --receipt-sha256 SHA --attempt MARKER` verifies a reaped tool abort, its current stopped owner and immutable assignment. Successful native writes and edits are replayed in the owned transport turn; the interrupted workspace document must match exactly. A sealed snapshot preserves the raw document and failure provenance. The existing official accounting API retains the unknown receipt and reported paid usage as `partial_unfinalized`, then releases the transport owner. Idempotent preparation reuses that accounting proof; it does not revise the backend, reset usage or accept the draft.

An explicit continuation supplies the snapshot as a readable literal projection and retains the original concept contract, held catalog and draft identities. The same primary completes the document through structural JSON publication. It may explicitly abstain while preserving the interrupted concepts in the snapshot. Source retrieval, solver execution, another author, automatic retries and scientific admission remain unavailable. Full input delivery and current receipt ownership are rechecked before a new unreviewed output is retained.
