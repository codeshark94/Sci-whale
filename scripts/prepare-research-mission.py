#!/usr/bin/env python3
"""Seal a fresh, dormant research intake without dispatching a provider."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scisaurus.core.errors import ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.development_session import DevelopmentSession, development_session_binding
from scisaurus.runtime.dsh_batch import validate_batch_config
from scisaurus.runtime.laboratory import load_laboratory, load_attestation, laboratory_identity
from scisaurus.runtime.mission_time import validate_mission_time_policy
from scisaurus.runtime.software_workbench import tool_contract


CONTRACT = """You are the sole primary researcher for a fresh mission. Select the research
question yourself from the declared broad scope. No earlier question, hypothesis,
result, verdict, literature catalogue or development history is inherited.
Excluded topics are negative constraints only, never recommended starting points.
Reject semantic reuse of an excluded mechanism or function even under a new title.

Compare substantively different candidates using captured primary literature.
For each, identify the closest existing work, the concrete mechanism or prediction
that differs, the strongest explanation that would remove its novelty, and the
coverage limits of your search. A different material name, geometry decoration,
model, optimizer or software stack alone is not a scientific contribution.
If novelty cannot be established, say unresolved; do not treat missing search hits
as proof of novelty. Do not manufacture citations or force a positive selection.

For usefulness, name a concrete application, its operating constraints, a measured
quantity that matters to it and a matched conventional baseline. Distinguish a
plausible application from demonstrated improvement or deployable performance.
Review theoretical bounds and known limits before committing to a claim. State
the estimand, units, null, fair controls, uncertainty, sensitivities, failure modes
and a decisive stopping rule. Choose the smallest informative run using an actual
installed established solver; installed software is an execution constraint,
not itself the reason to prefer a topic. A valid null is acceptable.

Use controller tools and receipt-bound feedback. The harness supplies tools,
errors, accounting and checkpoints; it does not choose the topic or inject a
design. Keep this same serialized development session throughout research.
Independent admission, blind validation, deterministic replay and scientific
review occur at the evidence boundaries; their sessions receive no producer
handle or history. Finish one small real execution and its same-result independent
validation before widening designs, literature or experiments. A final result
requires a fresh independent scientific audit, not your own success declaration.

