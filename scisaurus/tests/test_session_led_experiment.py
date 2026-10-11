"""Session-led development retains independent post-execution acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.runtime.composer import ComposerRunner
from scisaurus.runtime.dsh_batch import DshStructuredProducerClient
from scisaurus.runtime.specialists import build_repair_adjudication_prompt
from scisaurus.tests import test_experiment_review_handoff as fixtures

packet_and_report = fixtures.packet_and_report


class SessionLedExperimentTests(unittest.TestCase):
    def runner(self, root):
        runner = fixtures.ExperimentReviewHandoffTests().stopped(root)
        runner.laboratory_binding = object()
        with patch.object(runner, '_software_author_backend_config', return_value={'author_backend': {}}):
            runner.set_experiment_process('experiment', 'session_led')
        self.addCleanup(runner.close)
        return runner, runner.workflow['stages'][-1]

    def test_stopped_revision_is_idempotent_and_preserves_scientific_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            original, usage, workflow = deepcopy(runner.context), deepcopy(runner.usage), deepcopy(runner.workflow)
            with patch.object(runner, '_software_author_backend_config', return_value={'author_backend': {}}):
                first = runner.set_experiment_process('experiment', 'session_led')
                second = runner.set_experiment_process('experiment', 'session_led')
            self.assertEqual(first, second)
            self.assertEqual((runner.context, runner.usage, runner.workflow), (original, usage, workflow))
            runner.close()
            resumed = ComposerRunner(workflow, resume=True, control_only=True)
            self.addCleanup(resumed.close)
            self.assertTrue(resumed._session_led_experiment(stage))
            self.assertEqual(resumed.context, original)
            self.assertEqual(resumed.usage, usage)
            with self.assertRaises(StateError): resumed.run()

    def test_revision_rejects_invalid_mode_stage_and_missing_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = fixtures.ExperimentReviewHandoffTests().stopped(Path(tmp)); self.addCleanup(runner.close)
            with self.assertRaises(ValidationError): runner.set_experiment_process('experiment', 'automatic')
            with self.assertRaises(StateError): runner.set_experiment_process('topic', 'session_led')
            with self.assertRaises(StateError): runner.set_experiment_process('experiment', 'session_led')
            runner.control_only = False
            with self.assertRaises(StateError): runner.set_experiment_process('experiment', 'role_pool')

    def test_policy_cannot_be_removed_or_attached_to_a_changed_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            runner.context['topic']['topic']['research_question'] = 'Different scientific question'
            with self.assertRaises(StateError): runner._session_led_experiment(stage)
            runner.context['topic']['topic']['research_question'] = packet_and_report()[0]['topic']['research_question']
            control = runner.root/'output/run-control.json'
            value = json.loads(control.read_text()); value.pop('experiment_process_policy_refs'); control.write_text(json.dumps(value))
            with self.assertRaises(StateError): runner._session_led_experiment(stage)

    def panel(self, runner, stage, *, failure=None):
        packet, report = packet_and_report()
        packet.update(input_sha256='c'*64, science_input_sha256='d'*64,
                      experiment_process=runner._experiment_process_policy(stage),
                      failure={'error': 'Control mismatch'})
        packet['review_input_sha256'] = runner._capability_repair_review_input_sha256(packet)
        calls = []
        def pool(panel_stage, assignment, descriptor, **kwargs):
            rows = assignment['assignments']
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['role_id'], 'methodologist')
            self.assertEqual(rows[0]['_response_contract'], 'repair_adjudication')
            calls.append(rows[0])
            current = deepcopy(report)
            current.update(role_id='methodologist', assigned_role='methods.methodologist', usage={'model_calls': 1})
            if failure:
                current.update(status='failed', failure=failure, error='weekly quota exhausted', usage={'model_calls': 17})
            return {'reports': [current], 'by_role': {'methodologist': current}, 'usage': current['usage'], 'model_enabled': True}
        with patch.object(runner, '_build_capability_repair_packet', return_value=packet), \
             patch.object(runner, '_run_pending_capability_evidence', return_value=None), \
             patch.object(runner, '_run_specialist_pool', side_effect=pool), \
             patch.object(runner, '_run_specialist_verifier', side_effect=AssertionError('pre-execution verifier dispatched')):
            result = runner._run_capability_repair_panel(stage, {}, runner.context['topic'], {}, 'Control mismatch')
            if not failure:
                cached = runner._run_capability_repair_panel(stage, {}, runner.context['topic'],
                                                           {'capability_repair_panel': result}, 'Control mismatch')
                self.assertEqual(cached['dispatch_usage']['model_calls'], 0)
        return result, calls

    def test_single_plan_dispatch_is_not_an_independent_verdict_and_reuses_exact_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp)); result, calls = self.panel(runner, stage)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['decision'], 'repair')
            self.assertTrue(runner._capability_repair_plan_admitted(result))
            self.assertEqual(result['verifier']['status'], 'not_evaluated')
            self.assertIsNone(result['verifier']['decision'])
            _, _, verdict = runner._read_verified_artifact_json(result['ledger']['verifier_artifact_ref'])
            self.assertEqual(verdict['verdict'], 'not_evaluated')
            self.assertEqual(verdict['attempt_state'], 'not_started')
            changed = deepcopy(result); changed['independent_validation_required'] = False
            self.assertFalse(runner._capability_repair_plan_admitted(changed))
            changed = deepcopy(result); changed['packet']['experiment_process']['mode'] = 'role_pool'
            self.assertFalse(runner._capability_repair_plan_admitted(changed))
            self.assertNotEqual(runner._capability_repair_review_input_sha256(changed['packet']), result['review_input_sha256'])

    def test_quota_failure_retains_paid_usage_without_an_accepted_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            result, calls = self.panel(runner, stage, failure={'kind': 'provider_rate_limit', 'provider': 'fixed',
                                                            'details': {'provider_error_kind': 'quota_exhausted'}})
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['decision'], 'defer')
            self.assertFalse(runner._capability_repair_plan_admitted(result))
            self.assertEqual(result['dispatch_usage']['model_calls'], 17)
            self.assertEqual(result['model_failure']['failure']['kind'], 'provider_rate_limit')

    def test_outer_review_pool_does_not_redispatch_and_missing_execution_has_no_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            with patch('scisaurus.runtime.specialists.ModelClient', side_effect=AssertionError('provider called')):
                bundle = runner._run_specialist_pool(stage, {}, {}, stage_result={'status': 'blocked'})
                self.assertEqual(bundle['reports'], [])
                self.assertEqual(bundle['usage'], {})
                with patch.object(runner, '_has_executed_experiment_result', return_value=False):
                    self.assertIsNone(runner._run_specialist_verifier(stage, {}, {}, bundle, {'status': 'blocked'}))
            self.assertFalse(runner._should_run_failure_specialist_review(ValidationError('control mismatch'), stage))

    def test_executed_result_requires_an_independent_verifier_route_and_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            with patch.object(runner, '_has_executed_experiment_result', return_value=True), \
                 patch.object(runner, '_specialist_model_config', return_value=None):
                with self.assertRaises(ValidationError): runner._run_specialist_verifier(stage, {}, {}, {}, {})

    def test_unexecuted_ready_result_cannot_be_promoted_to_completion(self):
        from scisaurus.runtime.model_work import ModelWorkBlocked
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            with patch.object(runner, '_has_executed_experiment_result', return_value=False):
                with self.assertRaises(ModelWorkBlocked):
                    runner._run_specialist_verifier(stage, {}, {}, {}, {'status': 'completed'})

    def test_synthetic_evidence_services_preserve_producer_and_verifier_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            service = {**stage, 'id': 'scientific-software', '_model_budget_owner_stage_id': 'experiment'}
            self.assertTrue(runner._session_led_experiment(service))
            self.assertFalse(runner._session_led_result_stage(service))
            with patch.object(runner, '_specialist_model_config', side_effect=RuntimeError('producer route retained')):
                with self.assertRaisesRegex(RuntimeError, 'producer route retained'):
                    runner._run_specialist_pool(service, {}, {}, stage_result={'software_assessment_request': {}})
            with patch.object(runner, '_specialist_model_config', side_effect=RuntimeError('verifier route retained')):
                with self.assertRaisesRegex(RuntimeError, 'verifier route retained'):
                    runner._run_specialist_verifier(service, {}, {}, {}, {}, stage_result={'_repair_panel': True})

    def test_forward_authority_cannot_override_pending_independent_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, stage = self.runner(Path(tmp))
            runner.workflow['progression_policy'] = 'forward_first'
            error = ValidationError('independent science assignment is missing')
            self.assertFalse(runner._composer_can_advance_after_admission(stage, error))
            with self.assertRaises(StateError):
                runner._authorize_forward_context(stage, {'status': 'candidate_needs_review'}, reason='resume')
            self.assertIsNone(runner._materialize_forward_progress(stage, {}, error, {}, {}, [], force_advance=True))
            with patch.object(runner, '_has_executed_experiment_result', return_value=True), \
                 patch.object(runner, '_specialist_model_config', return_value={'model': 'independent'}):
                with self.assertRaises(ValidationError): runner._run_specialist_verifier(stage, {}, {}, {}, {})

    def test_primary_prompt_preserves_policy_and_small_execution_review_order(self):
        packet, _ = packet_and_report()
        packet['experiment_process'] = {'mode': 'session_led', 'artifact_ref': 'artifact:policy@1', 'body_sha256': 'a'*64}
        prompt = json.loads(build_repair_adjudication_prompt({}, packet, []))
        self.assertEqual(prompt['repair_adjudication_packet']['experiment_process'], packet['experiment_process'])
        client = DshStructuredProducerClient.__new__(DshStructuredProducerClient)
        task = client.task(prompt)
        self.assertIn('minimum discriminating solver execution', task)
        self.assertIn('same development session', task)
        self.assertIn('awaits validation', task)

    def test_primary_cannot_fall_back_when_dsh_backend_disappears(self):
        from scisaurus.tests import test_dsh_software_producer as software_fixtures
        fixture = software_fixtures.DshSoftwareProducerTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        dispatcher = fixture.dispatcher()
        dispatcher.software_author_backend = None
        assignment = {**fixture.assignment, '_requires_development_session': True,
                      '_response_contract': 'repair_adjudication', '_software_tools': False}
        with patch('scisaurus.runtime.specialists.ModelClient', side_effect=AssertionError('stateless fallback called')):
            report = dispatcher.dispatch([assignment], {})[0]
        self.assertEqual(report['status'], 'failed')
        self.assertIn('DSH development session', report['error'])
        self.assertEqual(report['usage'], {})


if __name__ == '__main__':
    unittest.main()
