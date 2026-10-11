"""Receipt-bound mission flow with provider and scientific execution replaced at service boundaries."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from scisaurus.core.events import ControlStore
from scisaurus.core.errors import StateError
from scisaurus.core.schema import canonical_bytes
from scisaurus.core.store import ArtifactStore
from scisaurus.runtime.composer import ComposerRunner
from scisaurus.runtime.development_session import development_session_binding
from scisaurus.runtime.dsh_batch import DshBatchError, DshBatchRunner
from scisaurus.runtime.mission_research import MissionResearchSession, digest
from scisaurus.runtime.model_work import ModelWorkBlocked
from scisaurus.tests import test_mission_research as mission_fixtures
from scisaurus.tests.test_experiment_review_handoff import QUESTION, REVIEW


class MissionResearchFlowTests(unittest.TestCase):
    def fixture(self, root):
        helper = mission_fixtures.MissionResearchTests()
        self.addCleanup(helper.doCleanups)
        c = helper.runner(root)
        composition = root / 'offline-composition.json'
        composition.write_text('{}')
        executable = str(Path(sys.executable).absolute())
        read_root = root/'backend-read'
        read_root.mkdir()
        config = {'schema_version': 'dsh-batch-1', 'command': [executable],
            'pinned_files': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                             for p in (executable, str(composition))},
            'composition': str(composition), 'read_roots': [str(read_root)],
            'provider': 'offline', 'model': 'fixed-model', 'auth_env': 'OFFLINE_UNUSED',
            'timeout_seconds': 120, 'max_output_tokens': 32768}
        state = c.root / 'development-session/session.json'
        state.write_bytes(canonical_bytes({'schema_version': 'dsh-development-session-1',
            'session_id': 'retained-session', 'binding': development_session_binding(c.root),
            'config_sha256': digest(config), 'calls': [], 'active_job': None}))
        c.attach_experiment_review('experiment', REVIEW)
        ref, assignment = helper.frozen(c)
        for stage in c.workflow['completion']['required_stage_ids']:
            if stage != 'experiment':
                c.stage_records.setdefault(stage, {})['status'] = 'completed'
        c.laboratory_binding = Mock()
        return c, helper, {'author_backend': config, 'runtime_python': executable}, ref, assignment

    def accepted_result(self, project):
        output = project / 'output'
        output.mkdir(parents=True)
        raw = {'observations': [{'id': 'fixture', 'value': 1}]}
        raw_path = output / 'raw.json'
        raw_path.write_bytes(canonical_bytes(raw))
        ref = lambda name: 'artifact:' + name + '@1'
        package = {'schema_version': 'results-package-2', 'id': 'offline', 'revision': 1,
            'study_type': 'exploratory', 'question': QUESTION, 'hypothesis': 'Fixture contract only.',
            'procedures': [], 'metrics': [], 'findings': [], 'assets': [],
            'limitations': ['Synthetic control-flow evidence, not physical evidence.'],
            'provenance': {'score_ref': ref('score/offline'), 'literature_survey_ref': None,
                'literature_assessment_ref': None, 'execution_refs': [ref('execution/first'), ref('execution/replay')],
                'validator_execution_ref': ref('execution/validator'),
                'execution_profile_ref': ref('profile/execution'), 'validation_profile_ref': ref('profile/validation'),
                'replay_sha256': digest(raw)},
            'validation': {'decision': 'accepted', 'deterministic_validation_ref': ref('validation/replay'),
                'model_review_refs': [ref('review/methods'), ref('review/claims')], 'assessment_ref': ref('assessment/result')}}
        package_path = output / 'results.json'
        package_path.write_bytes(canonical_bytes(package))
        control = ControlStore(project)
        try:
            store = ArtifactStore(control)
            store.init_project()
            record = store.publish_artifact(logical_id='methods/result', artifact_type='report',
                author='methods.result-integrator', body=canonical_bytes(package), media_type='application/json')
        finally:
            control.close()
        result = {'status': 'completed', 'project_id': str(project), 'results_package': str(package_path),
            'raw_results': str(raw_path), 'raw_results_sha256': digest(raw), 'incumbent_ref': record['artifact_ref'],
            'usage': {'cumulative_usage': {'model_calls': 2, 'input_tokens': 20, 'output_tokens': 10}}}
        (output / 'run.json').write_bytes(canonical_bytes(result))
        return result

    def drive(self, c, backend, assignment_ref, *, verdicts=('accepted',), reject=False,
              unknown=False, tamper=False, stop_after_tool=False):
        events, primary_sessions, audit_inputs = [], [], []
        session = MissionResearchSession(c)
        descriptor = c.root / 'offline-descriptor.json'
        stage = next(s for s in c.workflow['stages'] if s['id'] == 'experiment')
        stage_config = json.loads(Path(stage['config_path']).read_bytes())
        stage_config['experiment'] = {'literature_gate': {}, 'execution': {'source': 'submitted'}}
        stage_config['limits'] = {'concurrent_calls': 3, 'wall_clock_seconds': 120}
        Path(stage['config_path']).write_bytes(canonical_bytes(stage_config))
        descriptor.write_bytes(canonical_bytes({'experiment': stage_config['experiment']}))
        candidate = {'executor_source': 'complete fixture source', 'experiment_intent': {'research_question': QUESTION}}
        reviews = c._experiment_review_inputs(stage, c.context['topic']['topic'], c.context['experiment'])
        dispositions = [{'review_ref': r['artifact_ref'], 'id': d['id'], 'disposition': 'unresolved',
                         'evidence': 'Fixture records the unresolved theoretical/control scope.'}
                        for r in reviews for d in r['review']['directives']]
        action = {'operation': 'evaluate_pilot', 'arguments': {'assignment_ref': assignment_ref,
                  'candidate': candidate, 'external_review_dispositions': dispositions}}
        foundry = Mock()

        def admit(*args, **kwargs):
            events.append('independent_admission')
            submitted = kwargs['submitted_candidate']
            self.assertEqual(submitted.complete(system='', prompt='').json_object(), candidate)
            with self.assertRaises(ModelWorkBlocked):
                submitted.complete(system='', prompt='')
            if reject and events.count('independent_admission') == 1:
                error = ModelWorkBlocked('current independent rejection')
                error.repair_feedback = {'gate': 'scientific_review', 'checks': ['unmatched control']}
                raise error
            return {'registration': {'descriptor_path': str(descriptor)}, 'candidate': deepcopy(candidate),
                    'admission': {'decision': 'accepted', 'offline': True}}
        foundry.generate.side_effect = admit

        def evaluate(runner):
            events.append('evaluation_execution')
            if unknown:
                return {'status':'blocked','error':'offline weekly quota',
                    'failure':{'kind':'provider_rate_limit','details':{
                        'provider_error_kind':'quota_exhausted','status_code':429}},
                    'usage':{'cumulative_usage':{'model_calls':2,'input_tokens':20,'output_tokens':10}}}
            result = self.accepted_result(Path(runner.project))
            if tamper:
                Path(result['raw_results']).write_text('{"observations":[]}')
            return result

        class Evaluation:
            def __init__(self, project, config):
                self.project = project
                self.config = config
            def run(self):
                return evaluate(self)

        def dispatch(runner, task, *, inputs, seed_files, outputs, deadline, exchange, development):
            job = runner.root / ('offline-' + uuid.uuid4().hex)
            job.mkdir(parents=True)
            receipt = job / 'receipt.json'
            if development is None:
                self.assertIsNone(exchange)
                self.assertEqual(set(inputs), {'assignment.json', 'system-contract.txt'})
                self.assertEqual(runner.isolated_from, (str(c.root.resolve()),))
                assignment = json.loads(inputs['assignment.json'])
                audit_inputs.append(deepcopy(assignment))
                events.append('independent_final_audit')
                outcome = verdicts[len(audit_inputs)-1]
                if outcome == 'unknown':
                    usage = {'model_calls': 1, 'input_tokens': 30, 'output_tokens': 10}
                    receipt.write_bytes(canonical_bytes({'status': 'result_unknown', 'usage': usage}))
                    raise DshBatchError('offline final audit interrupted', receipt=receipt, usage=usage)
                response = {'decision': outcome, 'original_question_resolved': outcome == 'accepted',
                    'evidence_sha256': assignment['evidence_sha256'], 'rationale': 'Synthetic audit contract verdict.',
                    'limitations': ['Offline fixture only.']}
                usage = {'model_calls': 1, 'input_tokens': 30, 'output_tokens': 10}
                assignment.pop('runtime_python')
                receipt = Path(mission_fixtures.write_final_audit_receipt(job, assignment, backend, response, usage))
                return {'files': {'response.json': canonical_bytes(response)}, 'usage': usage,
                        'elapsed_seconds': 0, 'receipt': str(receipt)}
            self.assertIsNotNone(exchange)
            primary_sessions.append(development.state['session_id'])
            development.bind_job(receipt)
            usage = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0}
            def freeze(directory, files):
                directory.mkdir(parents=True)
                for name, body in files.items():
                    path = directory / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                    path.chmod(0o444)
                return {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
            initial_hashes = freeze(job / 'input', inputs)
            state = {'status': 'failed', 'outcome_known': True, 'process_reaped': True,
                'job_directory': str(job), 'session_id': development.state['session_id'],
                'config_sha256': digest(runner.config), 'development_session': {
                    'binding': development.binding, 'prior_receipts': deepcopy(development.state['calls'])},
                'usage': usage, 'input_sha256': initial_hashes, 'turns': []}
            current_input, current_hashes = 'input', initial_hashes

            def turn(response):
                nonlocal current_input, current_hashes
                usage['model_calls'] += 1
                usage['input_tokens'] += 10
                usage['output_tokens'] += 5
                receipt.write_bytes(canonical_bytes(state))
                events.append('primary_turn')
                files = {'response.json': canonical_bytes(response)}
                index = len(state['turns'])
                output_hashes = freeze(job / 'turns' / str(index), files)
                state['turns'].append({'input_directory': current_input, 'input_sha256': current_hashes,
                                       'outputs': output_hashes, 'usage': dict(usage)})
                updated = exchange(files, dict(usage))
                if updated is not None:
                    current_input = 'input-turn-' + str(index + 1)
                    current_hashes = freeze(job / current_input, updated['inputs'])
                return updated
            try:
                observation = turn({'tool_action': {'operation': 'describe_pilot',
                                     'arguments': {'assignment_ref': assignment_ref}}})
                self.assertTrue(json.loads(observation['inputs']['assignment.json'])['last_tool_observation']['configured_input'])
                if stop_after_tool:
                    raise ModelWorkBlocked('finite dispatch watchdog; checkpoint retained')
                observation = turn({'tool_action': action})
                if reject:
                    feedback = json.loads(observation['inputs']['assignment.json'])['last_tool_observation']
                    self.assertEqual(feedback['outcome'], 'failed')
                    revised = deepcopy(action)
                    revised['arguments']['candidate']['executor_source'] = 'revised fixture source'
                    candidate['executor_source'] = 'revised fixture source'
                    observation = turn({'tool_action': revised})
                current = json.loads(observation['inputs']['assignment.json'])['last_tool_observation']
                self.assertIn('artifact_ref', current, current)
                result_ref = current['artifact_ref']
                final = {'result_ref': result_ref, 'summary': 'Synthetic flow resolution.', 'limitations': ['Fixture only.']}
                for outcome in verdicts:
                    next_turn = turn({'final': final})
                    if outcome == 'hold':
                        self.assertIn('final_feedback', json.loads(next_turn['inputs']['assignment.json']))
                        final['summary'] = 'Revised synthetic interpretation.'
                    else:
                        self.assertIsNone(next_turn)
                encoded = canonical_bytes({'final': final})
                out = job / 'outputs'
                out.mkdir()
                (out / 'response.json').write_bytes(encoded)
                state.update(status='completed', output_directory='outputs', outputs={'response.json': hashlib.sha256(encoded).hexdigest()})
                return {'files': {'response.json': encoded}, 'usage': dict(usage), 'elapsed_seconds': 0, 'receipt': str(receipt)}
            finally:
                receipt.write_bytes(canonical_bytes(state))

        with ExitStack() as stack:
            stack.enter_context(patch('socket.socket', side_effect=AssertionError('offline: network disabled')))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('offline: process launch disabled')))
            stack.enter_context(patch('scisaurus.runtime.program_sandbox._macho_dependency_paths', return_value=set()))
            stack.enter_context(patch('scisaurus.runtime.models.ModelClient.complete', side_effect=AssertionError('hidden model')))
            stack.enter_context(patch('scisaurus.runtime.dsh_batch.DshAuthorClient.complete', side_effect=AssertionError('hidden author')))
            stack.enter_context(patch.object(c, '_run_stage', side_effect=AssertionError('legacy stage')))
            stack.enter_context(patch.object(c, '_software_author_backend_config', return_value=backend))
            stack.enter_context(patch.object(c, '_build_capability_repair_packet', return_value={
                'research_question': QUESTION, 'external_review_requests': reviews}))
            stack.enter_context(patch.object(session, '_bind_tools'))
            stack.enter_context(patch.object(session, '_foundry', return_value=(backend, foundry)))
            stack.enter_context(patch.object(c, '_sync_foundry_usage'))
            stack.enter_context(patch.object(c, '_apply_bindings', side_effect=lambda value, bindings: value))
            stack.enter_context(patch('scisaurus.runtime.experiment.ExperimentRunner', Evaluation))
            stack.enter_context(patch.object(DshBatchRunner, '_dispatch', dispatch))
            result = session.run()
        return result, session, events, primary_sessions, audit_inputs

    def test_without_external_recovery_entire_flow_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp))
            result, session, events, owners, audits = self.drive(c, backend, ref)
            self.assertEqual(result['status'], 'paused')
            self.assertEqual((events, owners, audits), ([], [], []))
            self.assertEqual(c.usage['model_calls'], 0)

    def test_final_unknown_preserves_usage_and_blocks_new_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref, verdicts=('unknown',))
            self.assertEqual(result['status'], 'paused')
            self.assertEqual(owners, ['retained-session'])
            self.assertEqual(len(audits), 1)
            self.assertEqual(c.usage['model_calls'], 6)
            self.assertNotIn('final_ref', session.state)
            self.assertNotIn('research_resume_authorization_ref', json.loads((c.root/'output/run-control.json').read_bytes()))
            with patch.object(DshBatchRunner, '_dispatch', side_effect=AssertionError('unknown final redispatched')):
                self.assertEqual(MissionResearchSession(c).run()['status'], 'paused')

    def test_completed_checkpoint_rejects_changed_raw_without_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref)
            self.assertEqual(result['status'], 'completed')
            Path(session.final['result']['raw_results']).write_text('{"observations":[]}')
            paid = deepcopy(c.usage)
            with patch.object(DshBatchRunner, '_dispatch', side_effect=AssertionError('changed final redispatched')):
                result = MissionResearchSession(c).run()
            self.assertEqual(result['status'], 'paused')
            self.assertIn('raw observations changed', str(c.blockers))
            self.assertEqual(c.usage, paid)

    def test_completed_checkpoint_requires_exact_existing_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref)
            self.assertEqual(result['status'], 'completed')
            audit = c._read_verified_artifact_json(session.final['independent_final_ref'])[0]
            session._publish(audit['artifact_id'], {'policy': session.policy, 'status': 'reconciled'})
            paid = deepcopy(c.usage)
            with patch.object(DshBatchRunner, '_dispatch', side_effect=AssertionError('missing final audit redispatched')):
                result = MissionResearchSession(c).run()
            self.assertEqual(result['status'], 'paused')
            self.assertIn('no matching independent final audit', str(c.blockers))
            self.assertEqual(c.usage, paid)

    def test_final_audit_reuse_rejects_changed_receipt_output_input_and_owner(self):
        for change in ('missing_receipt', 'receipt_usage', 'input', 'output', 'foreign_owner'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
                result, session, events, owners, audits = self.drive(c, backend, ref)
                self.assertEqual(result['status'], 'completed')
                manifest, _, audit = c._read_verified_artifact_json(session.final['independent_final_ref'])
                path = Path(audit['receipt'])
                if change == 'missing_receipt':
                    path.unlink()
                elif change == 'receipt_usage':
                    receipt = json.loads(path.read_bytes()); receipt['usage']['model_calls'] = 0
                    path.write_bytes(canonical_bytes(receipt))
                elif change == 'input':
                    (path.parent/'input/assignment.json').write_text('{}')
                elif change == 'output':
                    (path.parent/'outputs/response.json').write_text('{}')
                else:
                    c._publish(manifest['artifact_id'], 'decision_note', audit, 'foreign.reviewer')
                paid = deepcopy(c.usage)
                with patch.object(c, '_software_author_backend_config', return_value=backend), \
                     patch.object(DshBatchRunner, '_dispatch', side_effect=AssertionError('changed audit redispatched')):
                    result = MissionResearchSession(c).run()
                self.assertEqual(result['status'], 'paused')
                self.assertIn('independent final audit', str(c.blockers))
                self.assertEqual(c.usage, paid)

    def test_independent_backend_roots_cannot_expose_producer_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            session = MissionResearchSession(c)
            session._require_independent_backend(backend)
            for root in (c.root, c.root.parent, c.root/'development-session', c.root/'research-session/jobs'):
                bad = deepcopy(backend); bad['author_backend']['read_roots'] = [str(root)]
                root.mkdir(parents=True, exist_ok=True)
                with self.subTest(root=root), self.assertRaisesRegex(StateError, 'exposes the producer project'):
                    session._require_independent_backend(bad)
            bad = deepcopy(backend); bad['runtime_python'] = str(c.root/'runtime/bin/python')
            (c.root/'runtime').mkdir()
            with self.assertRaisesRegex(StateError, 'exposes the producer project'):
                session._require_independent_backend(bad)

    def test_implicit_command_grants_cannot_expose_producer_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp))
            session = MissionResearchSession(c)
            interpreter = Path(tmp)/'bin/python'; interpreter.parent.mkdir(); interpreter.write_text('fixture')
            (Path(tmp)/'pyvenv.cfg').write_text('fixture')
            bad = deepcopy(backend); bad['author_backend']['command'] = [str(interpreter)]
            with patch('scisaurus.runtime.program_sandbox._macho_dependency_paths', return_value=set()):
                with self.assertRaisesRegex(StateError, 'exposes the producer project'):
                    session._require_independent_backend(bad)
                bad = deepcopy(backend)
                bad['author_backend']['command'].append(str(c.root/'development-session/session.json'))
                with self.assertRaisesRegex(StateError, 'read file exposes the producer project'):
                    session._require_independent_backend(bad)

    def test_authorized_primary_evaluation_final_and_durable_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, assignment = self.fixture(Path(tmp))
            original = deepcopy(c.context)
            helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref)
            self.assertEqual(result['status'], 'completed', c.blockers)
            self.assertEqual(owners, ['retained-session'])
            self.assertEqual(events, ['primary_turn', 'primary_turn', 'independent_admission',
                'evaluation_execution', 'primary_turn', 'independent_final_audit'])
            self.assertEqual(audits[0]['raw_results']['observations'], [{'id': 'fixture', 'value': 1}])
            self.assertEqual(audits[0]['executor_source'], 'complete fixture source')
            self.assertEqual(c._read_verified_artifact_json(c.context['experiment']['prior_context_ref'])[2], original['experiment'])
            self.assertEqual(c.usage['model_calls'], 6)
            state = json.loads((c.root / 'development-session/session.json').read_bytes())
            self.assertIsNone(state['active_job'])
            self.assertEqual(len(state['calls']), 1)
            final_ref = session.state['final_ref']
            c.close()
            resumed = ComposerRunner(c.workflow, resume=True, control_only=True)
            self.addCleanup(resumed.close)
            resumed.laboratory_binding = Mock()
            with patch.object(resumed, '_software_author_backend_config', return_value=backend), \
                 patch.object(DshBatchRunner, '_dispatch', side_effect=AssertionError('completed checkpoint dispatched')):
                self.assertEqual(MissionResearchSession(resumed).run()['status'], 'completed', resumed.blockers)
            self.assertEqual(resumed.context['experiment']['research_session_final_ref'], final_ref)
            self.assertEqual(resumed.usage['model_calls'], 6)

    def test_known_rejection_and_final_hold_continue_same_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref, reject=True, verdicts=('hold','accepted'))
            self.assertEqual(result['status'], 'completed', c.blockers)
            self.assertEqual(owners, ['retained-session'])
            self.assertEqual(events.count('independent_admission'), 2)
            self.assertEqual(events.count('evaluation_execution'), 1)
            self.assertEqual(len(audits), 2)
            self.assertEqual(audits[0]['evidence_sha256'], audits[1]['evidence_sha256'])
            self.assertEqual(audits[1]['executor_source'], 'revised fixture source')

    def test_unknown_evaluation_stops_before_final_or_another_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref, unknown=True)
            self.assertEqual(result['status'], 'paused')
            self.assertEqual(owners, ['retained-session'])
            self.assertEqual(audits, [])
            self.assertTrue(session.state['pending_evaluation'])
            self.assertEqual(c.usage['model_calls'], 4)
            self.assertNotIn('research_resume_authorization_ref', json.loads((c.root/'output/run-control.json').read_bytes()))
            with patch.object(DshBatchRunner, '_dispatch', side_effect=AssertionError('blocked resume dispatched')):
                self.assertEqual(MissionResearchSession(c).run()['status'], 'paused')

    def test_changed_raw_cannot_reach_final_audit_or_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref, tamper=True)
            self.assertEqual(result['status'], 'paused')
            self.assertEqual(audits, [])
            self.assertIn('raw observations changed', str(c.blockers))

    def test_watchdog_and_cold_resume_keep_original_session_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, helper, backend, ref, _ = self.fixture(Path(tmp)); helper.authorize(c)
            result, session, events, owners, audits = self.drive(c, backend, ref, stop_after_tool=True)
            self.assertEqual(result['status'], 'paused')
            retained = (c.root/'development-session/session.json').read_bytes()
            c.close()
            resumed = ComposerRunner(c.workflow, resume=True, control_only=True)
            self.addCleanup(resumed.close); resumed.laboratory_binding = Mock()
            result, session, events, owners, audits = self.drive(resumed, backend, ref)
            self.assertEqual(result['status'], 'completed', resumed.blockers)
            self.assertEqual(owners, ['retained-session'])
            current = json.loads((resumed.root/'development-session/session.json').read_bytes())
            self.assertEqual(current['calls'][:1], json.loads(retained)['calls'])
            self.assertEqual(len(current['calls']), 2)


if __name__ == '__main__':
    unittest.main()
