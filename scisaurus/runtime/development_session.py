"""Project-owned, serialized DSH development history and workspace."""
from contextlib import closing, contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import uuid

from scisaurus.core.errors import ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.run_control import ensure_run_allowed
from scisaurus.core.store import ArtifactStore


def development_session_binding(project):
    owner = Path(project).resolve()
    return {"project_id": str(owner), "root": str(owner / "development-session")}


def _verify_receipt_files(receipt_path, receipt):
    boundaries = [("input", receipt.get("input_sha256", {}))]
    boundaries.extend((turn["input_directory"], turn["input_sha256"])
                      for turn in receipt.get("turns", []))
    boundaries.extend((f"turns/{index}", turn["outputs"])
                      for index, turn in enumerate(receipt.get("turns", [])))
    if receipt.get("status") == "completed":
        if receipt.get("output_directory") != "outputs":
            raise ValidationError("development history has no immutable output archive")
        boundaries.append(("outputs", receipt["outputs"]))
    for directory, hashes in boundaries:
        for name, expected in hashes.items():
            path = receipt_path.parent / directory / name
            if (not path.resolve().is_relative_to(receipt_path.parent)
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValidationError("development history output changed" if directory == "outputs"
                                      else "development history evidence changed")


class DevelopmentSession:
    def __init__(self, binding):
        if (not isinstance(binding, dict) or set(binding) != {"root", "project_id"}
                or any(not isinstance(v, str) or not Path(v).is_absolute() for v in binding.values())):
            raise ValidationError("development session requires an absolute project owner and root")
        self.binding = deepcopy(binding)
        self.root = Path(binding["root"]).resolve()
        project = Path(binding["project_id"]).resolve()
        if self.root == project or not self.root.is_relative_to(project):
            raise ValidationError("development workspace must belong to its project")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "session.json"
        self.state = None

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(canonical_bytes(self.state))
        temporary.replace(self.path)

    def bind_unused_backend(self, config, *, expected_state_sha256, binding_ref):
        """Bind an unused reservation to a sealed activation contract without changing its identity."""
        with (self.root / "owner.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValidationError("development reservation is already owned") from exc
            raw = self.path.read_bytes()
            state = json.loads(raw)
            if (hashlib.sha256(raw).hexdigest() != expected_state_sha256
                    or state.get("schema_version") != "dsh-development-session-1"
                    or state.get("binding") != self.binding or not state.get("session_id")
                    or state.get("calls") != [] or state.get("active_job") is not None):
                raise ValidationError("backend activation requires the exact unused development reservation")
            proof = Path(binding_ref).resolve()
            if not proof.is_file() or not proof.is_relative_to(Path(self.binding["project_id"])):
                raise ValidationError("backend activation proof belongs to another project")
            body = json.loads(proof.read_bytes())
            identity = hashlib.sha256(canonical_bytes(config)).hexdigest()
            if (body.get("initial_session_sha256") != expected_state_sha256
                    or body.get("session_id") != state["session_id"]
                    or body.get("backend_sha256") != identity
                    or body.get("development_session") != self.binding):
                raise ValidationError("backend activation proof differs from its reservation or configuration")
            state.update(config_sha256=identity, pre_dispatch_binding={
                "path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()})
            self._verify_unused_backend_binding(state)
            self.state = state
            self.save()

    def _verify_unused_backend_binding(self, state):
        reference = state["pre_dispatch_binding"]
        path = Path(reference["path"]).resolve()
        raw = path.read_bytes()
        proof = json.loads(raw)
        if (not path.is_relative_to(Path(self.binding["project_id"]))
                or hashlib.sha256(raw).hexdigest() != reference["sha256"]
                or proof.get("development_session") != self.binding
                or proof.get("session_id") != state["session_id"]
                or proof.get("backend_sha256") != state["config_sha256"]
                or hashlib.sha256(canonical_bytes(proof.get("backend"))).hexdigest() != state["config_sha256"]):
            raise ValidationError("development activation lost its sealed backend proof")
        original = Path(proof["initial_session"])
        original_raw = original.read_bytes()
        initial = json.loads(original_raw)
        if (not original.resolve().is_relative_to(path.parent)
                or hashlib.sha256(original_raw).hexdigest() != proof["initial_session_sha256"]
                or initial.get("binding") != self.binding or initial.get("session_id") != state["session_id"]
                or initial.get("calls") != [] or initial.get("active_job") is not None):
            raise ValidationError("development activation lost its unused reservation")

    @contextmanager
    def lease(self, config, deadline):
        with (self.root / "owner.lock").open("a+b") as lock:
            while True:
                ensure_run_allowed()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("development session is owned by another dispatch")
                    time.sleep(.05)
            try:
                digest = hashlib.sha256(canonical_bytes(config)).hexdigest()
                if self.path.exists():
                    self.state = json.loads(self.path.read_bytes())
                    if (self.state.get("schema_version") != "dsh-development-session-1"
                            or self.state.get("binding") != self.binding
                            or self.state.get("config_sha256") != digest):
                        raise ValidationError("development session owner or backend changed")
                    if self.state.get("active_job"):
                        raise ValidationError("development session has an unsettled dispatch")
                    versions = self._verify_backend_lineage(self.state)
                    for index, call in enumerate(self.state["calls"]):
                        receipt_path = Path(call["receipt"]).resolve()
                        body = receipt_path.read_bytes()
                        if hashlib.sha256(body).hexdigest() != call["sha256"]:
                            raise ValidationError("development session receipt changed")
                        receipt = json.loads(body)
                        owner = receipt.get("development_session", {})
                        if (receipt.get("job_directory") != str(receipt_path.parent)
                                or owner.get("binding") != self.binding
                                or owner.get("prior_receipts") != self.state["calls"][:index]
                                or receipt.get("session_id") != self.state["session_id"]
                                or receipt.get("config_sha256") != next((key for key, boundary in reversed(list(versions.items()))
                                    if index >= boundary), None)
                                or not receipt.get("process_reaped")
                                or not (receipt.get("status") == "completed"
                                    or receipt.get("status") == "failed" and receipt.get("outcome_known") is True
                                    or self._reconciled_unknown(call, receipt))):
                            raise ValidationError("development session requires a settled, reaped outcome")
                        _verify_receipt_files(receipt_path, receipt)
                else:
                    self.state = {"schema_version": "dsh-development-session-1", "binding": self.binding,
                                  "config_sha256": digest, "session_id": uuid.uuid4().hex,
                                  "calls": [], "active_job": None}
                    self.save()
                yield self
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _reconciled_unknown(self, call, receipt):
        if receipt.get("status") != "result_unknown" or not call.get("reconciliation_ref"):
            return False
        project = Path(self.binding["project_id"])
        with closing(sqlite3.connect((project / "state/control.sqlite").as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            store = ArtifactStore(SimpleNamespace(dir=str(project), _conn=connection))
            manifest = store.get(call["reconciliation_ref"])
            body = store.read_body(manifest["body_hash"])
            proof = json.loads(body)
            if (hashlib.sha256(body).hexdigest() != manifest["body_hash"]
                    or manifest["author"] != "command.recovery"
                    or manifest["artifact_id"] != "command/development-dispatch-reconciliations/" + call["sha256"]
                    or proof.get("schema_version") != "development-dispatch-reconciliation-1"
                    or proof.get("binding") != self.binding
                    or proof.get("receipt") != call["receipt"]
                    or proof.get("receipt_sha256") != call["sha256"]
                    or proof.get("session_id") != receipt.get("session_id")
                    or proof.get("config_sha256") != receipt.get("config_sha256")
                    or proof.get("disposition") != "paid_unknown_history_only"):
                raise ValidationError("development unknown history lost its reconciliation proof")
            if proof.get("billing_completeness") == "partial_unfinalized" and (
                    proof.get("charged_usage") != receipt.get("usage") or call.get("usage") != receipt.get("usage")):
                raise ValidationError("development partial accounting differs from its paid receipt")
            for ref, expected in proof["evidence"].items():
                record = store.get(ref)
                if (record["body_hash"] != expected
                        or hashlib.sha256(store.read_body(expected)).hexdigest() != expected):
                    raise ValidationError("development reconciliation accounting evidence changed")
            return True

    def reconcile_unknown(self, config, store, *, assessment_ref, checkpoint_ref, receipt_sha256):
        """Explicitly release a reaped, accounted dispatch without adopting its answer."""
        if Path(store.control.dir).resolve() != Path(self.binding["project_id"]).resolve():
            raise ValidationError("development reconciliation belongs to another project")
        with (self.root / "owner.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValidationError("development dispatch is still owned") from exc
            self.state = json.loads(self.path.read_bytes())
            digest = hashlib.sha256(canonical_bytes(config)).hexdigest()
            if self.state.get("binding") != self.binding or self.state.get("config_sha256") != digest:
                raise ValidationError("development reconciliation owner or backend changed")
            records = {}
            def read(ref, author):
                manifest = store.get(ref)
                body = store.read_body(manifest["body_hash"])
                if (manifest["author"] != author
                        or hashlib.sha256(body).hexdigest() != manifest["body_hash"]):
                    raise ValidationError("development reconciliation has unowned evidence")
                records[ref] = manifest["body_hash"]
                return json.loads(body)
            assessment = read(assessment_ref, "command.composer")
            producer_ref = assessment["producer_execution_ref"]
            producer_manifest = store.get(producer_ref)
            producer = read(producer_ref, producer_manifest["author"])
            invoice = read(assessment["usage_invoice_ref"], "command.composer")
            plan = read(invoice["assignment_plan_ref"], store.get(invoice["assignment_plan_ref"])["author"])
            read(invoice["chief_synthesis_ref"], store.get(invoice["chief_synthesis_ref"])["author"])
            checkpoint = read(checkpoint_ref, "command.composer")
            report = producer.get("report", {})
            path = Path(report.get("dsh_receipt", "")).resolve()
            body = path.read_bytes()
            receipt = json.loads(body)
            receipt_hash = hashlib.sha256(body).hexdigest()
            if receipt_hash != receipt_sha256:
                raise ValidationError("development reconciliation original receipt changed")
            if report.get("dsh_receipt_sha256") not in (None, receipt_hash):
                raise ValidationError("development reconciliation differs from its sealed receipt")
            if self.state.get("active_job") is None:
                prior = next((call for call in self.state["calls"] if call["receipt"] == str(path)), None)
                if prior and prior["sha256"] == receipt_hash and self._reconciled_unknown(prior, receipt):
                    return store.get(prior["reconciliation_ref"])
                raise ValidationError("development reconciliation has no active dispatch")
            usage = invoice.get("usage", {})
            identity = assessment.get("identity", {})
            identity_digest = hashlib.sha256(canonical_bytes(identity)).hexdigest()
            binding = producer.get("input_ref", {})
            budget = read(binding["ref"], "command.composer")
            request = read(budget["evidence_request_ref"], "command.composer")
            owner = invoice.get("model_budget_owner", {})
            invoice_id = hashlib.sha256(canonical_bytes({"stage_id": invoice["stage_id"],
                "assignment_plan_ref": invoice["assignment_plan_ref"]})).hexdigest()
            namespace = f"repair-panel:{invoice['stage_id']}:{invoice_id}"
            from scisaurus.runtime.repair_accounting import repair_panel_invoice_owner
            from scisaurus.runtime.software_workbench import software_computation_identity
            accounted_owner = repair_panel_invoice_owner(store, project_id=self.binding["project_id"],
                workflow_id=checkpoint.get("workflow_id"), stage_records=checkpoint.get("stages", {}),
                stage_id=invoice["stage_id"], invoice=invoice, usage_keys=usage)
            attempt = store.control._conn.execute("SELECT state,usage_json FROM attempts WHERE attempt_id=?",
                                                  (owner.get("attempt_id"),)).fetchone()
            actual = json.loads(attempt[1]).get("actual", {}) if attempt else {}
            if (assessment.get("status") != "blocked" or report.get("status") != "result_unknown"
                    or producer.get("project_id") != self.binding["project_id"]
                    or producer_manifest["author"] != producer.get("assigned_role")
                    or store.get(assessment_ref)["artifact_id"] != "command/scientific-software-assessments/" + identity_digest + "/failure"
                    or binding.get("kind") != "scientific_software_assessment"
                    or binding.get("digest") != identity_digest or invoice.get("input_sha256") != identity_digest
                    or budget.get("model_budget_owner") != owner
                    or accounted_owner.get("attempt_id") != owner.get("attempt_id")
                    or accounted_owner.get("cycle") != owner.get("cycle")
                    or invoice.get("schema_version") != "capability-repair-usage-1"
                    or store.get(assessment["usage_invoice_ref"])["artifact_id"] != "command/capability-repair-usage/" + invoice_id
                    or binding.get("stage_id") != invoice.get("stage_id")
                    or any(request.get(key) != value for key, value in identity.items() if key != "computation_scope")
                    or ("computation_scope" in identity and software_computation_identity(
                        request.get("computation_scope", {})) != identity["computation_scope"])
                    or assessment.get("evidence", {}).get("request") != request
                    or assessment.get("evidence", {}).get("request_ref") != budget["evidence_request_ref"]
                    or store.get(budget["evidence_request_ref"])["artifact_id"] != "command/scientific-software-assessments/" + identity_digest + "/request"
                    or budget.get("panel_stage_id") != producer.get("stage_id")
                    or budget.get("assignment_attempt_number") != producer.get("attempt_number")
                    or plan.get("stage_id") != producer.get("stage_id")
                    or plan.get("attempt_number") != producer.get("attempt_number")
                    or invoice.get("assignment_plan_ref") != assessment.get("ledger", {}).get("assignment_plan_ref")
                    or receipt.get("status") != "result_unknown" or receipt.get("outcome_known") is not False
                    or receipt.get("process_reaped") is not True or receipt.get("job_directory") != str(path.parent)
                    or str(path) != self.state["active_job"] or receipt.get("session_id") != self.state["session_id"]
                    or receipt.get("config_sha256") != digest
                    or receipt.get("development_session", {}).get("binding") != self.binding
                    or receipt.get("development_session", {}).get("prior_receipts") != self.state["calls"]
                    or checkpoint.get("status") not in {"paused", "blocked"}
                    or checkpoint.get("schema_version") != "composer-checkpoint-1"
                    or not store.get(checkpoint_ref)["artifact_id"].startswith("command/composer/checkpoints/")
                    or checkpoint.get("stage_usage_totals", {}).get(namespace) != usage
                    or namespace in checkpoint.get("pending_stage_usage", {})
                    or not attempt or attempt[0] != "failed" or not usage
                    or not {"model_calls", "input_tokens", "output_tokens"}.issubset(usage)
                    or any(type(value) is not int or value < 0 or report.get("usage", {}).get(key, 0) != value
                           or receipt.get("usage", {}).get(key, 0) != value or actual.get(key, 0) < value
                           for key, value in usage.items())):
                raise ValidationError("development unknown dispatch lacks exact paid ownership")
            _verify_receipt_files(path, receipt)
            try:
                os.kill(receipt["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise ValidationError("development process is still alive")
            proof = {"schema_version": "development-dispatch-reconciliation-1", "binding": self.binding,
                     "receipt": str(path), "receipt_sha256": receipt_hash, "session_id": receipt["session_id"],
                     "config_sha256": digest, "evidence": records, "charged_usage": usage,
                     "disposition": "paid_unknown_history_only"}
            logical = "command/development-dispatch-reconciliations/" + receipt_hash
            record = store.head(logical)
            if record is None:
                record = store.publish_artifact(logical_id=logical, artifact_type="note", author="command.recovery",
                    body=canonical_bytes(proof), media_type="application/json",
                    inputs=[{"ref": ref, "purpose": "subject"} for ref in records])
            elif store.read_body(record["body_hash"]) != canonical_bytes(proof):
                raise ValidationError("development reconciliation changed its original proof")
            self.state["calls"].append({"receipt": str(path), "sha256": receipt_hash, "usage": receipt["usage"],
                                        "reconciliation_ref": record["artifact_ref"]})
            self.state["active_job"] = None
            self.save()
            return record

    @property
    def resume(self):
        return bool(self.state["calls"])

    def reconcile_accounted_unknown(self, config, store, *, checkpoint_ref, policy_ref, receipt_sha256):
        """Settle a mission researcher transport using its sealed checkpoint charge."""
        if Path(store.control.dir).resolve() != Path(self.binding["project_id"]).resolve():
            raise ValidationError("researcher settlement belongs to another project")
        with (self.root / "owner.lock").open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValidationError("researcher dispatch is still owned") from exc
            self.state = json.loads(self.path.read_bytes())
            config_sha = hashlib.sha256(canonical_bytes(config)).hexdigest()
            evidence = {}
            def read(ref):
                manifest = store.get(ref)
                raw = store.read_body(manifest["body_hash"])
                if hashlib.sha256(raw).hexdigest() != manifest["body_hash"]:
                    raise ValidationError("researcher settlement evidence changed")
                evidence[ref] = manifest["body_hash"]
                return manifest, json.loads(raw)
            checkpoint_manifest, checkpoint = read(checkpoint_ref)
            policy_manifest, policy = read(policy_ref)
            active = self.state.get("active_job")
            if active is None:
                prior = next((call for call in self.state["calls"] if call["sha256"] == receipt_sha256), None)
                if prior and self._reconciled_unknown(prior, json.loads(Path(prior["receipt"]).read_bytes())):
                    return store.get(prior["reconciliation_ref"])
                raise ValidationError("researcher settlement has no active unknown dispatch")
            path = Path(active).resolve()
            raw = path.read_bytes()
            receipt = json.loads(raw)
            assignment_raw = (path.parent / "input/assignment.json").read_bytes()
            assignment = json.loads(assignment_raw)
            usage = receipt.get("usage", {})
            namespace = "research-session:" + str(path)
            if (self.state.get("binding") != self.binding or self.state.get("config_sha256") != config_sha
                    or hashlib.sha256(raw).hexdigest() != receipt_sha256
                    or receipt.get("status") != "result_unknown" or receipt.get("outcome_known") is not False
                    or receipt.get("process_reaped") is not True or receipt.get("job_directory") != str(path.parent)
                    or receipt.get("session_id") != self.state["session_id"] or receipt.get("config_sha256") != config_sha
                    or receipt.get("development_session", {}).get("binding") != self.binding
                    or receipt.get("development_session", {}).get("prior_receipts") != self.state["calls"]
                    or not path.is_relative_to(Path(self.binding["project_id"])/"research-session/jobs")
                    or checkpoint_manifest.get("author") != "command.composer"
                    or not checkpoint_manifest.get("artifact_id", "").startswith("command/composer/checkpoints/")
                    or checkpoint.get("status") not in {"paused", "blocked"}
                    or checkpoint.get("stage_usage_totals", {}).get(namespace) != usage
                    or policy_manifest.get("author") != "command.operator"
                    or policy_manifest.get("artifact_id") != "command/operator/research-process"
                    or policy.get("schema_version") != "operator-research-process-1" or policy.get("mode") != "session_led"
                    or policy.get("development_session") != self.binding or policy.get("session_id") != self.state["session_id"]
                    or assignment.get("schema_version") != "mission-research-assignment-1"
                    or receipt.get("input_sha256", {}).get("assignment.json") != hashlib.sha256(assignment_raw).hexdigest()
                    or assignment.get("workflow_id") != checkpoint.get("workflow_id")
                    or checkpoint.get("workflow_id") != policy.get("workflow_id")
                    or assignment.get("research_process", {}).get("body_sha256") != policy_manifest["body_hash"]
                    or assignment.get("research_process") != checkpoint.get("research_process")
                    or assignment.get("research_process", {}).get("artifact_ref") != policy_ref
                    or not {"model_calls", "input_tokens", "output_tokens"}.issubset(usage)
                    or any(type(amount) is not int or amount < 0 or checkpoint.get("usage", {}).get(key,0) < amount
                           for key,amount in usage.items())):
                raise ValidationError("unknown researcher transport lacks exact paid ownership")
            _verify_receipt_files(path, receipt)
            try:
                os.kill(receipt["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise ValidationError("researcher process is still alive")
            proof = {"schema_version":"development-dispatch-reconciliation-1", "binding":self.binding,
                "receipt":str(path),"receipt_sha256":receipt_sha256,"session_id":receipt["session_id"],
                "config_sha256":config_sha,"evidence":evidence,"charged_usage":usage,
                "disposition":"paid_unknown_history_only"}
            logical = "command/development-dispatch-reconciliations/"+receipt_sha256
            record = store.head(logical)
            if record is None:
                record = store.publish_artifact(logical_id=logical,artifact_type="note",author="command.recovery",
                    body=canonical_bytes(proof),media_type="application/json",
                    inputs=[{"ref":ref,"purpose":"subject"} for ref in evidence])
            elif store.read_body(record["body_hash"]) != canonical_bytes(proof):
                raise ValidationError("researcher reconciliation changed its original proof")
            self.state["calls"].append({"receipt":str(path),"sha256":receipt_sha256,"usage":usage,
                                       "reconciliation_ref":record["artifact_ref"]})
            self.state["active_job"] = None
            self.save()
            return record

    def reconcile_stopped_attempt(self, config, store, *, attempt_path, receipt_sha256):
        """Retain reported partial charges and release only an exactly owned reaped intake."""
        project = Path(self.binding["project_id"])
        if Path(store.control.dir).resolve() != project:
            raise ValidationError("attempt accounting belongs to another project")
        with (self.root / "owner.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = json.loads(self.path.read_bytes())
            attempt_path = Path(attempt_path).resolve()
            attempt_raw = attempt_path.read_bytes()
            attempt = json.loads(attempt_raw)
            sealed = attempt_path.parent / (hashlib.sha256(attempt_raw).hexdigest() + ".json")
            path = Path(attempt.get("receipt", "")).resolve()
            raw = path.read_bytes()
            receipt = json.loads(raw)
            digest = hashlib.sha256(canonical_bytes(config)).hexdigest()
            usage = receipt.get("usage", {})
            if attempt.get("preparation_sha256"):
                mission = project.parent
                pointer = json.loads((mission / "preparation.json").read_bytes())
                checkpoint_path = Path(pointer["checkpoint_path"]).resolve()
                checkpoint_raw = checkpoint_path.read_bytes()
                checkpoint = json.loads(checkpoint_raw)
                backend_proof_path = Path(attempt["backend_binding"]).resolve()
                backend_proof = json.loads(backend_proof_path.read_bytes())
                if (not checkpoint_path.is_relative_to(mission / "checkpoints")
                        or hashlib.sha256(checkpoint_raw).hexdigest() != attempt["preparation_sha256"]
                        or pointer["checkpoint_sha256"] != attempt["preparation_sha256"]
                        or checkpoint["development_session"] != self.binding
                        or checkpoint["session_id"] != self.state["session_id"]
                        or not backend_proof_path.is_relative_to(project / "intake-controller")
                        or backend_proof.get("backend_sha256") != digest
                        or backend_proof.get("development_session") != self.binding
                        or backend_proof.get("session_id") != self.state["session_id"]
                        or hashlib.sha256(canonical_bytes(backend_proof["backend"])).hexdigest() != digest):
                    raise ValidationError("stopped attempt lost its sealed preparation or backend")
            if (not attempt_path.is_relative_to(project / "intake-controller")
                    or sealed.read_bytes() != attempt_raw
                    or attempt.get("schema_version") != "fresh-intake-attempt-1"
                    or attempt.get("status") != "stopped_on_error"
                    or attempt.get("scientific_admission") != "not_assessed"
                    or attempt.get("session_id") != self.state["session_id"]
                    or attempt.get("usage") != usage
                    or self.state.get("binding") != self.binding
                    or self.state.get("config_sha256") != digest
                    or not path.is_relative_to(project / "intake-jobs")
                    or hashlib.sha256(raw).hexdigest() != receipt_sha256
                    or receipt.get("status") != "result_unknown"
                    or receipt.get("outcome_known") is True
                    or receipt.get("process_reaped") is not True
                    or receipt.get("job_directory") != str(path.parent)
                    or receipt.get("session_id") != self.state["session_id"]
                    or receipt.get("config_sha256") != digest
                    or receipt.get("development_session", {}).get("binding") != self.binding
                    or not {"model_calls", "input_tokens", "output_tokens"}.issubset(usage)
                    or any(type(v) is not int or v < 0 for v in usage.values())
                    or receipt.get("input_sha256", {}).get("assignment.json") != hashlib.sha256(
                        (path.parent / "input/assignment.json").read_bytes()).hexdigest()):
                raise ValidationError("stopped attempt lacks exact paid ownership")
            prior = next((c for c in self.state["calls"] if c["sha256"] == receipt_sha256), None)
            if self.state.get("active_job") is None and prior and self._reconciled_unknown(prior, receipt):
                return store.get(prior["reconciliation_ref"])
            if (self.state.get("active_job") != str(path)
                    or receipt.get("development_session", {}).get("prior_receipts") != self.state["calls"]):
                raise ValidationError("stopped attempt lost its active dispatch owner")
            _verify_receipt_files(path, receipt)
            try:
                os.kill(receipt["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise ValidationError("stopped attempt process is still alive")
            accounting = {"schema_version": "stopped-attempt-accounting-1", "binding": self.binding,
                "attempt_sha256": hashlib.sha256(attempt_raw).hexdigest(), "attempt": attempt,
                "receipt_sha256": receipt_sha256, "reported_usage": usage,
                "billing_completeness": "partial_unfinalized", "scientific_outcome": "unknown"}
            evidence_id = "command/stopped-attempt-accounting/" + receipt_sha256
            evidence = store.head(evidence_id) or store.publish_artifact(logical_id=evidence_id,
                artifact_type="note", author="command.recovery", body=canonical_bytes(accounting),
                media_type="application/json")
            if store.read_body(evidence["body_hash"]) != canonical_bytes(accounting):
                raise ValidationError("stopped attempt accounting changed")
            proof = {"schema_version": "development-dispatch-reconciliation-1", "binding": self.binding,
                "receipt": str(path), "receipt_sha256": receipt_sha256, "session_id": receipt["session_id"],
                "config_sha256": digest, "evidence": {evidence["artifact_ref"]: evidence["body_hash"]},
                "charged_usage": usage, "billing_completeness": "partial_unfinalized",
                "disposition": "paid_unknown_history_only"}
            logical = "command/development-dispatch-reconciliations/" + receipt_sha256
            record = store.head(logical) or store.publish_artifact(logical_id=logical, artifact_type="note",
                author="command.recovery", body=canonical_bytes(proof), media_type="application/json",
                inputs=[{"ref": evidence["artifact_ref"], "purpose": "subject"}])
            if store.read_body(record["body_hash"]) != canonical_bytes(proof):
                raise ValidationError("stopped attempt reconciliation changed")
            self.state["calls"].append({"receipt": str(path), "sha256": receipt_sha256,
                "usage": usage, "reconciliation_ref": record["artifact_ref"]})
            self.state["active_job"] = None
            self.save()
            return record

    def bind_recovered_backend(self, config, *, expected_state_sha256, binding_ref):
        """Append a sealed transport revision while preserving prior configuration ownership."""
        with (self.root / "owner.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = self.path.read_bytes()
            state = json.loads(raw)
            if (hashlib.sha256(raw).hexdigest() != expected_state_sha256
                    or state.get("binding") != self.binding or state.get("active_job")
                    or not state.get("calls")):
                raise ValidationError("backend recovery requires its exact settled session")
            self._verify_backend_lineage(state)
            digest = hashlib.sha256(canonical_bytes(config)).hexdigest()
            reference = {"path": str(Path(binding_ref).resolve()),
                "sha256": hashlib.sha256(Path(binding_ref).read_bytes()).hexdigest()}
            state.setdefault("backend_revisions", []).append(reference)
            state["config_sha256"] = digest
            self._verify_backend_lineage(state)
            self.state = state
            self.save()

    def _verify_backend_lineage(self, state):
        initial = deepcopy(state)
        revisions = state.get("backend_revisions", [])
        if revisions:
            first = json.loads(Path(revisions[0]["path"]).read_bytes())
            initial["config_sha256"] = first["previous_config_sha256"]
        if initial.get("pre_dispatch_binding"):
            self._verify_unused_backend_binding(initial)
        digest = initial["config_sha256"]
        allowed = {digest: 0}
        for reference in revisions:
            path = Path(reference["path"]).resolve()
            raw = path.read_bytes()
            proof = json.loads(raw)
            snapshot = Path(proof["previous_session"])
            prior_raw = snapshot.read_bytes()
            prior = json.loads(prior_raw)
            next_digest = hashlib.sha256(canonical_bytes(proof["backend"])).hexdigest()
            old_backend = proof["previous_backend"]
            if (hashlib.sha256(canonical_bytes(old_backend)).hexdigest() != digest
                    or any(old_backend.get(key) != proof["backend"].get(key) for key in
                           set(old_backend) | set(proof["backend"])
                           if key not in {"composition", "read_roots", "pinned_files"})):
                raise ValidationError("backend recovery may revise transport configuration only")
            boundary = len(prior["calls"])
            if (not path.is_relative_to(Path(self.binding["project_id"]))
                    or hashlib.sha256(raw).hexdigest() != reference["sha256"]
                    or proof.get("schema_version") != "recovered-development-backend-1"
                    or proof.get("development_session") != self.binding
                    or proof.get("session_id") != state["session_id"]
                    or proof.get("previous_config_sha256") != digest
                    or proof.get("backend_sha256") != next_digest or next_digest in allowed
                    or not snapshot.resolve().is_relative_to(path.parent)
                    or hashlib.sha256(prior_raw).hexdigest() != proof["previous_session_sha256"]
                    or prior.get("binding") != self.binding or prior.get("session_id") != state["session_id"]
                    or prior.get("config_sha256") != digest or prior.get("active_job") is not None
                    or prior.get("calls") != state["calls"][:boundary]
                    or prior.get("backend_revisions", []) != revisions[:len(allowed)-1]):
                raise ValidationError("development backend revision lost its exact settled lineage")
            allowed[next_digest] = boundary
            digest = next_digest
        if digest != state["config_sha256"]:
            raise ValidationError("development backend revision differs from current configuration")
        return allowed

    @property
    def read_roots(self):
        return [str(Path(row["receipt"]).parent) for row in self.state["calls"]]

    @property
    def transport_history(self):
        return [json.loads(Path(row["receipt"]).read_bytes()) for row in self.state["calls"]]

    def bind_job(self, receipt):
        self.state["active_job"] = str(receipt)
        self.save()

    def settle(self, receipt):
        if str(receipt) != self.state.get("active_job"):
            raise ValidationError("development session settlement lost its dispatch owner")
        body = Path(receipt).read_bytes()
        value = json.loads(body)
        owner = value.get("development_session", {})
        if (value.get("job_directory") != str(Path(receipt).resolve().parent)
                or owner.get("binding") != self.binding
                or owner.get("prior_receipts") != self.state["calls"]):
            raise ValidationError("development session settlement has a foreign receipt owner")
        if value.get("status") == "not_dispatched":
            self.state["active_job"] = None
        elif (value.get("session_id") == self.state["session_id"] and value.get("process_reaped")
                and (value.get("status") == "completed"
                     or value.get("status") == "failed" and value.get("outcome_known") is True)):
            self.state["calls"].append({"receipt": str(receipt),
                "sha256": hashlib.sha256(body).hexdigest(), "usage": value["usage"]})
            self.state["active_job"] = None
        self.save()
