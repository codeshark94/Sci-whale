"""Capability imagination retains held versions and one stopped primary dispatch."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.concept_invention import concept_contract, concept_files, validate_concepts
from scisaurus.runtime.fresh_intake import IntakePrimaryClient, run_once, sha
from scisaurus.runtime.intake_recovery import prepare_concept_step
import scisaurus.tests.test_invention_review as review_tests


class ConceptInventionTests(unittest.TestCase):
    def setUp(self):
        self.f = review_tests.InventionReviewTests('test_one_same_primary_review_reads_missing_input_first_then_stops')
        self.f.setUp(); self.owner = self.f.owner
        review_ticket = self.f.prepare()
        record = self.f.run_mocked(review_ticket)
        self.receipt = Path(record['receipt']); job = self.receipt.parent
        state = json.loads(self.owner.session.path.read_bytes())
        receipt = json.loads(self.receipt.read_bytes())
        response_path = job / 'outputs/response.json'
        response_path.write_bytes(canonical_bytes(record['response']))
        receipt.update(outputs={'response.json': sha(response_path.read_bytes())},
            development_session={'binding': self.owner.binding, 'prior_receipts': state['calls']})
        self.receipt.write_bytes(canonical_bytes(receipt)); self.identity = sha(self.receipt.read_bytes())
        from scisaurus.runtime.input_delivery import verify_full_input_reads
        record['input_read_coverage'] = verify_full_input_reads(self.receipt, first_input='unread-input.txt')
        self.marker = review_ticket.with_suffix('.attempt.json')
        raw = canonical_bytes(record); self.marker.write_bytes(raw)
        (self.owner.project / 'intake-controller' / (sha(raw) + '.json')).write_bytes(raw)
        state['calls'].append({'receipt': str(self.receipt), 'sha256': self.identity, 'usage': receipt['usage']})
        self.owner.session.state = state; self.owner.session.save()
        self.before = self.owner.session.path.read_bytes()
        self.protected = {Path(call['receipt']): Path(call['receipt']).read_bytes() for call in state['calls']}

    def tearDown(self):
        self.f.tearDown()

    def prepare(self):
        return prepare_concept_step(self.owner.root, receipt_sha256=self.identity, attempt_path=self.marker)

    def preflight(self, ticket):
        return self.f.preflight(ticket)

    def value(self, request):
        value = deepcopy(concept_contract()['concept_design']); value['decision'] = 'propose'
        value['concepts'][0]['capability_id'] = value['imagined_capabilities'][0]['id']
        value['concepts'][0]['held_candidate_separation'] = {key: 'different function and physical cause' for key in request['held_candidate_keys']}
        return value

    def test_all_candidates_held_without_history_or_backend_changes(self):
        ticket = self.prepare(); request = json.loads(ticket.read_bytes())['step_request']
        self.preflight(ticket)
        self.assertEqual(len(request['held_catalog']), 5)
        self.assertEqual(set(request['held_candidate_ids']), {'c0','c1','c2','hypothesis-one','hypothesis-two'})
        self.assertEqual(self.owner.session.path.read_bytes(), self.before)
        self.assertTrue(all(path.read_bytes() == raw for path, raw in self.protected.items()))
        self.assertEqual(self.prepare(), ticket)
        self.assertIn('held-candidates.json', concept_files(request))

    def test_bad_hold_process_and_missing_read_proof_refuse_dispatch(self):
        ticket = self.prepare()
        for field in ['held_catalog', 'rules', 'prior_input_read_coverage']:
            body = json.loads(ticket.read_bytes())
            if field == 'held_catalog': body['step_request'][field][0]['function'] = 'different'
            elif field == 'rules': body['step_request'][field] = ['select a familiar solver first']
            else: body['step_request'][field]['complete'] = False
            raw = canonical_bytes(body); changed = ticket.parent / (sha(raw) + '.json'); changed.write_bytes(raw)
            with self.assertRaises(StateError): self.preflight(changed)

    def test_structure_prediction_and_unverified_boundaries(self):
        request = json.loads(self.prepare().read_bytes())['step_request']; value = self.value(request)
        validate_concepts(value, request, [])
        for field in ['components', 'interfaces', 'causal_chain', 'falsifier', 'novelty_status', 'held_identity']:
            changed = deepcopy(value); c = changed['concepts'][0]
            if field == 'components': c['structure'][field] = []
            elif field == 'interfaces': c['structure'][field] = ''
            elif field == 'causal_chain': c['working_principle'][field] = ['effect']
            elif field == 'falsifier': c['possibility_test'][field] = ''
            elif field == 'novelty_status': c[field] = 'established'
            else: c['id'] = request['held_candidate_ids'][0]
            with self.assertRaises(ValidationError): validate_concepts(changed, request, [])
        validate_concepts({'decision':'abstain','imagined_capabilities':[],'concepts':[],
            'limitations':['No defensible physical link']}, request, [])

    def test_new_ability_cannot_repeat_held_function(self):
        request = json.loads(self.prepare().read_bytes())['step_request']; value = self.value(request)
        value['imagined_capabilities'][0]['new_ability'] = request['held_catalog'][0]['function']
        with self.assertRaises(ValidationError): validate_concepts(value, request, [])

    def run_mocked(self, ticket, *, wrong=False, missing=False):
        args = self.preflight(ticket); root, checkpoint, backend, assignment, protected = args
        assignment['scientific_software_tools'] = {'actions':{},'response':{'tool_action':{}},'rules':[]}
        request = json.loads(ticket.read_bytes())['step_request']
        projected = deepcopy(assignment); projected['concept_invention'] = request
        client_stub = object.__new__(IntakePrimaryClient)
        inputs = client_stub.input_files(projected, 'Imagine capabilities and design their structures.')
        delivered = json.loads(inputs['assignment.json'])['concept_invention']
        self.assertNotIn('held_sources', delivered); self.assertNotIn('held_catalog', delivered)
        self.assertIn('Explore qualitatively different capabilities boldly', client_stub.task(projected))
        job = self.owner.project / 'intake-jobs/concept'; (job / 'input').mkdir(parents=True); (job / 'outputs').mkdir()
        for name, raw in inputs.items(): (job / 'input' / name).write_bytes(raw)
        receipt = json.loads(self.receipt.read_bytes()); receipt.update(job_directory=str(job),
            input_sha256={name:sha(raw) for name,raw in inputs.items()})
        rpath = job / 'receipt.json'; rpath.write_bytes(canonical_bytes(receipt))
        review_tests.events_for(job, receipt, skip=('assignment.json',2) if missing else None)
        response = {'tool_action':{}} if wrong else {'concept_design':self.value(request)}
        result = SimpleNamespace(response_metadata={'receipt':str(rpath)},usage=self.owner.usage,json_object=lambda:response)
        with patch('scisaurus.runtime.intake_guard.verify_publication',return_value={'offline_transport_fixture':True}), \
             patch('scisaurus.runtime.fresh_intake.preflight',return_value=args), \
             patch('scisaurus.runtime.fresh_intake.IntakePrimaryClient') as client, \
             patch('scisaurus.runtime.fresh_intake.SoftwareWorkbench',side_effect=AssertionError('no scientific tool dispatch')):
            client.return_value.complete.return_value = result
            try:
                return run_once(root,sys.executable,recovery={'source':'offline capacity fixture','observed_at':'2026-01-01T00:00:00Z'},continuation=ticket)
            finally:
                self.assertEqual(client.return_value.complete.call_count,1)

    def test_one_same_primary_concept_response_then_stop_without_admission(self):
        ticket = self.prepare(); record = self.run_mocked(ticket)
        self.assertEqual(record['status'],'stopped_with_unreviewed_concepts')
        self.assertTrue(record['input_read_coverage']['complete'])
        self.assertEqual(record['scientific_admission'],'not_assessed')
        control = json.loads((self.owner.project / 'output/run-control.json').read_bytes())
        self.assertTrue(control['stop_requested']); self.assertIsNone(control.get('research_resume_authorization_ref'))
        self.assertEqual(len(control['candidate_statuses']),5)
        self.assertTrue(all(x=='held' for x in control['candidate_statuses'].values()))
        self.assertTrue(all(path.read_bytes()==raw for path,raw in self.protected.items()))
        with self.assertRaises(StateError): self.prepare()

    def test_wrong_response_stops_with_paid_usage_no_tools_or_repair(self):
        ticket = self.prepare()
        with self.assertRaises(ValidationError): self.run_mocked(ticket,wrong=True)
        record = json.loads(ticket.with_suffix('.attempt.json').read_bytes())
        self.assertEqual(record['status'],'stopped_on_error'); self.assertEqual(record['usage'],self.owner.usage)
        self.assertTrue(json.loads((self.owner.project / 'output/run-control.json').read_bytes())['stop_requested'])

    def test_incomplete_input_stops_without_another_primary_call(self):
        ticket = self.prepare()
        with self.assertRaisesRegex(StateError,'input delivery is incomplete'): self.run_mocked(ticket,missing=True)
        record = json.loads(ticket.with_suffix('.attempt.json').read_bytes())
        self.assertEqual(record['status'],'stopped_on_error'); self.assertIn('response',record)
        self.assertTrue(json.loads((self.owner.project / 'output/run-control.json').read_bytes())['stop_requested'])
