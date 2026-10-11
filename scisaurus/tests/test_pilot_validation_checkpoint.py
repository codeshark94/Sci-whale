"""Execution checkpoints retain complete evidence without replacing admission replay."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.capability_foundry import (
    CapabilityDeadlineError, _retained_executor_preview,
)
from scisaurus.runtime.model_work import ModelWorkBlocked, ModelWorkProvenanceError
from scisaurus.runtime.review_evidence import review_observation_table
from scisaurus.tests import test_capability_foundry as fixture


class PreviewCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.source = 'print("captured")'
        self.payload = b'{"frozen":true}'
        self.bodies = {}
        self.record = dict(operation='executor_preview', runtime_sha256='a' * 64,
                           returncode=0, timed_out=False, truncated=False, mode='sandbox-exec')
        for name, body in dict(program=self.source.encode(), stdin=self.payload,
                               stdout=b'{"observations":[]}', stderr=b'').items():
            digest = hashlib.sha256(body).hexdigest()
            self.bodies[digest] = body
            self.record[name + '_sha256'] = digest
        class Store:
            def read_body(inner, digest):
                if digest not in self.bodies:
                    raise OSError('missing object')
                return self.bodies[digest]
        self.store = Store()

    def restore(self, records=None, **kwargs):
        return _retained_executor_preview(self.store,
            [self.record] if records is None else records,
            kwargs.get('source', self.source), kwargs.get('payload', self.payload),
            kwargs.get('runtime', 'a' * 64))

    def test_exact_capture_restores_stdout_and_stderr(self):
        result = self.restore()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, self.bodies[self.record['stdout_sha256']])
        self.assertEqual(result.stderr, b'')

    def test_changed_source_input_or_runtime_requires_fresh_execution(self):
        for change in ({'source': self.source + '\n'}, {'payload': b'{}'},
                       {'runtime': 'b' * 64}):
            with self.subTest(change=change):
                self.assertIsNone(self.restore(**change))
        legacy = dict(self.record)
        legacy.pop('runtime_sha256')
        self.assertIsNone(self.restore([legacy]))

    def test_latest_failed_or_incomplete_preview_does_not_restore_older_success(self):
        for change in ({'returncode': 1}, {'returncode': False}, {'timed_out': True},
                       {'truncated': True}, {'mode': 'unsupported'}):
            with self.subTest(change=change):
                self.assertIsNone(self.restore([self.record, {**self.record, **change}]))

    def test_each_object_is_verified_and_missing_capture_fails_closed(self):
        for name in ('program', 'stdin', 'stdout', 'stderr'):
            digest = self.record[name + '_sha256']
            original = self.bodies[digest]
            with self.subTest(name=name, damage='changed'):
                self.bodies[digest] = original + b'damaged'
                with self.assertRaises(ModelWorkProvenanceError):
                    self.restore()
            with self.subTest(name=name, damage='missing'):
                del self.bodies[digest]
                with self.assertRaises(ModelWorkProvenanceError):
                    self.restore()
            self.bodies[digest] = original


class PilotValidationFlowTests(unittest.TestCase):
    def test_resume_completes_current_validation_before_review_with_fresh_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            foundry = fixture.CapabilityFoundryTests._foundry(root)
            cache = fixture.CapabilityFoundryTests._cache(self, root)
            author = fixture.StubClient(fixture.CapabilityFoundryTests._payload())
            def pause(phase, state):
                if phase == 'executor_observations_recorded':
                    raise CapabilityDeadlineError('validation checkpoint')
            with self.assertRaises(CapabilityDeadlineError):
                foundry.generate('bounded comparison', client=author,
                                 work_cache=cache, on_progress=pause)
            self.assertEqual(foundry.validator_client.calls, 0)
            self.assertEqual(foundry.reviewer_client.calls, 0)
            phases = []
            with patch.object(foundry, '_execute', wraps=foundry._execute) as execute:
                result = foundry.generate('bounded comparison', client=author,
                    work_cache=cache, on_progress=lambda phase, state: phases.append(phase))
            self.assertEqual(result['status'], 'registered')
            self.assertEqual(author.calls, 1)
            self.assertEqual(foundry.validator_client.calls, 1)
            self.assertIn('executor_preview_restored_for_validation', phases)
            operations = [row['operation'] for row in cache.entries()[0]['sandbox_executions']]
            self.assertEqual(operations, ['executor_preview', 'validator_readiness',
                'validator_preview', 'executor_replay', 'executor_replay',
                'executor_replay', 'validator_recalculation'])
            self.assertEqual(execute.call_count, 6)

    def test_validator_receives_all_current_rows_without_producer_code_or_conclusions(self):
        with tempfile.TemporaryDirectory() as temp:
            foundry = fixture.CapabilityFoundryTests._foundry(Path(temp))
            captured = []
            complete = foundry.validator_client.complete
            def inspect(**kwargs):
                captured.append(json.loads(kwargs['prompt']))
                return complete(**kwargs)
            foundry.validator_client.complete = inspect
            with patch.object(foundry, '_execute', wraps=foundry._execute) as execute:
                foundry.generate('bounded comparison', client=fixture.StubClient(
                    fixture.CapabilityFoundryTests._payload()))
            preview_source, preview_input = execute.call_args_list[0].args
            preview = foundry._execute(preview_source, preview_input)
            document = json.loads(preview.stdout)
            assignment = captured[0]
            self.assertGreater(len(document['observations']), 3)
            self.assertEqual(assignment['raw_observations'],
                             review_observation_table(document['observations']))
            self.assertTrue(assignment['raw_observations_complete'])
            self.assertEqual(assignment['candidate_sha256'],
                             hashlib.sha256(canonical_bytes(document)).hexdigest())
            for forbidden in ('executor_source', 'validator_source', 'metrics', 'findings'):
                self.assertNotIn(forbidden, assignment)
            self.assertNotIn(fixture.MINI_EXECUTOR, json.dumps(assignment))
            self.assertNotIn(fixture.MINI_VALIDATOR, json.dumps(assignment))
            self.assertTrue(assignment['recorded_assets'])
            self.assertTrue(all(set(asset) <= {'id', 'path', 'sha256', 'role', 'media_type'}
                                for asset in assignment['recorded_assets']))
            for asset in document['assets']:
                self.assertNotIn(asset['caption'], json.dumps(assignment['recorded_assets']))

    def test_late_control_and_optional_fields_are_lossless_and_change_identity(self):
        rows = [{'replicate': i, 'value': i * 0.1} for i in range(5)]
        rows[-1]['control_measurement'] = None
        original = review_observation_table(rows)
        decoded = [dict(zip(original['schemas'][original.get('schema_ids', [0] * len(rows))[i]], row))
                   for i, row in enumerate(original['rows'])]
        self.assertEqual(decoded, rows)
        modified = deepcopy(rows)
        modified[-1]['control_measurement'] = 1.0
        self.assertNotEqual(original['observations_sha256'],
                            review_observation_table(modified)['observations_sha256'])

    def test_late_row_change_invalidates_actual_validator_assignment(self):
        assignments = []
        for changed in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                foundry = fixture.CapabilityFoundryTests._foundry(Path(temp))
                candidate = fixture.CapabilityFoundryTests._payload()
                if changed:
                    candidate['executor_source'] = candidate['executor_source'].replace(
                        '    ordered = sorted(errors)',
                        '    observations[-1]["control_tag"] = "current"\n'
                        '    ordered = sorted(errors)')
                def capture(**kwargs):
                    assignments.append(json.loads(kwargs['prompt']))
                    raise RuntimeError('captured assignment')
                foundry.validator_client.complete = capture
                with self.assertRaisesRegex(RuntimeError, 'captured assignment'):
                    foundry.generate('bounded comparison', client=fixture.StubClient(candidate))
                self.assertEqual(foundry.reviewer_client.calls, 0)
        self.assertEqual(assignments[0]['raw_observation_sample'],
                         assignments[1]['raw_observation_sample'])
        self.assertNotEqual(assignments[0]['candidate_sha256'], assignments[1]['candidate_sha256'])
        self.assertNotEqual(hashlib.sha256(canonical_bytes(assignments[0])).hexdigest(),
                            hashlib.sha256(canonical_bytes(assignments[1])).hexdigest())

    def test_unknown_validator_blocks_review_and_resume_without_new_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            foundry = fixture.CapabilityFoundryTests._foundry(root)
            cache = fixture.CapabilityFoundryTests._cache(self, root)
            author = fixture.StubClient(fixture.CapabilityFoundryTests._payload())
            calls = []
            def unavailable(**kwargs):
                calls.append(kwargs)
                raise RuntimeError('unknown validator outcome')
            foundry.validator_client.complete = unavailable
            with self.assertRaises(RuntimeError):
                foundry.generate('bounded comparison', client=author, work_cache=cache)
            with self.assertRaises(ModelWorkBlocked):
                foundry.generate('bounded comparison', client=author, work_cache=cache)
            self.assertEqual(len(calls), 1)
            self.assertEqual(author.calls, 1)
            self.assertEqual(foundry.reviewer_client.calls, 0)
            self.assertTrue(any(value['status'] == 'result_unknown'
                for value in cache.entries()[0]['validator_authorship'].values()))

    def test_contract_change_preserves_delegated_quota_and_paid_unknown_before_execution(self):
        from types import SimpleNamespace
        from scisaurus.runtime.dsh_batch import DshBatchError
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            foundry = fixture.CapabilityFoundryTests._foundry(root)
            foundry.author_backend = {'schema_version': 'fixture', 'model': 'stub'}
            cache = fixture.CapabilityFoundryTests._cache(self, root)
            author = fixture.StubClient(fixture.CapabilityFoundryTests._payload())
            author.runner = SimpleNamespace(root=root / 'author')
            foundry.validator_client.runner = SimpleNamespace(root=root / 'validator')
            calls = []
            failure = {'status_code': 429, 'provider_error_kind': 'quota_exhausted',
                       'retry_after_known': False}
            def unavailable(**kwargs):
                calls.append(kwargs)
                raise DshBatchError('unknown final', receipt=root / 'receipt.json',
                    usage={'model_calls': 39, 'input_tokens': 184144,
                           'output_tokens': 53608}, provider_failure=failure)
            foundry.validator_client.complete = unavailable
            original_read = Path.read_bytes
            def changed_contract(path):
                value = original_read(path)
                return value + b'\n# contract fixture\n' if path.name == 'capability_foundry.py' else value
            from scisaurus.runtime.capability_foundry import candidate_prompt
            def changed_assignment(*args, **kwargs):
                value = candidate_prompt(*args, **kwargs)
                value['contract_description'] = 'Updated transport contract.'
                return value
            with patch('scisaurus.runtime.dsh_batch.DshAuthorClient', return_value=author):
                with self.assertRaises(DshBatchError):
                    foundry.generate('bounded comparison', work_cache=cache)
                with patch.object(Path, 'read_bytes', changed_contract), \
                        patch.object(foundry, '_execute', wraps=foundry._execute) as execute, \
                        self.assertRaises(DshBatchError) as caught:
                    foundry.generate('bounded comparison', work_cache=cache)
                self.assertEqual(execute.call_count, 0)
                with patch('scisaurus.runtime.capability_foundry.candidate_prompt',
                           side_effect=changed_assignment), \
                        patch.object(foundry, '_execute', wraps=foundry._execute) as execute, \
                        self.assertRaises(DshBatchError) as caught:
                    foundry.generate('bounded comparison', work_cache=cache)
                self.assertEqual(execute.call_count, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(author.calls, 1)
            self.assertEqual(foundry.reviewer_client.calls, 0)
            self.assertEqual(caught.exception.provider_failure, failure)
            self.assertEqual(caught.exception.usage['model_calls'], 40)
            self.assertEqual(caught.exception.usage['output_tokens'], 53608)


if __name__ == '__main__':
    unittest.main()
