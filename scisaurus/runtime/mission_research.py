"""A persistent researcher selects tools; the controller records their evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from scisaurus.core.errors import StateError, ValidationError, ProviderRateLimitError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.development_session import development_session_binding
from scisaurus.runtime.dsh_batch import DshBatchError
from scisaurus.runtime.primary_client import PrimaryClient as MissionResearchClient,prepare_primary_backend
from scisaurus.runtime.model_work import ModelWorkBlocked
from scisaurus.runtime.run_control import ensure_run_allowed
from scisaurus.runtime.software_workbench import SoftwareWorkbench


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def final_audit_recovery_evidence(composer, body, proof):
    """Bind accounting recovery to the actual isolated audit dispatch."""
    receipt_ref = proof.get("receipt")
    if not isinstance(receipt_ref,str) or not receipt_ref.strip():
        raise StateError("final audit recovery requires its actual receipt and usage")
    identity=body.get('request_identity',digest(body['assignment']))
    family = (composer.root/'research-session'/('reviews' if body.get('kind')=='requested_review' else 'independent-final')/identity).resolve()
    root = Path(body.get("dispatch_root") or family).resolve()
    path = Path(receipt_ref).resolve()
    if not root.is_relative_to(family) or not path.is_relative_to(root) or path.name!="receipt.json":
        raise StateError("final audit receipt is outside its owned dispatch")
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw)
        backend = composer._software_author_backend_config()
        usage = receipt["usage"]
        if (proof.get("receipt_sha256")!=hashlib.sha256(raw).hexdigest()
                or receipt.get("schema_version")!="dsh-batch-receipt-1"
                or receipt.get("status") not in {"result_unknown","completed","not_dispatched"}
                or backend is None
                or receipt.get("config_sha256")!=digest(body.get('backend') or backend["author_backend"])
                or not isinstance(usage,dict)
                or not {"model_calls","input_tokens","output_tokens"}.issubset(usage)
                or any(type(value) is not int or value<0 for value in usage.values())):
            raise StateError("final audit receipt has invalid identity or paid accounting")
        if receipt["status"]=="not_dispatched":
            if usage["model_calls"]!=0 or receipt.get("pid") is not None:
                raise StateError("undispatched final audit has contradictory execution evidence")
        else:
            if (receipt.get("process_reaped") is not True
                    or Path(receipt["job_directory"]).resolve()!=path.parent):
                raise StateError("final audit dispatch is not reaped under its receipt owner")
            captured = path.parent/"input/assignment.json"
            assignment_raw = captured.read_bytes()
            assignment = json.loads(assignment_raw)
            if (receipt.get("input_sha256",{}).get("assignment.json")!=hashlib.sha256(assignment_raw).hexdigest()
                    or assignment.pop("runtime_python",None)!=str(Path(backend["runtime_python"]).absolute())
                    or assignment!=body["assignment"]):
                raise StateError("final audit receipt differs from its frozen assignment")
        if type(receipt.get("pid")) is int:
            import os
            try:os.kill(receipt["pid"],0)
            except ProcessLookupError:pass
            else:raise StateError("final audit dispatch is still alive")
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise StateError("final audit recovery has incomplete receipt evidence") from error
    return {"receipt":str(path),"receipt_sha256":hashlib.sha256(raw).hexdigest(),"usage":deepcopy(usage)}


class ResearcherSubmittedCandidate:
    """An already paid researcher output, not a new author model request."""
    def __init__(self, candidate, *, model, submission_ref, max_output_tokens):
        self.candidate = deepcopy(candidate)
        self.model = model
        self.submission_ref = submission_ref
        self.max_output_tokens = max_output_tokens
        self.used = False

    def complete(self, *, system, prompt):
        from scisaurus.runtime.models import ModelResult
        if self.used:
            raise ModelWorkBlocked("candidate admission returned feedback; the researcher must submit a revision")
        self.used = True
        return ModelResult(json.dumps(self.candidate), self.model, {}, 0, "stop", 0,
            response_metadata={"backend": "research-session-submission", "submission_ref": self.submission_ref})


class MissionResearchSession:
    def __init__(self, composer):
        self.composer = composer
        self.policy = composer._research_process_policy()
        self.root = composer.root / "research-session"
        self.root.mkdir(parents=True, exist_ok=True)
        self.logical = "command/research-session/state"
        self.state = self._read_head(self.logical) or {
            "schema_version": "mission-research-state-1", "workflow_id": composer.workflow["id"],
            "policy": self.policy, "pending_evaluation": None, "result_ref": None, "tool_refs": [], "note_refs": []}
        if self.state.get("workflow_id") != composer.workflow["id"] or self.state.get("policy") != self.policy:
            raise StateError("research session checkpoint belongs to another workflow or policy")
        self.deadline = None
        self.workbench = None
        self.final = None

    def _read_head(self, logical):
        head = self.composer.store.head(logical)
        if head is None:
            return None
        return self.composer._read_verified_artifact_json(head["artifact_ref"])[2]

    def _publish(self, logical, body, *, subjects=()):
        return self.composer._publish(logical, "decision_note", body,
                                      "command.research-session", subjects=list(subjects))

    def save(self, phase):
        self._publish(self.logical, self.state, subjects=[self.policy["artifact_ref"]])
        self.composer._checkpoint("research:" + phase, force=True)

    def _primary_receipt(self):
        state = self.composer._read_json_object(self.composer.root/"development-session/session.json") or {}
        return state.get("active_job") or (state.get("calls") or [{}])[-1].get("receipt")

    def _charge(self, owner, usage):
        c = self.composer
        key = "research-session:" + owner
        previous = c.stage_usage_totals.get(key,{})
        usage = usage.get("cumulative_usage", usage)
        actual = {k:v for k,v in usage.items() if type(v) is int and v>=0}
        for k,v in actual.items():
            c.usage[k] = c.usage.get(k,0) + max(0,v-previous.get(k,0))
        c.stage_usage_totals[key] = {k:max(v,previous.get(k,0)) for k,v in actual.items()} | {
            k:v for k,v in previous.items() if k not in actual}

    def _revoke_resume(self):
        from scisaurus.runtime.run_control import save_control
        c = self.composer
        control = c._read_json_object(c.root/"output/run-control.json") or {}
        control.pop("research_resume_authorization_ref", None)
        save_control(c.root/"output", control)

    def _bind_tools(self):
        from scisaurus.runtime.software_history import engineering_history
        from scisaurus.runtime.software_discovery import accepted_survey_sources,retain_sources
        c = self.composer
        sources,histories = [],[]
        for stage in c.workflow["stages"]:
            if stage["kind"]=="survey":
                context = c.context.get(stage["id"],{})
                if context.get("project_dir") and context.get("survey_ref"):
                    topic = c._topic_context_for_stage(stage,ancestor_ids=c._stage_ancestor_ids(stage))
                    if topic:
                        sources.extend(accepted_survey_sources(context["project_dir"],survey_ref=context["survey_ref"],
                            question=topic[1]["topic"]["research_question"])["sources"])
            if stage["kind"]=="experiment":
                topic = c._topic_context_for_stage(stage,ancestor_ids=c._stage_ancestor_ids(stage))
                if topic:
                    histories.append(engineering_history(c.store,c._read_verified_artifact_json,self.workbench,
                        project_id=c.workflow["project_id"],stage_id=stage["id"],topic=topic[1]["topic"]))
        catalog = retain_sources(c.root/"scientific-software",sources)
        self.workbench.evidence_refs = frozenset(x["source_ref"] for x in catalog)
        from scisaurus.runtime.primary_work import primary_observations
        development=c._read_json_object(c.root/'development-session/session.json') or {}
        observed=primary_observations(development)
        observations=[*self.state.get('observations',[]),*observed]
        self.state['observations']=list({x.get('receipt_ref') or (index,):x for index,x in enumerate(observations)}.values())
        self.workbench.history_refs = frozenset([*(x['receipt_ref'] for h in histories for x in h['operations']),
            *(x['receipt_ref'] for x in observations if x.get('receipt_ref'))])
        self.state["evidence_catalog"] = catalog
        self.state["engineering_history"] = histories

    def _charge_primary_receipt(self):
        receipt = self._primary_receipt()
        if receipt:
            path = Path(receipt).resolve()
            if path.is_relative_to(self.root/"jobs") and path.is_file():
                self._charge(str(path),json.loads(path.read_bytes()).get("usage",{}))

    def guard(self):
        self.composer._require_research_resume_authorized()
        self.composer._require_settled_research_operations()
        ensure_run_allowed()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ModelWorkBlocked('primary dispatch watchdog expired')

    def tools(self):
        from scisaurus.runtime.primary_work import work_assignment
        tools=work_assignment({}, {'kind':'research_work','checkpoint':{}})['scientific_software_tools']
        tools['actions'].update({
            'inspect_checkpoint':{},
            'read_artifact':{'artifact_ref':'retained artifact reference'},
            'record_checkpoint':{'note':'free-form research notes','evidence_refs':[]},
            'request_review':{'task':'independent review request','evidence':'current explicit evidence'},
            'finish':{'summary':'primary conclusions and limitations'},
        })
        return tools

    def assignment(self):
        from scisaurus.runtime.primary_work import work_assignment
        c=self.composer
        original={'objective':c.workflow['objective'],'research_process':self.policy,
            'retained_workflow':deepcopy(c.workflow),'retained_stage_evidence':deepcopy(c.context),
            'usage':deepcopy(c.usage),'available_open_source_tools':c.laboratory_binding.context()}
        result=work_assignment(original,{'kind':'research_work','checkpoint':deepcopy(self.state)})
        result['scientific_software_tools']=self.tools()
        return result

    def operation(self,action):
        self.guard()
        name,args=action['operation'],action['arguments']
        if name=='inspect_checkpoint':return {'outcome':'ok','checkpoint':self.assignment()}
        if name=='read_artifact':
            manifest,_,body=self.composer._read_verified_artifact_json(args['artifact_ref'])
            return {'outcome':'ok','manifest':manifest,'body':body}
        if name in {'record_checkpoint','finish'}:
            record=self._publish('command/research-session/notes/'+digest(args),
                {'policy':self.policy,'notes':deepcopy(args),'scientific_validation':'not_evaluated'})
            self.state['note_refs'].append(record['artifact_ref'])
            if name=='finish':self.state['primary_finished']=True
            self.save('checkpoint')
            return {'outcome':'ok','artifact_ref':record['artifact_ref'],'scientific_validation':'not_evaluated'}
        if name=='request_review':return self._review(args)
        from scisaurus.runtime.primary_work import native_tool_handler
        ledger=self.composer.mission_time_ledger
        if ledger is not None:
            ledger.switch('survey' if name in {'search','read','fetch_source','search_web','read_evidence','search_evidence'} else 'experiment')
        observation=native_tool_handler(self.assignment(),self.workbench,self.state)(action)
        record=self._publish('command/research-session/tools/'+digest(action),
            {'workflow_id':self.composer.workflow['id'],'policy':self.policy,'action':action,
             'status':'result_unknown' if observation.get('outcome')=='result_unknown' else 'completed',
             'observation':observation})
        self.state['tool_refs'].append(record['artifact_ref']);self.save('tool')
        return observation

    def _review(self,args):
        from scisaurus.runtime.requested_review import request_review,review_usage_owner
        self.guard();c=self.composer;configured=c._software_author_backend_config()
        if configured is None:raise StateError('independent review backend is unavailable')
        logical='command/research-session/final-reviews/'+digest(args)
        def record(dispatch):
            self._publish(logical,{**dispatch,'policy':self.policy})
            if dispatch.get('receipt'):
                self._charge(review_usage_owner(dispatch['receipt']),dispatch['usage'])
            self.save('review')
        return request_review(configured,self.root/'reviews',args,deadline=self.deadline,
            isolated_from=[c.root],record=record,laboratory=c.laboratory_binding)

    def run(self):
        c=self.composer
        try:
            self.guard();c._require_settled_research_frontier();c._require_settled_research_operations()
            configured=c._software_author_backend_config()
            if configured is None:raise StateError('primary backend is unavailable')
            self.deadline=time.monotonic()+min(c._remaining(),configured['author_backend']['timeout_seconds'])
            self.workbench=SoftwareWorkbench(c.root/'scientific-software',deadline=self.deadline,laboratory=c.laboratory_binding)
            self._bind_tools()
            backend=prepare_primary_backend(configured['author_backend'],self.root,configured['runtime_python'],development_session_binding(c.root))
            client=MissionResearchClient(backend,root=self.root/'jobs',runtime_python=configured['runtime_python'],
                development_session=development_session_binding(c.root))
            client.deadline=self.deadline
            client.runner.task_contract['native_controller_tools']=True
            client.runner.native_tool_handler=self.operation
            self.state['primary_finished']=False
            c.status='running';self.save('started')
            result=client.complete(system='The primary owns research choices and conclusions; tools provide actual results and feedback.',
                prompt=json.dumps(self.assignment()))
            self._charge(result.response_metadata['receipt'],result.usage)
            self.state['latest_reply']=result.json_object();self.state['primary_receipt']=result.response_metadata['receipt']
            c.status='research_finished' if self.state.get('primary_finished') else 'research_checkpoint'
            self.save('checkpoint')
        except Exception as error:
            self._charge_primary_receipt()
            if isinstance(error,(DshBatchError,ProviderRateLimitError)):self._revoke_resume()
            c.status='paused'
            failure=error.failure_details() if callable(getattr(error,'failure_details',None)) else None
            c.blockers.append({'stage_id':'workflow','reason':str(error),'stop_reason':'operational_state',
                'failure':failure,'receipt':str(getattr(error,'receipt',None) or self._primary_receipt())})
            self.save('paused')
        return c._finish()
