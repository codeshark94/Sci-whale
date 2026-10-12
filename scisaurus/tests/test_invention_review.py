"""Explicit reviews retain the predecessor and read undisplayed evidence first."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import IntakePrimaryClient, run_once, sha
from scisaurus.runtime.intake_recovery import prepare_invention_review
from scisaurus.runtime.invention import invention_contract
from scisaurus.runtime.invention_review import review_contract, review_files, validate_review
import scisaurus.tests.test_invention_process as invention_tests


def events_for(job, receipt, *, order=None, skip=None):
    events = []
    for name in order or receipt['input_sha256']:
        path = job / 'input' / name
        text = '\n'.join(f'{i}: {line}' for i, line in enumerate(path.read_text().splitlines(), 1)
                         if skip != (name, i))
        for kind, data in [('tool/call', {'name': 'read', 'callId': name,
                                         'arguments': json.dumps({'file_path': str(path)})}),
                           ('tool/result', {'message': {'content': [{'toolCallId': name,
                                'isError': False, 'content': [{'text': f'<path>{path}</path>\n{text}'}]}]}})]:
            events.append({'params': {'sessionId': receipt['session_id'], 'event': {
                'type': kind, 'data': {'turn': 1, **data}}}})
    (job / 'events.jsonl').write_text('\n'.join(json.dumps(row) for row in events))


class InventionReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = invention_tests.InventionProcessTests('test_same_completed_session_holds_all_candidates_and_preserves_full_sources')
        self.fixture.setUp()
        self.owner = self.fixture.owner
        ticket = json.loads(self.fixture.prepare().read_bytes())
        self.process = ticket['step_request']
        invention = deepcopy(invention_contract()['invention']); invention['decision'] = 'propose'
        first = invention['directions'][0]
        first['id'] = 'hypothesis-one'; first['held_candidate_separation'] = {
            identity: 'distinct function and mechanism' for identity in self.fixture.ids}
        second = deepcopy(first); second['id'] = 'hypothesis-two'; invention['directions'].append(second)
        self.response = {'invention': invention}
        job = self.owner.project / 'intake-jobs/invention'; (job / 'input').mkdir(parents=True); (job / 'outputs').mkdir()
        assignment = {'excluded_topics': [], 'invention_process': self.process}
        (job / 'input/assignment.json').write_text(json.dumps(assignment, ensure_ascii=False, indent=2))
        (job / 'input/system-contract.txt').write_text('Preserve observations and hypotheses.')
        (job / 'outputs/response.json').write_bytes(canonical_bytes(self.response))
        state = json.loads(self.owner.session.path.read_bytes())
        receipt = deepcopy(self.owner.body)
        receipt.update(job_directory=str(job), transport_turns=[1],
            development_session={'binding': self.owner.binding, 'prior_receipts': state['calls']},
            input_sha256={name: sha((job / 'input' / name).read_bytes()) for name in ['assignment.json', 'system-contract.txt']},
            outputs={'response.json': sha((job / 'outputs/response.json').read_bytes())})
        self.receipt = job / 'receipt.json'; self.receipt.write_bytes(canonical_bytes(receipt))
        self.identity = sha(self.receipt.read_bytes())
        events_for(job, receipt, skip=('assignment.json', 2))
        state['calls'].append({'receipt': str(self.receipt), 'sha256': self.identity, 'usage': receipt['usage']})
        self.owner.session.state = state; self.owner.session.save()
        record = json.loads(self.fixture.marker.read_bytes())
        record.update(receipt=str(self.receipt), response=self.response, status='stopped_with_unreviewed_invention')
        raw = canonical_bytes(record); self.marker = self.owner.project / 'intake-controller/invention.attempt.json'
        self.marker.write_bytes(raw); (self.marker.parent / (sha(raw) + '.json')).write_bytes(raw)
        control = self.owner.project / 'output/run-control.json'; value = json.loads(control.read_bytes())
        value['attempt'] = str(self.marker); control.write_bytes(canonical_bytes(value))
        self.before = self.owner.session.path.read_bytes()
        self.previous = {path: path.read_bytes() for path in [self.receipt, job / 'input/assignment.json', job / 'outputs/response.json']}

    def tearDown(self):
        self.fixture.tearDown()

    def prepare(self):
        return prepare_invention_review(self.owner.root, receipt_sha256=self.identity, attempt_path=self.marker)

    def preflight(self, ticket):
        return self.fixture.f.preflight(ticket)

    def response_value(self):
        value = deepcopy(review_contract()['invention_review'])
        row = value['reviews'][0]; row.update(id='hypothesis-one', mechanism_status='unresolved',
            novelty_status='unverified', decision='hold', source_evidence=[])
        second = deepcopy(row); second['id'] = 'hypothesis-two'; value['reviews'].append(second)
        return value

    def test_prepare_delivers_exact_unread_lines_and_preserves_two_hypotheses(self):
        ticket = self.prepare(); request = json.loads(ticket.read_bytes())['step_request']
        self.preflight(ticket)
        self.assertEqual(request['hypothesis_ids'], ['hypothesis-one', 'hypothesis-two'])
        files = review_files(request)
        self.assertEqual(files['unread-input.txt'], (self.receipt.parent / 'input/assignment.json').read_bytes().splitlines(keepends=True)[1])
        def unchunk(value):
            if isinstance(value, dict):
                if set(value) == {'literal_chunks'}: return ''.join(value['literal_chunks'])
                return {key: unchunk(item) for key, item in value.items()}
            if isinstance(value, list): return [unchunk(item) for item in value]
            return value
        self.assertEqual(unchunk(json.loads(files['hypotheses.json'])), self.response)
        self.assertEqual(self.owner.session.path.read_bytes(), self.before)
        self.assertTrue(all(path.read_bytes() == raw for path, raw in self.previous.items()))
        self.assertEqual(self.prepare(), ticket)
        ticket.with_suffix('.attempt.json').write_text('{}')
        with self.assertRaises(StateError): self.prepare()

    def test_preflight_rejects_changed_missing_range_or_hypothesis_identity(self):
        ticket = self.prepare()
        for key in ['hypothesis_ids', 'prior_input_read_coverage']:
            body = json.loads(ticket.read_bytes())
            if key == 'hypothesis_ids': body['step_request'][key] = ['another']
            else: body['step_request'][key]['files']['assignment.json']['missing_ranges'] = [[3, 3]]
            raw = canonical_bytes(body); changed = ticket.parent / (sha(raw) + '.json'); changed.write_bytes(raw)
            with self.assertRaises(StateError): self.preflight(changed)

    def test_response_rejects_new_hypotheses_novelty_admission_and_uncaptured_sources(self):
        request = json.loads(self.prepare().read_bytes())['step_request']; value = self.response_value()
        validate_review(value, request)
        for change in ['id', 'novelty_status', 'source_evidence']:
            altered = deepcopy(value)
            altered['reviews'][0][change] = {'id': 'new-topic', 'novelty_status': 'confirmed',
                'source_evidence': [review_contract()['invention_review']['reviews'][0]['source_evidence'][0]]}[change]
            with self.assertRaises(ValidationError): validate_review(altered, request)

    def run_mocked(self, ticket, *, first=True, wrong=False):
        args = self.preflight(ticket); root, checkpoint, backend, assignment, protected = args
        assignment['scientific_software_tools'] = {'actions': {}, 'response': {'tool_action': {}}, 'rules': []}
        request = json.loads(ticket.read_bytes())['step_request']
        projected = deepcopy(assignment); projected['invention_review'] = request
        client_stub = object.__new__(IntakePrimaryClient)
        inputs = client_stub.input_files(projected, 'Self-review current hypotheses.')
        self.assertNotIn('prior_assignment_path', json.loads(inputs['assignment.json'])['invention_review'])
        job = self.owner.project / 'intake-jobs/review'; (job / 'input').mkdir(parents=True); (job / 'outputs').mkdir()
        for name, raw in inputs.items(): (job / 'input' / name).write_bytes(raw)
        receipt = json.loads(self.receipt.read_bytes()); receipt.update(job_directory=str(job),
            input_sha256={name: sha(raw) for name, raw in inputs.items()})
        rpath = job / 'receipt.json'; rpath.write_bytes(canonical_bytes(receipt))
        order = ['unread-input.txt', 'assignment.json', 'system-contract.txt', 'hypotheses.json']
        if not first: order = ['assignment.json', 'unread-input.txt', 'system-contract.txt', 'hypotheses.json']
        events_for(job, receipt, order=order)
        response = {'tool_action': {}} if wrong else {'invention_review': self.response_value()}
        result = SimpleNamespace(response_metadata={'receipt': str(rpath)}, usage=self.owner.usage, json_object=lambda: response)
        with patch('scisaurus.runtime.fresh_intake.preflight', return_value=args), \
             patch('scisaurus.runtime.fresh_intake.IntakePrimaryClient') as client, \
             patch('scisaurus.runtime.fresh_intake.SoftwareWorkbench', side_effect=AssertionError('tools must stay inactive')):
            client.return_value.complete.return_value = result
            try:
                return run_once(root, sys.executable, recovery={'source': 'offline capacity fixture', 'observed_at': '2026-01-01T00:00:00Z'}, continuation=ticket)
            finally:
                self.assertEqual(client.return_value.complete.call_count, 1)

    def test_one_same_primary_review_reads_missing_input_first_then_stops(self):
        ticket = self.prepare(); record = self.run_mocked(ticket)
        self.assertEqual(record['status'], 'stopped_with_unadmitted_self_review')
        self.assertTrue(record['input_read_coverage']['complete'])
        control = json.loads((self.owner.project / 'output/run-control.json').read_bytes())
        self.assertTrue(control['stop_requested']); self.assertIsNone(control.get('research_resume_authorization_ref'))
        self.assertTrue(all(path.read_bytes() == raw for path, raw in self.previous.items()))
        with self.assertRaises(StateError): self.prepare()

    def test_fully_read_but_wrong_order_stops_with_response_usage_and_no_retry(self):
        ticket = self.prepare()
        with self.assertRaisesRegex(StateError, 'out of order'): self.run_mocked(ticket, first=False)
        record = json.loads(ticket.with_suffix('.attempt.json').read_bytes())
        self.assertEqual(record['status'], 'stopped_on_error')
        self.assertIn('response', record); self.assertEqual(record['usage'], self.owner.usage)
        self.assertTrue(json.loads((self.owner.project / 'output/run-control.json').read_bytes())['stop_requested'])

    def test_wrong_response_stops_without_tools_or_repair(self):
        ticket = self.prepare()
        with self.assertRaises(ValidationError): self.run_mocked(ticket, wrong=True)
        self.assertEqual(json.loads(ticket.with_suffix('.attempt.json').read_bytes())['status'], 'stopped_on_error')
