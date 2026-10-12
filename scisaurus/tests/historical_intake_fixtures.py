"""Construct immutable legacy checkpoints for recovery tests, without author dispatch."""
from copy import deepcopy
import json
from pathlib import Path
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.dsh_batch import DshStructuredProducerClient
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.input_delivery import verify_full_input_reads
from scisaurus.runtime.intake_contract import validate_response


def input_files(assignment, system):
    from scisaurus.runtime.structured_response import OUTPUT_RULES
    value = deepcopy(assignment)
    value['structured_output_policy'] = {'representation':'parsed_json_object',
        'publication':'complete_document_write','initial_response_state':'absent',
        'preserved_document_input':'draft-response.json' if value.get('structured_continuation') else None,
        'rules':OUTPUT_RULES}
    extra = {}
    if value.get('structured_continuation'):
        from scisaurus.runtime.structured_recovery import continuation_files, delivered_continuation
        extra.update(continuation_files(value['structured_continuation']))
        value['structured_continuation'] = delivered_continuation(value['structured_continuation'])
    for key, module in [('concept_invention','concept_invention'),('invention_review','invention_review')]:
        if value.get(key):
            import importlib
            projection = importlib.import_module('scisaurus.runtime.' + module)
            request = value[key]
            extra.update(getattr(projection,'concept_files' if key=='concept_invention' else 'review_files')(request))
            value[key] = projection.delivered_request(request)
    return {**DshStructuredProducerClient.input_files(None,value,system),**extra}


def checkpoint(root, ticket, receipt, response, assignment, status):
    control=Path(root)/'composer/output/run-control.json'
    record=json.loads(Path(json.loads(control.read_bytes())['attempt']).read_bytes())
    record.update({'status':status,'receipt':str(receipt),'response':response,
        'scientific_admission':'not_assessed','usage':json.loads(Path(receipt).read_bytes())['usage']})
    try:
        record['input_read_coverage'] = verify_full_input_reads(receipt,
            first_input='unread-input.txt' if 'unread-input.txt' in json.loads(Path(receipt).read_bytes())['input_sha256'] else None)
        validate_response(assignment,response)
    except Exception:
        record['status']='stopped_on_error'
        raise
    finally:
        marker=Path(ticket).with_suffix('.attempt.json');raw=canonical_bytes(record);marker.write_bytes(raw)
        control=Path(root)/'composer/output/run-control.json';value=json.loads(control.read_bytes())
        value.update(stop_requested=True,research_resume_authorization_ref=None,attempt=str(marker))
        control.write_bytes(canonical_bytes(value))
        (Path(root)/'composer/intake-controller'/(sha(raw)+'.json')).write_bytes(raw)
    return record
