"""Offline mission ownership, tool feedback, accounting and acceptance boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.runtime.composer import ComposerRunner
from scisaurus.runtime.development_session import development_session_binding, DevelopmentSession
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.dsh_batch import DshBatchError
from scisaurus.runtime.mission_research import MissionResearchSession, ResearcherSubmittedCandidate
from scisaurus.runtime.model_work import ModelWorkBlocked, ModelWorkCache
from scisaurus.runtime.models import ModelResult
from scisaurus.tests import test_experiment_review_handoff as fixtures


class MissionResearchTests(unittest.TestCase):
    def runner(self, root):
        c = fixtures.ExperimentReviewHandoffTests().stopped(root)
        self.addCleanup(c.close)
        c.laboratory_binding = object()
        directory = c.root/'development-session'; directory.mkdir()
        (directory/'session.json').write_text(json.dumps({'session_id':'retained-session','binding':development_session_binding(c.root),
            'calls':[], 'active_job':None}))
        with patch.object(c,'_software_author_backend_config',return_value={'author_backend':{}}):
            c.set_research_process('session_led')
        return c

    def authorize(self,c):
        return c.authorize_research_resume({'source':'externally confirmed account capacity',
            'observed_at':'2026-10-11T04:00:00Z','reconciliation_refs':[]})

    def test_registration_preserves_question_usage_frontier_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp)); retained=(deepcopy(c.context),deepcopy(c.usage),deepcopy(c.workflow),set(c.continuation_pending_stage_ids))
            session=(c.root/'development-session/session.json').read_bytes()
            with patch.object(c,'_software_author_backend_config',return_value={'author_backend':{}}):
                first=c.set_research_process('session_led'); second=c.set_research_process('session_led')
            self.assertEqual(first,second)
            self.assertEqual(retained,(c.context,c.usage,c.workflow,c.continuation_pending_stage_ids))
            self.assertEqual(session,(c.root/'development-session/session.json').read_bytes())
            c.close(); resumed=ComposerRunner(c.workflow,resume=True,control_only=True); self.addCleanup(resumed.close)
            self.assertEqual(first,resumed._research_process_policy())
            self.assertEqual(retained[0],resumed.context)

    def test_no_recovery_means_no_primary_stage_solver_or_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp)); s=MissionResearchSession(c)
            with patch('scisaurus.runtime.mission_research.MissionResearchClient') as primary, \
                 patch.object(c,'_run_stage') as legacy, \
                 patch('scisaurus.runtime.experiment.ExperimentRunner.run') as solver:
                with self.assertRaises(StateError):s.guard()
                with self.assertRaises(StateError):s.operation({'operation':'check_environment','arguments':{}})
                primary.assert_not_called();legacy.assert_not_called();solver.assert_not_called()

    def test_mission_routing_bypasses_stage_committee(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp))
            with patch.object(MissionResearchSession,'run',return_value={'status':'paused'}) as primary, patch.object(c,'_run_stage') as legacy:
                self.assertEqual(c._run_authorized(),{'status':'paused'})
                primary.assert_called_once();legacy.assert_not_called()

    def test_policy_rejects_mutated_question_owner_or_missing_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp)); original=deepcopy(c.context)
            c.context['topic']['topic']['research_question']='another question'
            with self.assertRaises(StateError):c._research_process_policy()
            c.context=original
            state=c.root/'development-session/session.json'; v=json.loads(state.read_bytes()); v['session_id']='other';state.write_text(json.dumps(v))
            with self.assertRaises(StateError):c._research_process_policy()
            v['session_id']='retained-session';state.write_text(json.dumps(v))
            path=c.root/'output/run-control.json';v=json.loads(path.read_bytes());v.pop('research_process_policy_ref');path.write_text(json.dumps(v))
            with self.assertRaises(StateError):c._research_process_policy()

    def test_authorization_is_external_and_revoked_on_new_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c)
            s.guard();s._revoke_resume()
            with self.assertRaises(StateError):s.guard()
            with self.assertRaises(ValidationError):c.authorize_research_resume({'source':'','observed_at':'x','reconciliation_refs':[]})

    def test_usage_is_monotonic_owner_scoped_and_flattens_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));s=MissionResearchSession(c);initial=deepcopy(c.usage)
            s._charge('primary',{'model_calls':3,'input_tokens':100})
            s._charge('primary',{'model_calls':3,'input_tokens':100})
            s._charge('primary',{'model_calls':4,'input_tokens':120})
            s._charge('evaluation:a',{'cumulative_usage':{'model_calls':2,'input_tokens':80},'revision':5})
            self.assertEqual(c.usage['model_calls']-initial.get('model_calls',0),6)
            self.assertEqual(c.usage['input_tokens']-initial.get('input_tokens',0),200)
            self.assertNotIn('revision',c.usage)

    def test_submitted_candidate_is_single_use_and_no_new_paid_author(self):
        candidate={'executor_source':'complete source','experiment_intent':{'research_question':fixtures.QUESTION}}
        client=ResearcherSubmittedCandidate(candidate,model='fixed',submission_ref='artifact:source@1',max_output_tokens=32768)
        candidate['executor_source']='mutated'
        result=client.complete(system='',prompt='')
        self.assertEqual(result.json_object()['executor_source'],'complete source')
        self.assertEqual(result.usage,{})
        self.assertEqual(result.request_attempts,0)
        with self.assertRaises(ModelWorkBlocked):client.complete(system='',prompt='')

    def frozen(self,c):
        assignment={'capability_brief':'bounded study','configured_input':{'frozen':True},'required_intent_fields':{'research_question':fixtures.QUESTION}}
        cache=ModelWorkCache(c.store,c._publish,namespace='command/foundry-work');record=cache.put('test',{'assignment':assignment,'status':'blocked'})
        return record['cache_ref'],assignment

    def test_frozen_assignment_accepts_actual_owner_and_rejects_old_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));s=MissionResearchSession(c);ref,assignment=self.frozen(c)
            self.assertEqual(s._frozen_assignment(ref)[1],assignment)
            cache=ModelWorkCache(c.store,c._publish,namespace='command/foundry-work');cache.put('test',{'assignment':assignment,'status':'pending'})
            with self.assertRaises(StateError):s._frozen_assignment(ref)

    def test_tools_keep_researcher_computation_and_explicit_independent_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));s=MissionResearchSession(c);tools=s.tools()['actions']
            self.assertIn('run',tools);self.assertIn('evaluate_pilot',tools);self.assertIn('record_checkpoint',tools)
            self.assertNotIn('choose_topic',tools)

    def test_known_rejection_is_feedback_unknown_stays_pending_and_revokes(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c)
            action={'operation':'evaluate_pilot','arguments':{'source':'model-owned'}}
            known=ModelWorkBlocked('scientific review rejected candidate');known.repair_feedback={'gate':'adversarial_review','checks':[]}
            with patch.object(s,'_evaluate',side_effect=known),patch.object(c,'_sync_foundry_usage'):
                observation=s.operation(action)
            self.assertEqual(observation['outcome'],'failed');self.assertIsNone(s.state['pending_evaluation'])
            unknown=DshBatchError('weekly quota',receipt='/unavailable/receipt',usage={'model_calls':39})
            with patch.object(s,'_evaluate',side_effect=unknown),patch.object(c,'_sync_foundry_usage'):
                with self.assertRaises(DshBatchError):s.operation({'operation':'evaluate_pilot','arguments':{'source':'revised'}})
            self.assertTrue(s.state['pending_evaluation'])
            with self.assertRaises(StateError):s.guard()
            self.assertEqual(c.usage.get('model_calls',0),0)

    def test_workbench_reuse_revalidates_instead_of_replaying_outer_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c);s.workbench=Mock()
            s.workbench.execute.return_value={'receipt_ref':'software:sha256:current','result':{'checked':True}}
            action={'operation':'inspect_runtime','arguments':{'runtime':'python'}}
            s.operation(action);s.operation(action)
            self.assertEqual(s.workbench.execute.call_count,2)

    def test_unknown_computation_holds_expansion_and_revokes_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c);s.workbench=Mock()
            s.workbench.execute.return_value={'outcome':'result_unknown','receipt_ref':'software:unknown','usage':{}}
            with self.assertRaises(DshBatchError):
                s.operation({'operation':'run','arguments':{'purpose':'scientific_computation'}})
            self.assertTrue(s.state['pending_evaluation'])
            self.assertIsNone(json.loads((c.root/'output/run-control.json').read_bytes()).get('research_resume_authorization_ref'))
            self.assertEqual(s._read_head(s.state['pending_evaluation'])['status'],'result_unknown')

    def test_primary_unknown_requires_exact_paid_checkpoint_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));s=MissionResearchSession(c)
            config={'model':'fixed','max_output_tokens':32768}
            binding=development_session_binding(c.root)
            path=s.root/'jobs/unknown/receipt.json';path.parent.mkdir(parents=True)
            inputs=path.parent/'input';inputs.mkdir()
            assignment={'schema_version':'mission-research-assignment-1','workflow_id':c.workflow['id'],
                        'research_process':s.policy}
            (inputs/'assignment.json').write_bytes(canonical_bytes(assignment))
            config_sha=hashlib.sha256(canonical_bytes(config)).hexdigest()
            usage={'model_calls':2,'input_tokens':100,'output_tokens':60}
            receipt={'status':'result_unknown','outcome_known':False,'process_reaped':True,'pid':987654321,
                'job_directory':str(path.parent),'session_id':'retained-session','config_sha256':config_sha,
                'development_session':{'binding':binding,'prior_receipts':[]},'usage':usage,
                'input_sha256':{'assignment.json':hashlib.sha256((inputs/'assignment.json').read_bytes()).hexdigest()}}
            path.write_bytes(canonical_bytes(receipt));original=path.read_bytes();receipt_sha=hashlib.sha256(original).hexdigest()
            state={'schema_version':'dsh-development-session-1','session_id':'retained-session','binding':binding,
                'config_sha256':config_sha,'calls':[],'active_job':str(path)}
            (c.root/'development-session/session.json').write_bytes(canonical_bytes(state))
            development=DevelopmentSession(binding)
            checkpoint=c.store.head(f'command/composer/checkpoints/{len(c.feedback)+len(c.stage_records)+1}')
            with self.assertRaisesRegex(ValidationError,'paid ownership'):
                development.reconcile_accounted_unknown(config,c.store,checkpoint_ref=checkpoint['artifact_ref'],
                    policy_ref=s.policy['artifact_ref'],receipt_sha256=receipt_sha)
            s._charge(str(path),usage);c._checkpoint_stopped_control('offline:paid_transport')
            checkpoint=c.store.head(f'command/composer/checkpoints/{len(c.feedback)+len(c.stage_records)+1}')
            arguments={'checkpoint_ref':checkpoint['artifact_ref'],'policy_ref':s.policy['artifact_ref'],'receipt_sha256':receipt_sha}
            with patch('scisaurus.runtime.development_session.os.kill',return_value=None):
                with self.assertRaisesRegex(ValidationError,'still alive'):
                    development.reconcile_accounted_unknown(config,c.store,**arguments)
            with patch('scisaurus.runtime.development_session.os.kill',side_effect=ProcessLookupError):
                first=development.reconcile_accounted_unknown(config,c.store,**arguments)
                second=development.reconcile_accounted_unknown(config,c.store,**arguments)
            self.assertEqual(first['artifact_ref'],second['artifact_ref'])
            self.assertEqual(path.read_bytes(),original)
            with development.lease(config,time.monotonic()+1):
                self.assertIsNone(development.state['active_job'])
                self.assertEqual(len(development.state['calls']),1)
                self.assertEqual(development.transport_history[0]['status'],'result_unknown')
            self.assertEqual({key:c.usage[key] for key in usage},usage)
            with self.assertRaises(StateError):s.guard()

    def test_known_experiment_rejection_reuses_feedback_without_unknown_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c)
            feedback={'outcome':'failed','result':{'status':'research_expansion_required'},'artifact_ref':'current-rejection'}
            action={'operation':'evaluate_pilot','arguments':{'candidate':'same'}}
            with patch.object(s,'_evaluate',return_value=feedback) as evaluate,patch.object(s,'_validated_result') as validate:
                self.assertEqual(s.operation(action),feedback)
                self.assertEqual(s.operation(action),feedback)
                evaluate.assert_called_once();validate.assert_not_called()
            self.assertIsNone(s.state['pending_evaluation']);s.guard()

    def test_first_turn_controller_failure_records_explicit_unknown(self):
        from scisaurus.tests.test_dsh_batch import BatchTests
        from scisaurus.runtime.dsh_batch import DshBatchRunner
        fixture=BatchTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        with self.assertRaises(DshBatchError) as raised:
            DshBatchRunner(fixture.config('missing'),root=fixture.root/'jobs').run(
                'offline missing output',inputs={'assignment.json':'{}'},outputs=['answer.txt'],exchange=lambda files,usage:None)
        receipt=json.loads(Path(raised.exception.receipt).read_bytes())
        self.assertEqual(receipt['status'],'result_unknown')
        self.assertIs(receipt['outcome_known'],False)
        self.assertTrue(receipt['process_reaped'])
        self.assertEqual(receipt['usage']['model_calls'],1)

    def test_structured_dispatch_honors_outer_watchdog(self):
        from scisaurus.tests.test_dsh_batch import BatchTests
        from scisaurus.runtime.dsh_batch import DshStructuredProducerClient
        fixture=BatchTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        client=DshStructuredProducerClient(fixture.config(),root=fixture.root/'jobs',runtime_python=__import__('sys').executable)
        client.deadline=time.monotonic()+.2
        with patch.object(client.runner,'run',return_value={'files':{'response.json':b'{"verdict":"hold"}'},
            'usage':{},'elapsed_seconds':0,'receipt':'offline-receipt'}) as run:
            client.complete(system='offline',prompt='{}')
        self.assertLessEqual(run.call_args.kwargs['deadline'],client.deadline)

    def test_support_allocation_remains_active_across_research_checkpoints(self):
        from scisaurus.runtime.mission_time import MissionTimeLedger
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c)
            c.mission_time_ledger=MissionTimeLedger({'mode':'unbounded','support_seconds':1,'design_fraction_target':.95},clock=time.monotonic)
            c.mission_time_policy_ref='offline-policy'
            c.status='running';c.mission_time_ledger.switch('survey');s.save('note_recorded')
            self.assertEqual(c.mission_time_ledger.active_kind,'survey')
            c.mission_time_ledger.used['support']=1
            with self.assertRaisesRegex(ModelWorkBlocked,'support allocation'):s.guard()

    def test_optional_worker_setting_keeps_logical_capacity_and_caps_actual_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c);s.deadline=time.monotonic()+120
            ref,assignment=self.frozen(c)
            stage=next(stage for stage in c.workflow['stages'] if stage['id']=='experiment')
            config=json.loads(Path(stage['config_path']).read_bytes())
            config['limits']={'concurrent_calls':3,'wall_clock_seconds':120}
            config['experiment']={'literature_gate':{}}
            config['limits'].pop('worker_concurrency',None)
            Path(stage['config_path']).write_bytes(canonical_bytes(config))
            descriptor=Path(tmp)/'descriptor.json';descriptor.write_bytes(canonical_bytes({'experiment':config['experiment']}))
            foundry=Mock();foundry.generate.return_value={'registration':{'descriptor_path':str(descriptor)},'admission':{}}
            backend={'author_backend':{'model':'fixed','max_output_tokens':32768}}
            args={'assignment_ref':ref,'candidate':{'executor_source':'current','experiment_intent':{'research_question':fixtures.QUESTION}},'external_review_dispositions':[]}
            with patch.object(s,'_frozen_assignment',return_value=(stage,assignment)),patch.object(s,'_adjudicate_reviews'), \
                 patch.object(s,'_foundry',return_value=(backend,foundry)),patch.object(c,'_apply_bindings',side_effect=lambda evaluation,bindings:evaluation), \
                 patch.object(c,'_sync_foundry_usage'),patch('scisaurus.runtime.experiment.ExperimentRunner') as runner:
                runner.return_value.run.return_value={'status':'research_expansion_required','usage':{'cumulative_usage':{'model_calls':1}}}
                observation=s._evaluate(args,'offline')
            actual=runner.call_args.args[1]['limits']
            self.assertEqual(actual['concurrent_calls'],config['limits']['concurrent_calls'])
            self.assertGreaterEqual(actual['concurrent_calls'],3)
            self.assertLessEqual(actual['worker_concurrency'],2)
            self.assertEqual(observation['outcome'],'failed')
            self.assertEqual(c.usage['model_calls'],1)

    def test_primary_receipt_cannot_charge_independent_error_as_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));s=MissionResearchSession(c)
            foreign=c.root/'independent/receipt.json';foreign.parent.mkdir();foreign.write_text('{"usage":{"model_calls":39}}')
            with patch.object(s,'_primary_receipt',return_value=str(foreign)):s._charge_primary_receipt()
            self.assertEqual(c.usage.get('model_calls',0),0)

    def test_final_requires_current_evidence_even_with_primary_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp));self.authorize(c);s=MissionResearchSession(c)
            with self.assertRaises(ModelWorkBlocked):s.propose_final({'result_ref':'artifact:fake@1','summary':'It succeeded','limitations':[]})
            self.assertIsNone(s.final)

    def test_full_primary_tool_loop_final_requires_fresh_separate_scope_verdict(self):
        for accepted in (False,True):
            with self.subTest(accepted=accepted),tempfile.TemporaryDirectory() as tmp:
                c=self.runner(Path(tmp));self.authorize(c)
                prior=deepcopy(c.context)
                for stage_id in c.workflow['completion']['required_stage_ids']:
                    if stage_id!='experiment':c.stage_records.setdefault(stage_id,{})['status']='completed'
                evaluated=c._publish('command/research-session/results/offline','decision_note',{'offline':True},'command.research-session')
                final={'result_ref':evaluated['artifact_ref'],'summary':'Current source and observations resolve the stated question.', 'limitations':['Only the declared model scope.']}
                body={'stage_id':'experiment','result':{'status':'completed','usage':{}},
                      'foundry':{'candidate':{'executor_source':'complete current source','experiment_intent':{'research_question':fixtures.QUESTION}}}}
                s=MissionResearchSession(c);s.state['result_ref']=evaluated['artifact_ref']
                independent_calls=[]
                class Independent:
                    def __init__(self,*args,**kwargs):
                        independent_calls.append(kwargs)
                        self.assert_boundary=kwargs
                    def complete(self,*,system,prompt):
                        assignment=json.loads(prompt)
                        verdict={'decision':'accepted' if accepted else 'hold','original_question_resolved':accepted,
                            'evidence_sha256':assignment['evidence_sha256'],'rationale':'Declared comparison has current evidence.' if accepted else 'The pilot does not resolve the original question.',
                            'limitations':['Declared physical model only.']}
                        return ModelResult(json.dumps(verdict),'independent',{'model_calls':1,'input_tokens':35},0,'stop',response_metadata={'receipt':'offline-independent'})
                class Primary:
                    def __init__(self,*args,**kwargs):self.options=kwargs
                    def complete(self,*,system,prompt):
                        assignment=json.loads(prompt)
                        changed=self.options['tool_exchange']({'tool_action':{'operation':'record_checkpoint','arguments':{'phase':'experiment','note':'Current interpretation follows the recorded pilot.','evidence_refs':[]}}},assignment,{'model_calls':1,'input_tokens':50})
                        feedback=self.options['final_exchange']({'final':final},changed,{'model_calls':2,'input_tokens':80})
                        if accepted:
                            if feedback is not None:raise AssertionError(str(feedback))
                        else:
                            if not feedback or not feedback.get('final_feedback'):raise AssertionError('limited result incorrectly completed')
                        return ModelResult(json.dumps({'final':final}),'primary',{'model_calls':2,'input_tokens':80},0,'stop',response_metadata={'receipt':'None'})
                with patch.object(c,'_software_author_backend_config',return_value={'author_backend':{'timeout_seconds':120},'runtime_python':'/offline/python'}), \
                     patch.object(s,'_bind_tools'),patch.object(s,'assignment',return_value={'checkpoint':{},'scientific_software_tools':s.tools()}), \
                     patch.object(s,'_validated_result',return_value=(body,{'question':fixtures.QUESTION}, {'observations':[{'measured':1}]})), \
                     patch('scisaurus.runtime.mission_research.MissionResearchClient',Primary), \
                     patch('scisaurus.runtime.mission_research.DshStructuredProducerClient',Independent), \
                     patch.object(c,'_finish',side_effect=lambda:{'status':c.status}),patch.object(c,'_settle_stage_work_orders'):
                    result=s.run()
                self.assertEqual(len(independent_calls),1,c.blockers)
                self.assertNotIn('development_session',independent_calls[0])
                self.assertNotIn('receipt_inputs',independent_calls[0])
                self.assertEqual(c.usage.get('model_calls'),3)
                self.assertEqual(c.context['topic'],prior['topic'])
                if accepted:
                    self.assertEqual(result['status'],'completed')
                    self.assertEqual(c.stage_records['experiment']['status'],'completed')
                    self.assertTrue(c.context['experiment']['prior_context_ref'])
                    self.assertNotIn('experiment',c.continuation_pending_stage_ids)
                else:
                    self.assertEqual(result['status'],'paused')
                    self.assertEqual(c.context,prior)
                    self.assertIn('experiment',c.continuation_pending_stage_ids)

    def test_frontier_unknown_blocks_recovery_even_with_external_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp))
            cache=ModelWorkCache(c.store,c._publish,namespace='command/foundry-work')
            cache.put('unknown',{'status':'blocked','usage':{'model_calls':39},'requests':[{'status':'result_unknown','role':'methods.validator-author'}]})
            with self.assertRaises(StateError):self.authorize(c)
            self.assertFalse(json.loads((c.root/'output/run-control.json').read_bytes()).get('research_resume_authorization_ref'))

    def test_recovery_rejects_future_time_and_foreign_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.runner(Path(tmp))
            with self.assertRaises(ValidationError):c.authorize_research_resume({'source':'account status','observed_at':'2999-01-01T00:00:00Z','reconciliation_refs':[]})
            foreign=c._publish('command/foreign-proof','decision_note',{},'command.composer')
            with self.assertRaises(StateError):c.authorize_research_resume({'source':'account status','observed_at':'2026-01-01T00:00:00Z','reconciliation_refs':[foreign['artifact_ref']]})

    def test_submitted_path_keeps_independent_foundry_gates_without_author_dispatch(self):
        from scisaurus.tests.test_capability_foundry import CapabilityFoundryTests
        with tempfile.TemporaryDirectory() as tmp:
            foundry=CapabilityFoundryTests._foundry(Path(tmp));foundry.max_attempts=1
            foundry.author_backend={'schema_version':'dsh-batch-1','model':'fixed'}
            payload=CapabilityFoundryTests._payload()
            candidate={k:payload[k] for k in ('executor_source','experiment_intent')}
            client=ResearcherSubmittedCandidate(candidate,model='fixed',submission_ref='artifact:submitted@1',max_output_tokens=32768)
            with patch('scisaurus.runtime.dsh_batch.DshAuthorClient',side_effect=AssertionError('hidden author dispatch')):
                result=foundry.generate('bounded comparison',submitted_candidate=client)
            self.assertTrue(client.used)
            self.assertEqual(result['status'],'registered')
            self.assertGreater(foundry.validator_client.calls,0)
            self.assertGreater(foundry.reviewer_client.calls,0)


if __name__=='__main__':unittest.main()
