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
