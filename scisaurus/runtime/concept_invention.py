"""Capability imagination followed by model-owned mechanism and structural design."""
from copy import deepcopy
import json
from pathlib import Path

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.runtime.dsh_batch import task_json_bytes
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.input_delivery import verify_full_input_reads
from scisaurus.runtime.invention_review import chunk_literals

CONCEPT_RULES = [
    'Begin by imagining useful physical capabilities that matter does not ordinarily provide. Describe the new behavior and its beneficiary before choosing a familiar effect, material, geometry, solver or literature keyword.',
    'Explore qualitatively different capabilities boldly. Give the primary responsibility for the ideas. No particular device, mechanism, material, coefficient, numerical target or successful outcome is prescribed. A performance increment or new application label alone is insufficient.',
    'Only after capability imagination develop selected capabilities into concrete concepts. Explain an intervention, internal state change and functional output, then specify components, spatial arrangement, interfaces, state sequence and a readable text schematic. Naming an effect or making an analogy is not structural design.',
    'Check energy or resource supply, conservation, dimensional consistency, causality and the hardest physical gap after the concept has taken shape. Keep unsupported links explicit. Boldness does not permit impossible passive gain or invented observations.',
    'Define the smallest future model or prototype that could show possibility: matched conventional baseline, controlled intervention, measurable output with units, distinguishing signature, acceptable null, falsifier and stopping condition. This step designs the test and does not execute it.',
    'Compare the developed concept with the closest known principle and the remaining prior-art uncertainty. Unknown novelty does not block a concept-only proposal. Do not certify world-first novelty, claim full-text reading or interpret absent search hits as proof.',
    'The held catalog contains negative constraints, not starting designs. Preserve every versioned candidate and explain functional and causal separation from each. Do not revive one through a name, material, parameter, load source, optimizer or solver change. Retain the original mission exclusions.',
    'Consider installed-runtime feasibility after capability, mechanism and structure. The deliverable demonstrates an intelligible prospect, not scientific admission, an executed result or a finished paper. Abstain if no defensible concept can be formed.',
    'Read every current input line using the native read tool, continuing from the exact next unread line after truncation. Return one concept_design response. No source retrieval, solver, installation, alternate author, tool retry or repair dispatch is authorized. Stop on error; no automatic redispatch.'
]


def concept_contract():
    return {'concept_design': {'decision': 'propose | abstain',
        'imagined_capabilities': [{'id': 'capability identity', 'new_ability': 'specific unfamiliar useful behavior',
            'beneficiary': 'who needs this capability', 'operating_scenario': 'when and where it matters',
            'why_useful': 'concrete benefit'}],
        'concepts': [{'id': 'new concept identity', 'capability_id': 'one imagined capability identity',
            'working_principle': {'trigger': 'intervention', 'internal_state_change': 'physical state transition',
                'functional_output': 'observable new behavior', 'causal_chain': ['intervention', 'internal state', 'output']},
            'structure': {'components': [{'name': 'component', 'material_role': 'required physical property',
                'geometry_role': 'shape and physical role'}], 'arrangement': 'spatial organization',
                'interfaces': 'coupling between components', 'state_sequence': ['initial state', 'operating state'],
                'text_schematic': 'readable structural sketch'},
            'physics_sanity': {'energy_or_resource_source': 'physical supply', 'balance_or_limit': 'conservation or bound',
                'dimensional_check': 'units of the causal relation', 'causality_check': 'ordering and relevant timescales',
                'hardest_physical_gap': 'unproven physical link'},
            'possibility_test': {'minimal_model_or_prototype': 'smallest future test', 'matched_baseline': 'realizable conventional control',
                'intervention': 'controlled change', 'observable': 'measured quantity', 'units': 'quantity units',
                'expected_signature': 'prediction distinguishing the proposed capability', 'null': 'acceptable null outcome',
                'falsifier': 'observation that defeats the mechanism', 'stop_condition': 'condition to stop'},
            'novelty_hypothesis': 'possible functional or causal distinction', 'closest_known_principle': 'known explanation or explicitly unknown',
            'unresolved_prior_art': 'specific remaining comparison',
            'held_candidate_separation': {'each versioned held identity': 'functional and causal difference'},
            'novelty_status': 'unverified', 'evidence_status': 'concept_only', 'limitations': ['remaining limitations']}],
        'limitations': ['overall limits of this concept-only response']}}


def held_catalog(state):
    """Retain versioned authored functions and mechanisms as negative constraints."""
    catalog = []
    sources = []
    for call in state['calls']:
        receipt_path = Path(call['receipt']).resolve()
        receipt = json.loads(receipt_path.read_bytes())
        if receipt.get('status') != 'completed':
            continue
        path = receipt_path.parent / 'outputs/response.json'
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if receipt.get('outputs', {}).get('response.json') != sha(raw):
            raise StateError('held concept catalog lost its owned response body')
        response = json.loads(raw)
        if set(response) == {'topic_selection'}:
            rows = [{'id': row['id'], 'function': row['question'], 'mechanism': row['mechanism']}
                    for row in response['topic_selection']['candidates']]
        elif set(response) == {'invention'}:
            rows = [{'id': row['id'], 'function': row['unmet_function'], 'mechanism': row['mechanism']}
                    for row in response['invention']['directions']]
        elif set(response) == {'concept_design'}:
            capabilities = {row['id']: row['new_ability'] for row in response['concept_design']['imagined_capabilities']}
            rows = [{'id': row['id'], 'function': capabilities[row['capability_id']],
                     'mechanism': json.dumps(row['working_principle'], ensure_ascii=False, sort_keys=True)}
                    for row in response['concept_design']['concepts']]
        else:
            continue
        sources.append({'receipt': str(receipt_path), 'receipt_sha256': call['sha256'],
                        'response': str(path), 'response_sha256': sha(raw)})
        catalog.extend({'identity': sha(raw) + ':' + row['id'], 'source_sha256': sha(raw), **row} for row in rows)
    if not catalog:
        raise StateError('concept imagination requires its preserved held catalog')
    return catalog, sources


