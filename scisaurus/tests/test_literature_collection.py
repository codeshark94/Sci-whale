"""One-shot literature acquisition preserves captures and stops on the first error."""
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.runtime.literature_collection import collect_literature
from scisaurus.runtime.fresh_intake import sha, IntakePrimaryClient


class FakeLiterature:
    def __init__(self, fail_at=None):self.calls=[];self.fail_at=fail_at
    def run(self, **arguments):
        self.calls.append(arguments)
        outcome='rate_limited' if len(self.calls)==self.fail_at else 'ok'
        raw=json.dumps({'query':arguments['query'],'outcome':outcome}).encode()
        return {'outcome':outcome,'works':[],'metadata':{'attempts':1},'capture_sha256':sha(raw),
                'capture':{'body':base64.b64encode(raw).decode(),'sha256':sha(raw)}}


class LiteratureCollectionTests(unittest.TestCase):
    def action(self):
        return {'operation':'collect_literature','arguments':{'requests':[
            {'candidate_id':c,'query':'theory '+c,'limit':5} for c in ['A','B','C']]}}

    def test_success_retains_each_exact_capture_and_never_repeats(self):
        with tempfile.TemporaryDirectory() as directory:
            client=FakeLiterature()
            result=collect_literature(self.action(),candidate_ids=['A','B','C'],root=directory,
                deadline=time.monotonic()+2,client=client)
            self.assertEqual(result['outcome'],'ok');self.assertEqual(len(client.calls),3)
            self.assertEqual(result['not_attempted'],[])
            for row in result['rows']:
                self.assertEqual(sha((Path(directory)/'captures'/row['result']['capture_sha256']).read_bytes()),row['result']['capture_sha256'])
            self.assertEqual(sha(Path(result['receipt']).read_bytes()),result['receipt_sha256'])
            with self.assertRaises(StateError):collect_literature(self.action(),candidate_ids=['A','B','C'],root=directory,deadline=time.monotonic()+2,client=client)
            self.assertEqual(len(client.calls),3)

    def test_first_provider_failure_stops_later_candidates_and_retains_partial_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            client=FakeLiterature(fail_at=2)
            result=collect_literature(self.action(),candidate_ids=['A','B','C'],root=directory,deadline=time.monotonic()+2,client=client)
            self.assertEqual(result['outcome'],'failed');self.assertEqual(result['failed_candidate'],'B')
            self.assertEqual(len(client.calls),2);self.assertEqual(result['not_attempted'],['C'])
            self.assertEqual(len(list((Path(directory)/'captures').iterdir())),2)

    def test_all_request_owners_and_queries_are_checked_before_any_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            client=FakeLiterature();action=self.action();action['arguments']['requests'][2]['candidate_id']='other'
            with self.assertRaises(ValidationError):collect_literature(action,candidate_ids=['A','B','C'],root=directory,deadline=time.monotonic()+2,client=client)
            self.assertEqual(client.calls,[])
            action=self.action();action['arguments']['requests'][2]['query']='invalid*'
            with self.assertRaises((ValidationError,ValueError)):collect_literature(action,candidate_ids=['A','B','C'],root=directory,deadline=time.monotonic()+2,client=client)
            self.assertEqual(client.calls,[])

    def test_live_adapter_is_constructed_with_zero_retries_and_no_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            fake=FakeLiterature()
            with patch('scisaurus.runtime.literature_collection.OpenAlexClient',autospec=True) as constructor:
                constructor.return_value=fake
                # autospec is a factory mock rather than a runtime type.
                with patch('scisaurus.runtime.literature_collection.isinstance',side_effect=lambda value,kind: False if kind is constructor else isinstance(value,kind),create=True):
                    collect_literature(self.action(),candidate_ids=['A','B','C'],root=directory,deadline=time.monotonic()+2)
            self.assertEqual(constructor.call_args.kwargs['max_retries'],0)
            self.assertFalse(constructor.call_args.kwargs['allow_anonymous_fallback'])

    def test_initialization_failure_is_sealed_without_any_provider_request(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('scisaurus.runtime.literature_collection.OpenAlexClient',side_effect=ValueError('invalid shared state')):
                with self.assertRaises(ValueError):collect_literature(self.action(),candidate_ids=['A','B','C'],root=directory,deadline=time.monotonic()+2)
            record=json.loads((Path(directory)/'collection.json').read_bytes())
            self.assertEqual(record['outcome'],'failed')
            self.assertEqual(record['rows'],[])
            self.assertEqual(record['not_attempted'],['A','B','C'])
            self.assertEqual(len(list(Path(directory).glob('*.json'))),2)

    def test_primary_task_limits_the_step_to_current_candidates_and_metadata_acquisition(self):
        task=object.__new__(IntakePrimaryClient).task({'literature_acquisition':{'candidate_ids':['A','B','C'],'rule':'one targeted OpenAlex query per candidate. Do not make a new topic selection. The controller executes the batch once.'}})
        self.assertIn('one targeted OpenAlex query per candidate',task)
        self.assertIn('Do not make a new topic selection',task)
        self.assertIn('controller executes the batch once',task)
