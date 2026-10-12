"""Completed primary proposals bind one unchanged literature continuation."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import preflight, sha
from scisaurus.runtime.intake_recovery import prepare_literature_step
from scisaurus.tests.test_intake_recovery import ExplicitPreparationTests


class CompletedLiteratureStepTests(unittest.TestCase):
    def setUp(self):
        self.f=ExplicitPreparationTests('test_partial_unknown_is_retained_and_idempotent');self.f.setUp()
        f=self.f
        f.config['command']=[sys.executable]
        fields=['id','question','mechanism','closest_work','novelty','theoretical_limits','utility','pilot','exclusion_comparison']
        candidates=[{k: ('c'+str(i) if k=='id' else 'literal-'+k) for k in fields} for i in range(3)]
        candidates[0]['pilot']='α\n'+('long literal '*300)
        self.proposal={'topic_selection':{'decision':'propose','selected_candidate_id':'c0','selection_rationale':'unreviewed',
            'limitations':['no literature'],'candidates':candidates}}
        assignment={'output_contract':{'topic_selection':{k:v for k,v in self.proposal['topic_selection'].items()}},
                    'excluded_topics':[],'laboratory':{'config_identity':'lab','attestation_identity':'att'}}
        (f.job/'input/assignment.json').write_bytes(canonical_bytes(assignment))
        output=f.job/'outputs/response.json';output.parent.mkdir();output.write_bytes(canonical_bytes(self.proposal));self.output=output
        f.body.update(status='completed',output_directory='outputs',outputs={'response.json':sha(output.read_bytes())},
            config_sha256=sha(canonical_bytes(f.config)),input_sha256={'assignment.json':sha((f.job/'input/assignment.json').read_bytes())})
        f.receipt.write_bytes(canonical_bytes(f.body));f.sha=sha(f.receipt.read_bytes())
        f.session.state.update(config_sha256=f.body['config_sha256'],active_job=None,
            calls=[{'receipt':str(f.receipt),'sha256':f.sha,'usage':f.usage}]);f.session.save()
        self.before=f.session.path.read_bytes()
        inputs=f.root/'inputs';inputs.mkdir();(inputs/'assignment.json').write_bytes(canonical_bytes(assignment))
        checkpoint={'mission_id':f.root.name,'development_session':f.binding,'session_id':'original',
            'input_sha256':{'inputs/assignment.json':sha((inputs/'assignment.json').read_bytes())},
            'laboratory_config_path':'fake-lab','laboratory_attestation_path':'fake-attestation'}
        raw=canonical_bytes(checkpoint);cp=f.root/'checkpoints'/(sha(raw)+'.json');cp.write_bytes(raw)
        (f.root/'preparation.json').write_bytes(canonical_bytes({'checkpoint_path':str(cp),'checkpoint_sha256':sha(raw)}))
        backend_path=Path(f.attempt_body['backend_binding']);backend_path.write_bytes(canonical_bytes({'backend':f.config}))
        f.attempt_body.update(status='stopped_with_unreviewed_proposal',response=self.proposal,preparation_sha256=sha(raw))
        f.seal_attempt()
        control=f.project/'output/run-control.json';value=json.loads(control.read_bytes())
        value['attempt']=str(f.attempt);control.write_bytes(canonical_bytes(value))

    def tearDown(self):self.f.tearDown()
    def prepare(self):return prepare_literature_step(self.f.root,receipt_sha256=self.f.sha,attempt_path=self.f.attempt)
    def preflight(self,ticket):
        lab=SimpleNamespace(identity='lab',attestation={})
        with patch('scisaurus.runtime.fresh_intake.validate_batch_config',side_effect=lambda x:x),\
             patch('scisaurus.runtime.fresh_intake.verify_batch_runtime'),\
             patch('scisaurus.runtime.fresh_intake.LaboratoryBinding.load',return_value=lab),\
             patch('scisaurus.runtime.fresh_intake.load_attestation',return_value={'config_sha256':'lab','attestation_sha256':'att'}):
            return preflight(self.f.root,sys.executable,continuation=ticket)

    def test_completed_proposal_is_lossless_and_session_history_unchanged(self):
        ticket=self.prepare();value=json.loads(ticket.read_bytes())
        actual=[{k:''.join(v) for k,v in c.items()} for c in value['step_request']['candidate_literal_chunks']]
        self.assertEqual(actual,self.proposal['topic_selection']['candidates'])
        self.assertTrue(all(len(x)<=1000 for c in value['step_request']['candidate_literal_chunks'] for v in c.values() for x in v))
        self.assertEqual(self.f.session.path.read_bytes(),self.before)
        self.preflight(ticket)
        self.assertEqual(self.prepare(),ticket)
        ticket.with_suffix('.attempt.json').write_bytes(b'{}')
        with self.assertRaises(StateError):self.prepare()

    def test_foreign_marker_and_mutated_proposal_are_rejected(self):
        foreign=self.f.root/'foreign.json';foreign.write_bytes(self.f.attempt.read_bytes())
        with self.assertRaises(StateError):prepare_literature_step(self.f.root,receipt_sha256=self.f.sha,attempt_path=foreign)
        self.output.write_bytes(b'{}')
        with self.assertRaises(Exception):self.prepare()
        self.assertEqual(self.f.session.path.read_bytes(),self.before)

    def test_tampered_candidate_or_predecessor_status_cannot_dispatch(self):
        original=self.prepare();value=json.loads(original.read_bytes())
        value['step_request']['candidate_literal_chunks'][0]['question']=['different question']
        raw=canonical_bytes(value);path=original.parent/(sha(raw)+'.json');path.write_bytes(raw)
        with self.assertRaisesRegex(StateError,'changed its current candidates'):self.preflight(path)
        value=json.loads(original.read_bytes());value['prior_receipt_status']='result_unknown'
        raw=canonical_bytes(value);path=original.parent/(sha(raw)+'.json');path.write_bytes(raw)
        with self.assertRaises(StateError):self.preflight(path)
        self.assertEqual(self.f.session.path.read_bytes(),self.before)
