"""Stopped intake accounting, configuration lineage and explicit continuation gates."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.events import ControlStore
from scisaurus.core.schema import canonical_bytes
from scisaurus.core.store import ArtifactStore
from scisaurus.runtime.development_session import DevelopmentSession, development_session_binding
from scisaurus.runtime.intake_recovery import reasoning_off_composition
from scisaurus.runtime.dsh_batch import DshBatchRunner
from scisaurus.tests.test_development_session import DevelopmentSessionTests, FAKE


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class IntakeAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name).resolve() / 'mission/composer'
        self.project.mkdir(parents=True)
        self.binding = development_session_binding(self.project)
        self.session = DevelopmentSession(self.binding)
        self.config = {"provider": "fixed", "model": "fixed"}
        self.job = self.project / 'intake-jobs/job'
        (self.job / 'input').mkdir(parents=True)
        (self.job / 'input/assignment.json').write_bytes(b'{"question":null}')
        self.receipt = self.job / 'receipt.json'
        self.usage = dict(model_calls=5, input_tokens=9273, output_tokens=1229, cache_read_tokens=3916)
        self.body = dict(status='result_unknown', process_reaped=True, pid=999999999,
            job_directory=str(self.job), session_id='original', config_sha256=digest(canonical_bytes(self.config)),
            usage=self.usage, development_session=dict(binding=self.binding, prior_receipts=[]),
            input_sha256={'assignment.json': digest((self.job/'input/assignment.json').read_bytes())})
        self.receipt.write_bytes(canonical_bytes(self.body))
        self.sha = digest(self.receipt.read_bytes())
        self.session.state = dict(schema_version='dsh-development-session-1', binding=self.binding,
            session_id='original', config_sha256=self.body['config_sha256'], calls=[], active_job=str(self.receipt))
        self.session.save()
        self.attempt = self.project/'intake-controller/attempt.json'
        self.attempt.parent.mkdir()
        self.attempt_body = dict(schema_version='fresh-intake-attempt-1', status='stopped_on_error',
            scientific_admission='not_assessed', session_id='original', receipt=str(self.receipt), usage=self.usage)
        self.seal_attempt()
        self.control = ControlStore(self.project)
        self.store = ArtifactStore(self.control)
        self.store.init_project()

    def tearDown(self):
        self.control.close()
        self.temp.cleanup()

    def seal_attempt(self):
        raw = canonical_bytes(self.attempt_body)
        self.attempt.write_bytes(raw)
        (self.attempt.parent/(digest(raw)+'.json')).write_bytes(raw)

    def settle(self):
        return self.session.reconcile_stopped_attempt(self.config, self.store,
            attempt_path=self.attempt, receipt_sha256=self.sha)

    def test_partial_unknown_is_retained_and_idempotent(self):
        raw = self.receipt.read_bytes()
        proof = self.settle()
        state = json.loads(self.session.path.read_bytes())
        self.assertIsNone(state['active_job'])
        self.assertEqual(state['calls'][0]['usage'], self.usage)
        self.assertEqual(self.receipt.read_bytes(), raw)
        value = json.loads(self.store.read_body(proof['body_hash']))
        self.assertEqual(value['billing_completeness'], 'partial_unfinalized')
        self.assertEqual(value['disposition'], 'paid_unknown_history_only')
        self.assertEqual(self.settle()['artifact_ref'], proof['artifact_ref'])
        with self.session.lease(self.config, time.monotonic()+1):
            self.assertEqual(self.session.state['session_id'], 'original')

    def test_foreign_hash_and_usage_are_rejected_without_releasing_owner(self):
        self.attempt_body['usage'] = dict(self.usage, output_tokens=0)
        self.seal_attempt()
        with self.assertRaises(ValidationError): self.settle()
        self.assertEqual(json.loads(self.session.path.read_bytes())['active_job'], str(self.receipt))

    def test_live_process_and_changed_input_are_rejected(self):
        with patch('scisaurus.runtime.development_session.os.kill', return_value=None):
            with self.assertRaisesRegex(ValidationError, 'still alive'): self.settle()
        (self.job/'input/assignment.json').write_bytes(b'{}')
        with self.assertRaises(ValidationError): self.settle()

    def test_configuration_lineage_keeps_old_receipt_and_same_session(self):
        self.settle()
        prior_raw = self.session.path.read_bytes()
        directory = self.project/'intake-controller/recoveries/one'
        directory.mkdir(parents=True)
        snapshot = directory/'previous-session.json'; snapshot.write_bytes(prior_raw)
        changed = dict(self.config, composition='explicit-none')
        proof = dict(schema_version='recovered-development-backend-1', development_session=self.binding,
            session_id='original', previous_session=str(snapshot), previous_session_sha256=digest(prior_raw),
            previous_config_sha256=self.body['config_sha256'], previous_backend=self.config, backend=changed,
            backend_sha256=digest(canonical_bytes(changed)))
        path = directory/'binding.json'; path.write_bytes(canonical_bytes(proof))
        self.session.bind_recovered_backend(changed, expected_state_sha256=digest(prior_raw), binding_ref=path)
        with self.session.lease(changed, time.monotonic()+1):
            self.assertEqual(self.session.state['session_id'], 'original')
            self.assertEqual(self.session.transport_history[0]['config_sha256'], self.body['config_sha256'])
        snapshot.write_bytes(b'{}')
        with self.assertRaises((ValidationError, KeyError)):
            with self.session.lease(changed, time.monotonic()+1): pass


class ReasoningTransportTests(unittest.TestCase):
    def test_explicit_none_targets_openai_endpoint_without_model_change(self):
        original = [{'config': {'providers': {'fixed': {'api': 'openai-completions', 'reasoning':'off',
            'compat': {'thinkingFormat':'deepseek'}, 'models': [{'id':'same','reasoningEfforts':{'off':None,'high':'high'}}]}}}}]
        result = reasoning_off_composition(original, 'fixed', 'same')[0]['config']['providers']['fixed']
        self.assertEqual(result['compat']['thinkingFormat'], 'openai')
        self.assertTrue(result['compat']['supportsReasoningEffort'])
        self.assertEqual(result['models'][0]['reasoningEfforts']['off'], 'none')
        self.assertIsNone(original[0]['config']['providers']['fixed']['models'][0]['reasoningEfforts']['off'])
        with self.assertRaises(StateError): reasoning_off_composition(original,'fixed','another')


class ReasoningGuardTests(DevelopmentSessionTests):
    def test_historical_reasoning_replay_does_not_abort_new_request(self):
        self.dispatch()
        script = Path(self.config['command'][1])
        script.write_text(FAKE.replace("emit({'id':r['id'],'result':{'sessionId':p['sessionId']}})",
            "emit({'method':'session.event','params':{'sessionId':p['sessionId'],'event':{'type':'assistant/chunk','data':{'turn':1,'step':1,'chunk':{'type':'reasoning-delta','text':'historical'}}}}})\n  emit({'id':r['id'],'result':{'sessionId':p['sessionId']}})"))
        # Use the original pinned fake command, with its mock replay injected at subprocess launch.
        self.config['pinned_files'][str(script)] = digest(script.read_bytes())
        state = json.loads((Path(self.binding['root'])/'session.json').read_bytes())
        # Only this offline test changes pin identity; production requires a sealed revision.
        state['config_sha256'] = digest(canonical_bytes(self.config))
        receipt_path = Path(state['calls'][0]['receipt'])
        r = json.loads(receipt_path.read_bytes()); r['config_sha256']=state['config_sha256']
        receipt_path.write_bytes(canonical_bytes(r));state['calls'][0]['sha256']=digest(receipt_path.read_bytes())
        (Path(self.binding['root'])/'session.json').write_bytes(canonical_bytes(state))
        result = DshBatchRunner(self.config, root=self.project/'next', development_session=self.binding,
            forbid_reasoning=True).run('next', inputs={'assignment.json':b'{}'}, outputs=['answer.txt'])
        self.assertEqual(result['usage']['model_calls'], 1)

    def test_new_reasoning_aborts_without_next_model_step(self):
        script = Path(self.config['command'][1])
        script.write_text(FAKE.replace("event('assistant/message',", "event('step/start',{'turn':n,'step':1})\n  event('assistant/chunk',{'turn':n,'step':1,'chunk':{'type':'reasoning-delta','text':'unexpected'}})\n  event('assistant/message',"))
        self.config['pinned_files'][str(script)] = digest(script.read_bytes())
        with self.assertRaisesRegex(Exception, 'reasoning-off') as caught:
            DshBatchRunner(self.config, root=self.project/'next', development_session=self.binding,
                forbid_reasoning=True).run('next',inputs={'assignment.json':b'{}'},outputs=['answer.txt'])
        receipt=json.loads(Path(caught.exception.receipt).read_bytes())
        self.assertTrue(receipt['process_reaped'])
        self.assertEqual(receipt['status'],'result_unknown')
        self.assertEqual(receipt['usage']['model_calls'],1)


class ExplicitPreparationTests(IntakeAccountingTests):
    def setUp(self):
        super().setUp()
        self.root = self.project.parent
        composition = self.project/'composition.json'
        composition.write_bytes(canonical_bytes([{'config': {'providers': {'fixed': {
            'api':'openai-completions','reasoning':'off','models':[{'id':'fixed',
            'reasoningEfforts':{'off':None,'high':'high'}}]}}}}]))
        self.config.update(composition=str(composition), read_roots=[], pinned_files={})
        self.body['config_sha256']=digest(canonical_bytes(self.config))
        self.receipt.write_bytes(canonical_bytes(self.body)); self.sha=digest(self.receipt.read_bytes())
        self.session.state['config_sha256']=self.body['config_sha256'];self.session.save()
        checkpoint = dict(development_session=self.binding, session_id='original')
        raw = canonical_bytes(checkpoint)
        path = self.root/'checkpoints'/(digest(raw)+'.json');path.parent.mkdir();path.write_bytes(raw)
        (self.root/'preparation.json').write_bytes(canonical_bytes(dict(checkpoint_path=str(path),checkpoint_sha256=digest(raw))))
        backend_proof = self.attempt.parent/'backend.json'
        backend_proof.write_bytes(canonical_bytes(dict(backend=self.config)))
        self.attempt_body['backend_binding']=str(backend_proof);self.seal_attempt()
        output=self.project/'output';output.mkdir()
        (output/'run-control.json').write_bytes(canonical_bytes(dict(stop_requested=True,generation='stopped')))
        self.validation = patch('scisaurus.runtime.intake_recovery.validate_batch_config',side_effect=lambda v:v)
        self.verification = patch('scisaurus.runtime.intake_recovery.verify_batch_runtime')
        self.validation.start();self.verification.start()

    def tearDown(self):
        self.validation.stop();self.verification.stop();super().tearDown()

    def prepare(self):
        from scisaurus.runtime.intake_recovery import prepare_continuation
        return prepare_continuation(self.root,receipt_sha256=self.sha)

    def test_local_binding_interruption_resumes_without_another_model_dispatch(self):
        with patch.object(DevelopmentSession,'bind_recovered_backend',side_effect=OSError('local interruption')):
            with self.assertRaises(OSError): self.prepare()
        settled = json.loads(self.session.path.read_bytes())
        self.assertIsNone(settled['active_job'])
        self.assertEqual(len(settled['calls']),1)
        ticket=self.prepare()
        state=self.session.path.read_bytes()
        self.assertEqual(self.prepare(),ticket)
        self.assertEqual(self.session.path.read_bytes(),state)
        ticket.with_suffix('.attempt.json').write_bytes(b'{}')
        with self.assertRaises(StateError): self.prepare()

    def test_tampered_published_backend_proof_never_mutates_session(self):
        with patch.object(DevelopmentSession,'bind_recovered_backend',side_effect=OSError('local interruption')):
            with self.assertRaises(OSError): self.prepare()
        state=self.session.path.read_bytes()
        directory=self.project/'intake-controller/recoveries'/self.sha
        ticket=next(p for p in directory.glob('*.json')
            if isinstance(json.loads(p.read_bytes()),dict) and json.loads(p.read_bytes()).get('schema_version')=='explicit-intake-continuation-1')
        proof=Path(json.loads(ticket.read_bytes())['backend_binding'])
        value=json.loads(proof.read_bytes());value['transport_change']='tampered'
        proof.chmod(0o644);proof.write_bytes(canonical_bytes(value))
        with self.assertRaisesRegex(StateError,'proof changed'):self.prepare()
        self.assertEqual(self.session.path.read_bytes(),state)
