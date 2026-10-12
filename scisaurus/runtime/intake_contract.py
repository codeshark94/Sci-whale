"""Authoritative response checks shared by native publication and final acceptance."""
import json
import hashlib
import math
from pathlib import Path

from scisaurus.core.errors import ValidationError


def strict_document(body):
    def finite_float(value):
        number=float(value)
        if not math.isfinite(number):raise ValidationError('nonfinite JSON number: '+value)
        return number
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValidationError('duplicate JSON field: ' + key)
            value[key] = item
        return value
    return json.loads(body, object_pairs_hook=object_pairs,
                      parse_float=finite_float,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValidationError('nonfinite JSON value: ' + value)))


def validate_response(assignment, response):
    if not isinstance(response, dict):
        raise ValidationError('response requires a JSON object')
    if assignment.get('research_work'):
        from scisaurus.runtime.primary_work import validate_work_response
        validate_work_response(assignment,response)
    elif assignment.get('concept_invention'):
        from scisaurus.runtime.concept_invention import validate_concepts
        if set(response) != {'concept_design'}:
            raise ValidationError('response requires exactly concept_design')
        validate_concepts(response['concept_design'], assignment['concept_invention'],
                          assignment['excluded_topics'])
        if assignment.get('structured_continuation'):
            from scisaurus.runtime.structured_recovery import validate_continuation
            validate_continuation(response, assignment['structured_continuation'])
    elif assignment.get('invention_review'):
        from scisaurus.runtime.invention_review import validate_review
        if set(response) != {'invention_review'}:
            raise ValidationError('response requires exactly invention_review')
        validate_review(response['invention_review'], assignment['invention_review'])
    elif assignment.get('invention_process'):
        from scisaurus.runtime.invention import validate_invention
        if set(response) != {'invention'}:
            raise ValidationError('response requires exactly invention')
        validate_invention(response['invention'], assignment['invention_process'],
                           assignment['excluded_topics'])
    elif set(response) == {'topic_selection'} and not assignment.get('literature_acquisition'):
        from scisaurus.runtime.fresh_intake import validate_proposal
        validate_proposal(response['topic_selection'], assignment)
    elif set(response) == {'tool_action'}:
        action = response['tool_action']
        if not isinstance(action, dict) or action.get('operation') not in assignment['scientific_software_tools']['actions']:
            raise ValidationError('response requests an undeclared controller operation')
        if assignment.get('literature_acquisition'):
            from scisaurus.runtime.literature_collection import validate_literature_action
            validate_literature_action(action,assignment['literature_acquisition']['candidate_ids'])
        else:
            from scisaurus.runtime.software_workbench import validate_readonly_action
            validate_readonly_action(action)
    else:
        raise ValidationError('response differs from its active intake contract')


def delivered_assignment(path):
    """Restore only hash-owned literal projections needed by the same validator."""
    assignment=strict_document(Path(path).read_bytes())
    request=assignment.get('concept_invention')
    if request and 'held_catalog' not in request:
        raw=Path(path).with_name('held-candidates.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=request['delivered_input_sha256']['held-candidates.json']:
            raise ValidationError('held catalog projection changed')
        def restore(value):
            if isinstance(value, dict):
                if set(value)=={'literal_chunks'}:
                    return ''.join(value['literal_chunks'])
                return {key:restore(item) for key,item in value.items()}
            if isinstance(value,list):return [restore(item) for item in value]
            return value
        request['held_catalog']=restore(strict_document(raw))
        if [row['identity'] for row in request['held_catalog']]!=request['held_candidate_keys']:
            raise ValidationError('held catalog changed its versioned identities')
    return assignment