This assignment prepares topic selection only. Its topic_selection deliverable
is a model-owned proposal, not an admitted question or a completed experiment.
Export one declared tool_action, or the complete topic_selection object required
by output_contract. Abstain when the evidence does not support a useful distinct
question. All actual provider and solver dispatch requires externally confirmed
account capacity and an explicitly authorized execution controller. There is no
provider probe, timer-based unlock, automatic fallback author or legacy committee.
"""


def sha(body):
    return hashlib.sha256(body).hexdigest()


def prepare(root, spec, backend, laboratory_path, attestation_path):
    root = Path(root).resolve()
    if root.exists():
        raise ValidationError("fresh research preparation requires a new directory")
    if (set(spec) != {"objective", "scope", "excluded_topics", "time_policy"}
            or any(not isinstance(spec[k], str) or not spec[k].strip() for k in ("objective", "scope"))
            or not isinstance(spec["excluded_topics"], list)):
        raise ValidationError("research intake requires objective, scope, exclusions and time policy")
    for item in spec["excluded_topics"]:
        if (not isinstance(item, dict) or set(item) != {"id", "title", "research_question", "domain"}
                or any(not isinstance(v, str) or not v.strip() for v in item.values())):
            raise ValidationError("topic exclusions require only identity, title, question and domain")
    validate_mission_time_policy(spec["time_policy"])
    backend = validate_batch_config(backend)
    if backend["max_output_tokens"] < 32768:
        raise ValidationError("research intake requires at least 32768 output tokens")
    laboratory_path, attestation_path = Path(laboratory_path).resolve(), Path(attestation_path).resolve()
    lab, attestation = load_laboratory(laboratory_path), load_attestation(attestation_path)
    if attestation["config_sha256"] != laboratory_identity(lab):
        raise ValidationError("research preparation laboratory and attestation differ")

    root.mkdir(parents=True, mode=0o700)
    project = root / "composer"
    binding = development_session_binding(project)
    # A local lease reserves identity and empty durable storage; it cannot dispatch.
    with DevelopmentSession(binding).lease(backend, time.monotonic() + 30) as session:
        session_id = session.state["session_id"]
    tools = tool_contract()
    assignment = {
        "schema_version": "fresh-research-intake-1", "mission_id": root.name,
        "phase": "topic_selection", "objective": spec["objective"], "scope": spec["scope"],
        "excluded_topics": spec["excluded_topics"], "topic_reuse_allowed": False,
        "selected_topic": None, "frozen_scientific_input": None,
        "inherited_research_evidence": [], "inherited_session_history": [],
        "time_policy": spec["time_policy"],
        "scientific_software_tools": tools,
        "output_contract": {"topic_selection": {
            "decision": "propose|abstain", "selected_candidate_id": "candidate id or null on abstention",
            "candidates": [{"id": "model-owned identity", "question": "falsifiable research question",
                "mechanism": "physical mechanism", "closest_work": "captured source receipt references and comparison",
                "novelty": "difference, strongest counterargument and unresolved search coverage",
                "utility": "application, operating constraints, primary estimand and matched baseline",
                "theoretical_limits": "bounds, limiting cases and competing explanations",
                "pilot": "installed solver, frozen-input plan, controls, units, uncertainty and stopping rule",
                "exclusion_comparison": "mechanism/function comparison against every relevant excluded topic"}],
            "selection_rationale": "evidence-linked choice or abstention",
            "limitations": ["unresolved evidence and execution limits"]}},
        "laboratory": {"config_identity": laboratory_identity(lab),
            "attestation_identity": attestation["attestation_sha256"],
            "runtime_labels": [row["label"] for row in lab["runtimes"]],
            "runtime_drift_checked": False},
    }
    inputs = root / "inputs"
    inputs.mkdir()
    (project / "output").mkdir()
    files = {"inputs/assignment.json": canonical_bytes(assignment),
             "inputs/system-contract.txt": CONTRACT.encode(),
             "backend.json": canonical_bytes(backend),
             "topic-history.json": canonical_bytes({"schema_version": "topic-history-1", "scopes": {}}),
             "composer/output/run-control.json": canonical_bytes({
                 "stop_requested": True, "research_resume_authorization_ref": None})}
    for name, body in files.items():
        path = root / name
        path.write_bytes(body)
        path.chmod(0o444)
    checkpoint = {
        "schema_version": "research-preparation-checkpoint-1", "mission_id": root.name,
        "phase": "awaiting_external_capacity", "session_id": session_id,
        "development_session": binding, "selected_topic": None, "scientific_admission": "not_assessed",
        "usage": {"model_calls": 0, "input_tokens": 0, "output_tokens": 0},
        "provider_dispatched": False, "solver_dispatched": False,
        "controller_registered": False, "resume_authorization": None,
        "input_sha256": {name: sha(body) for name, body in files.items()},
        "session_initial_sha256": sha(Path(binding["root"], "session.json").read_bytes()),
        "laboratory_config_path": str(laboratory_path), "laboratory_attestation_path": str(attestation_path),
        "next_action": "externally confirm account capacity, bind an intake tool-exchange controller to the reserved session, then request primary topic selection",
        "completion_gates": ["closest-work and usefulness assessment", "model-owned frozen pilot input",
            "small actual installed-solver execution", "same-result independent validation and replay",
            "fresh independent scientific final audit"],
    }
    body = canonical_bytes(checkpoint)
    directory = root / "checkpoints"
    directory.mkdir()
    path = directory / (sha(body) + ".json")
    path.write_bytes(body)
    path.chmod(0o444)
    (root / "preparation.json").write_bytes(canonical_bytes({"checkpoint_path": str(path), "checkpoint_sha256": sha(body)}))
    (root / "preparation.json").chmod(0o444)
    return checkpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--laboratory", required=True)
    parser.add_argument("--attestation", required=True)
    args = parser.parse_args()
    checkpoint = prepare(args.root, json.loads(Path(args.spec).read_text()),
        json.loads(Path(args.backend).read_text()), args.laboratory, args.attestation)
    print(json.dumps({key: checkpoint[key] for key in
        ("mission_id", "phase", "session_id", "usage", "provider_dispatched", "controller_registered")}))


if __name__ == "__main__":
    main()