def concept_request(context):
    c = context
    expected = {'stopped_with_unadmitted_self_review': 'invention_review',
                'stopped_with_unreviewed_concepts': 'concept_design'}
    key = expected.get(c['record']['status'])
    if key is None or set(c['response']) != {key}:
        raise StateError('concept imagination requires its exact completed predecessor')
    coverage = verify_full_input_reads(c['receipt_path'],
        first_input='unread-input.txt' if key == 'invention_review' else None)
    if c['record'].get('input_read_coverage') != coverage:
        raise StateError('concept predecessor lost its recorded full input delivery')
    catalog, sources = held_catalog(c['state'])
    request = {'kind': 'concept_invention', 'prior_receipt_sha256': sha(c['receipt_path'].read_bytes()),
        'prior_input_read_coverage': coverage, 'held_catalog': catalog, 'held_sources': sources,
        'held_candidate_ids': list(dict.fromkeys(row['id'] for row in catalog)),
        'held_candidate_keys': [row['identity'] for row in catalog],
        'rules': CONCEPT_RULES, 'output_contract': concept_contract()}
    request['delivered_input_sha256'] = {name: sha(raw) for name, raw in concept_files(request).items()}
    return request


def concept_files(request):
    files = {'held-candidates.json': task_json_bytes(chunk_literals(request['held_catalog']))}
    if ('delivered_input_sha256' in request
            and {name: sha(raw) for name, raw in files.items()} != request['delivered_input_sha256']):
        raise StateError('concept held catalog projection changed')
    return files


def delivered_request(request):
    return {key: deepcopy(value) for key, value in request.items() if key not in {'held_sources', 'held_catalog'}}


def validate_concepts(value, request, exclusions):
    contract = concept_contract()['concept_design']
    if (not isinstance(value, dict) or set(value) != set(contract)
            or value['decision'] not in {'propose', 'abstain'}
            or not isinstance(value['imagined_capabilities'], list) or not isinstance(value['concepts'], list)
            or (value['decision'] == 'propose') != bool(value['concepts'])):
        raise ValidationError('concept design differs from its capability-first contract')
    def text_fields(item, prototype):
        if not isinstance(item, dict) or set(item) != set(prototype):
            raise ValidationError('concept design lost required structural fields')
        for name, example in prototype.items():
            if isinstance(example, str) and (not isinstance(item[name], str) or not item[name].strip()):
                raise ValidationError('concept design requires literal ' + name)
    def string_list(items, minimum=0):
        if not isinstance(items, list) or len(items) < minimum or not all(isinstance(x, str) and x.strip() for x in items):
            raise ValidationError('concept design requires nonempty literal list entries')
    capabilities = {}
    forbidden = {' '.join(row['function'].casefold().split()) for row in request['held_catalog']}
    forbidden.update(' '.join(row['research_question'].casefold().split()) for row in exclusions)
    for capability in value['imagined_capabilities']:
        text_fields(capability, contract['imagined_capabilities'][0])
        if (capability['id'] in capabilities or capability['id'] in request['held_candidate_ids']
                or ' '.join(capability['new_ability'].casefold().split()) in forbidden):
            raise ValidationError('imagined capability repeats a held identity or function')
        capabilities[capability['id']] = capability
    ids = []
    prototype = contract['concepts'][0]
    for concept in value['concepts']:
        text_fields(concept, prototype)
        if (concept['capability_id'] not in capabilities or concept['id'] in request['held_candidate_ids']
                or concept['novelty_status'] != 'unverified' or concept['evidence_status'] != 'concept_only'):
            raise ValidationError('concept design changed its ownership or claimed admission')
        for field in ['working_principle', 'structure', 'physics_sanity', 'possibility_test']:
            text_fields(concept[field], prototype[field])
        string_list(concept['working_principle']['causal_chain'], 3)
        string_list(concept['structure']['state_sequence'], 2)
        components = concept['structure']['components']
        if not isinstance(components, list) or not components:
            raise ValidationError('concept structure requires actual components')
        for component in components:
            text_fields(component, prototype['structure']['components'][0])
        separation = concept['held_candidate_separation']
        if (not isinstance(separation, dict) or set(separation) != set(request['held_candidate_keys'])
                or not all(isinstance(x, str) and x.strip() for x in separation.values())):
            raise ValidationError('concept must compare every versioned held function and mechanism')
        string_list(concept['limitations'])
        ids.append(concept['id'])
    if len(set(ids)) != len(ids):
        raise ValidationError('concept identities must be unique')
    string_list(value['limitations'])
