"""Stopped operator evidence is question-bound and independently adjudicated."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.runtime.composer import ComposerRunner, CAPABILITY_REPAIR_PANEL_PROMPT_REVISION
from scisaurus.runtime.composer_supervisor import ComposerSupervisor
from scisaurus.runtime.model_work import ModelWorkBlocked
from scisaurus.runtime.specialists import (
    build_repair_adjudication_prompt, build_specialist_prompt,
    repair_adjudication_evidence_document, _verifier_repair_packet, _json_with_budget,
)
from scisaurus.tests import test_concept_differentiation as concept_fixtures
from scisaurus.tests.test_composer import _align_repair_fixture


QUESTION = 'Does the declared physical comparison hold under matched controls?'
REVIEW = {'research_question': QUESTION, 'directives': [
    {'id': 'theory', 'text': 'Check the assumptions and theoretical limits.'},
    {'id': 'controls', 'text': 'Check whether the reference realizes the declared contrast.'}],
    'evidence': {'source_sha256': 'a' * 64, 'authority': 'unadjudicated evidence'}}


def review_input(ref='artifact:command/operator/review@1', review=None):
    return {'artifact_ref': ref, 'body_sha256': 'b' * 64,
            'review': deepcopy(review or REVIEW), 'authority': 'unadjudicated_review_evidence'}


def packet_and_report():
    packet = {'topic': {'id': 'topic', 'research_question': QUESTION},
        'failure_lineage': {'identity_verified': True, 'stage_id': 'experiment',
            'attempt_number': 3, 'failure_dossier_ref': 'artifact:failure/attempt-3@1',
            'failure_input_sha256': 'a' * 64},
        'question_alignment': {'original': {'research_question': QUESTION,
            'disconfirmation_test': 'Use matched controls.'},
            'candidate': {'primary_outcomes': [{'id': 'contrast', 'definition': 'Matched contrast.'}]}},
        'repair_contract': {'must_prove': ['Independent recalculation.']},
        'external_review_requests': [review_input()],
        'external_review_contract': {'instruction': 'Adjudicate each directive before code authoring.'}}
    plan = {'disposition': 'repair',
        'root_cause': {'statement': 'The control does not implement the declared contrast.',
                       'evidence': ['The exact source defines a different reference.']},
        'required_changes': [{'target': 'executor', 'instruction': 'Implement the declared reference.',
                             'scientific_basis': 'The contrast requires matched controls.', 'source_refs': []}],
        'acceptance_checks': [{'phase': 'execution', 'check': 'Recalculate the matched contrast.'}],
        'residual_uncertainties': [],
        'external_review_dispositions': [
            {'review_ref': packet['external_review_requests'][0]['artifact_ref'], 'id': item['id'],
             'disposition': 'unresolved', 'evidence': ['The current source needs an explicit check.']}
            for item in REVIEW['directives']]}
    response = _align_repair_fixture({'decision': 'repair', 'repair_plan': plan},
                                    build_repair_adjudication_prompt({}, packet, []))
    return packet, {'status': 'succeeded', 'response': {'decision': 'repair', 'raw': response}}


class ExperimentReviewHandoffTests(unittest.TestCase):
    def stopped(self, root):
        workflow = concept_fixtures.ConceptDifferentiationTests().workflow(root)
        runner = ComposerRunner(workflow)
        runner.context['topic'] = {'kind': 'topic_discovery', 'topic': {'id': 'topic', 'research_question': QUESTION}}
        runner.context['experiment'] = {'unknown_result': {'usage': {'model_calls': 39}},
                                        'frozen_input': {'unaltered': True}}
        runner.continuation_pending_stage_ids = {'experiment'}
        runner.status = 'paused'; runner._checkpoint('paused', force=True); runner.close()
        (root/'composer/output/run-control.json').write_text('{"stop_requested":true}')
        return ComposerRunner(workflow, resume=True, control_only=True)

    def test_receipt_idempotence_and_resume_preserve_scientific_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.stopped(Path(tmp))
            try:
                original, usage, workflow = deepcopy(runner.context), deepcopy(runner.usage), deepcopy(runner.workflow)
                result = runner.attach_experiment_review('experiment', REVIEW)
                self.assertEqual(result, runner.attach_experiment_review('experiment', REVIEW))
                self.assertEqual(len(runner.context['experiment']['operator_experiment_review_refs']), 1)
                for key, value in original['experiment'].items():
                    self.assertEqual(runner.context['experiment'][key], value)
                self.assertEqual(runner.context['topic'], original['topic'])
                self.assertEqual((runner.workflow, runner.usage), (workflow, usage))
                with self.assertRaises(StateError): runner.run()
                runner.close()
                runner = ComposerRunner(workflow, resume=True, control_only=True)
                stage = runner.workflow['stages'][-1]
                inputs = runner._experiment_review_inputs(stage, runner.context['topic']['topic'], runner.context['experiment'])
                self.assertEqual(inputs[0]['body_sha256'], result['body_sha256'])
                self.assertEqual(inputs[0]['review'], REVIEW)
                self.assertEqual(runner.usage, usage)
            finally: runner.close()

    def test_supervisor_stop_preserves_an_already_paused_checkpoint_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.stopped(Path(tmp)); workflow = deepcopy(runner.workflow); runner.close()
            supervisor = ComposerSupervisor.__new__(ComposerSupervisor); supervisor.workflow = workflow
            supervisor._mark_interrupted_checkpoint()
            runner = ComposerRunner(workflow, resume=True, control_only=True)
            try:
                result = runner.attach_experiment_review('experiment', REVIEW)
                self.assertTrue(result['artifact_ref'])
                self.assertEqual(runner.context['experiment']['frozen_input'], {'unaltered': True})
            finally: runner.close()

    def test_supervisor_projection_cannot_change_scientific_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.stopped(Path(tmp)); workflow = deepcopy(runner.workflow); runner.close()
            supervisor = ComposerSupervisor.__new__(ComposerSupervisor); supervisor.workflow = workflow
            supervisor._mark_interrupted_checkpoint()
            path = Path(tmp)/'composer/output/progress.json'; state = json.loads(path.read_text())
            state['context']['experiment']['frozen_input']['unaltered'] = False
            path.write_text(json.dumps(state))
            runner = ComposerRunner(workflow, resume=True, control_only=True)
            try:
                with self.assertRaises(StateError): runner.attach_experiment_review('experiment', REVIEW)
            finally: runner.close()

    def test_rejects_wrong_question_stage_live_owner_and_invalid_directives(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.stopped(Path(tmp))
            try:
                for change in ({'research_question': 'Different question'}, {'directives': []},
                               {'directives': REVIEW['directives'] * 2}, {'evidence': []}):
                    with self.subTest(change=change), self.assertRaises(ValidationError):
                        runner.attach_experiment_review('experiment', {**REVIEW, **change})
                with self.assertRaises(StateError): runner.attach_experiment_review('survey', REVIEW)
                runner.control_only = False
                with self.assertRaises(StateError): runner.attach_experiment_review('experiment', REVIEW)
                runner.control_only = True
                (runner.root/'output/run-control.json').write_text(json.dumps({'stop_requested': True, 'pid': os.getpid()}))
                with self.assertRaises(StateError): runner.attach_experiment_review('experiment', REVIEW)
                (runner.root/'output/run-control.json').write_text('{"stop_requested":false}')
                with self.assertRaises(StateError): runner.attach_experiment_review('experiment', REVIEW)
            finally: runner.close()

    def test_foreign_receipt_and_modified_question_cannot_be_consumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.stopped(Path(tmp))
            try:
                result = runner.attach_experiment_review('experiment', REVIEW)
                manifest, _, body = runner._read_verified_artifact_json(result['artifact_ref'])
                stage = runner.workflow['stages'][-1]; topic = runner.context['topic']['topic']
                for change in ({'workflow_id': 'foreign'}, {'stage_id': 'survey'}, {'topic_id': 'foreign'}):
                    with patch.object(runner, '_read_verified_artifact_json', return_value=(manifest, None, {**body, **change})):
                        with self.assertRaises(StateError): runner._experiment_review_inputs(stage, topic, runner.context['experiment'])
                with patch.object(runner, '_read_verified_artifact_json', return_value=({**manifest, 'author': 'producer'}, None, body)):
                    with self.assertRaises(StateError): runner._experiment_review_inputs(stage, topic, runner.context['experiment'])
                with self.assertRaises(StateError):
                    runner._experiment_review_inputs(stage, {**topic, 'research_question': 'changed'}, runner.context['experiment'])
                for change in ({'directives': None}, {'directives': []}, {'evidence': []}):
                    invalid = {**body, 'review': {**body['review'], **change}}
                    with patch.object(runner, '_read_verified_artifact_json', return_value=(manifest, None, invalid)):
                        with self.assertRaises(StateError): runner._experiment_review_inputs(stage, topic, runner.context['experiment'])
            finally: runner.close()

    def test_all_prompt_consumers_preserve_complete_review_and_plan_schema(self):
        packet, report = packet_and_report()
        for row in packet['external_review_requests'][0]['review']['directives']:
            row['text'] += ' evidence' * 800
        document = repair_adjudication_evidence_document(packet)
        peer = json.loads(build_specialist_prompt({'stage_kind': 'experiment'},
             {'repair_panel': True, 'capability_repair_packet': packet}))['projected_input']['capability_repair_packet']
        lead = json.loads(build_repair_adjudication_prompt({}, packet, []))
        verifier = _verifier_repair_packet(packet, detail='compact')
        author = ComposerRunner._capability_authoring_repair_projection({'packet': packet})
        for projection in (document, peer, lead['repair_adjudication_packet'], verifier, author):
            self.assertEqual(projection['external_review_requests'], packet['external_review_requests'])
        self.assertIn('external_review_dispositions', lead['decision_contract']['output_schema']['repair_plan'])
        bounded = json.loads(_json_with_budget({'external_review_requests': packet['external_review_requests'], 'optional': 'x'*50000}, system='', max_input_tokens=15000))
        self.assertEqual(bounded['external_review_requests'], packet['external_review_requests'])

    def test_evidence_identity_changes_only_when_review_is_supplied(self):
        packet, _ = packet_and_report()
        original = deepcopy(packet); original.pop('external_review_requests')
        digest = ComposerRunner._capability_repair_review_input_sha256(original)
        self.assertEqual(digest, ComposerRunner._capability_repair_review_input_sha256({**original, 'external_review_requests': []}))
        self.assertNotEqual(digest, ComposerRunner._capability_repair_review_input_sha256(packet))
        changed = deepcopy(packet); changed['external_review_requests'][0]['review']['directives'][0]['text'] += ' changed'
        self.assertNotEqual(ComposerRunner._capability_repair_review_input_sha256(packet), ComposerRunner._capability_repair_review_input_sha256(changed))

    def test_each_versioned_directive_requires_evidence_without_forcing_positive(self):
        packet, report = packet_and_report()
        plan, error = ComposerRunner._validated_capability_repair_plan(report, packet)
        self.assertIsNone(error)
        self.assertTrue(all(row['disposition'] == 'unresolved' for row in plan['external_review_dispositions']))
        self.assertIn({'phase': 'execution', 'check': 'Independent recalculation.'}, plan['acceptance_checks'])
        for change in ([], report['response']['raw']['repair_plan']['external_review_dispositions'][:1],
                       [{'id': [], 'review_ref': {}, 'disposition': 'confirmed', 'evidence': ['evidence']}],
                       [{'id': 'theory', 'review_ref': 'foreign', 'disposition': 'confirmed', 'evidence': []}]):
            changed = deepcopy(report); changed['response']['raw']['repair_plan']['external_review_dispositions'] = change
            self.assertIsNone(ComposerRunner._validated_capability_repair_plan(changed, packet)[0])
        for disposition in ([], {}, None, True):
            changed = deepcopy(report)
            changed['response']['raw']['repair_plan']['external_review_dispositions'][0]['disposition'] = disposition
            self.assertIsNone(ComposerRunner._validated_capability_repair_plan(changed, packet)[0])
        duplicate = deepcopy(packet['external_review_requests'][0]); duplicate['artifact_ref'] += '-revision'
        packet['external_review_requests'].append(duplicate)
        self.assertIsNone(ComposerRunner._validated_capability_repair_plan(report, packet)[0])
        extra = deepcopy(report['response']['raw']['repair_plan']['external_review_dispositions'])
        for row in extra: row['review_ref'] = duplicate['artifact_ref']; row['disposition'] = 'refuted'
        report['response']['raw']['repair_plan']['external_review_dispositions'] += extra
        self.assertIsNone(ComposerRunner._validated_capability_repair_plan(report, packet)[1])

    def test_review_gate_precedes_authoring_even_for_reference_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.stopped(Path(tmp))
            try:
                runner.workflow['capability_foundry_config_path'] = 'unused.json'
                runner.attach_experiment_review('experiment', REVIEW)
                with patch.object(runner, '_experiment_pilot_survey', return_value=False), \
                     patch.object(runner, '_reference_implementation_authoring', return_value=True), \
                     patch.object(runner, '_run_capability_repair_panel', return_value={'status': 'blocked', 'decision': 'hold'}) as panel, \
                     patch.object(runner, '_materialize_topic_capability') as author:
                    with self.assertRaises(ModelWorkBlocked):
                        runner._apply_topic_to_experiment_config(runner.workflow['stages'][-1], {'experiment': {}})
                    panel.assert_called_once(); author.assert_not_called()
            finally: runner.close()

    def test_admitted_review_survives_authoring_interruption_without_another_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.stopped(Path(tmp))
            try:
                runner.workflow['capability_foundry_config_path'] = 'unused.json'
                runner.attach_experiment_review('experiment', REVIEW)
                packet, report = packet_and_report()
                packet['external_review_requests'] = runner._experiment_review_inputs(
                    runner.workflow['stages'][-1], runner.context['topic']['topic'], runner.context['experiment'])
                for row in report['response']['raw']['repair_plan']['external_review_dispositions']:
                    row['review_ref'] = packet['external_review_requests'][0]['artifact_ref']
                packet['input_sha256'] = 'c' * 64
                plan, error = ComposerRunner._validated_capability_repair_plan(report, packet)
                self.assertIsNone(error)
                panel = {'schema_version': 'capability-repair-panel-8', 'status': 'completed',
                    'decision': 'repair', 'packet': packet, 'input_sha256': packet['input_sha256'],
                    'prompt_revision': CAPABILITY_REPAIR_PANEL_PROMPT_REVISION,
                    'repair_plan': plan, 'reports': [],
                    'dispatch_usage': {'model_calls': 5, 'input_tokens': 100, 'output_tokens': 200},
                    'verifier': {'status': 'succeeded', 'decision': 'accept'}}
                self.assertTrue(ComposerRunner._capability_repair_plan_admitted(panel))
                runner.context['experiment']['capability_repair_panel'] = deepcopy(panel)
                with patch.object(runner, '_experiment_pilot_survey', return_value=False), \
                     patch.object(runner, '_build_capability_repair_packet', return_value=packet) as packet_builder, \
                     patch.object(runner, '_run_pending_capability_evidence', return_value=None), \
                     patch.object(runner, '_materialize_topic_capability', side_effect=ModelWorkBlocked('authoring interrupted')) as author:
                    for _ in range(2):
                        with self.assertRaises(ModelWorkBlocked) as blocked:
                            runner._apply_topic_to_experiment_config(runner.workflow['stages'][-1], {'experiment': {}})
                        self.assertTrue(author.call_args.kwargs['force_regenerate'])
                        self.assertEqual(author.call_args.kwargs['repair_context']['repair_plan'], panel['repair_plan'])
                        self.assertTrue(all(value == 0 for value in blocked.exception.repair_panel_usage.values()))
                        self.assertEqual(getattr(blocked.exception, 'usage', {}).get('model_calls', 0), 0)
                        self.assertEqual(runner.context['experiment']['capability_repair_panel']['packet'], panel['packet'])
                    self.assertEqual(packet_builder.call_count, 2)
            finally: runner.close()
