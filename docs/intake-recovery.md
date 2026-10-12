# Explicit intake recovery

A stopped primary intake retains its original session, receipt, task inputs, workspace and history. Reported usage is recorded as `partial_unfinalized`; accounting releases the transport owner without accepting an unknown answer or certifying a final invoice.

`prepare-intake-recovery.py --root MISSION --receipt-sha256 SHA` verifies the sealed stopped attempt, reaped process, original configuration, task hashes and paid ownership. It publishes accounting and reconciliation artifacts and a content-addressed continuation ticket. This operation makes no provider request. Local preparation can resume after interruption using the same ticket; a consumed dispatch cannot be repeated.

Backend revision proofs retain each prior configuration and its exact call boundary. Recovery changes the transport composition only, preserving the provider, model, command and output capacity. The Ollama OpenAI-compatible composition declares `reasoning_effort: "none"` explicitly. Other runtimes keep their existing configuration.

After external account-capacity confirmation, `run-prepared-research-turn.py --root MISSION --runtime-python PYTHON --recovery CAPACITY_JSON --continuation TICKET` consumes one persisted attempt marker. It resumes the existing session and records one response and at most one declared source/runtime observation. No tool repair, installation, scientific computation or automatic redispatch is admitted. Credential files are supplied separately through `--runtime-env-file` and remain outside task inputs.

The transport excludes historical replay from new usage and reasoning checks. Unexpected new reasoning output, a tool failure, provider failure, invalid response or lost execution permission stops the attempt. The original paid receipt remains immutable; unsettled computation remains unknown. Proposals require separate scientific admission and validation.

Offline tests prove lifecycle, ownership, interruption recovery and request serialization. They do not establish that a remote model honors reasoning suppression or that a proposed scientific topic is valid; those require actual observations.
