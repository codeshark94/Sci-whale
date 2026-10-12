"""Function-first invention requests; hypotheses remain separate from scientific admission."""
import base64
import json
from pathlib import Path

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.runtime.fresh_intake import sha

INVENTION_RULES = [
    'Generate unmet functions before selecting material, geometry, solver or literature keywords. Define who needs the function, operating conditions and what the strongest conventional solution cannot achieve.',
    'Explore causally different mechanisms, including transferable principles outside the nearest application field. Explain the transfer physically; an analogy or familiar keyword combination is insufficient.',
    'For every direction connect intervention, internal physical state and observable response. State a falsifiable prediction with units, a matched baseline, a null and a decisive counterexample.',
    'Use retained literature as unreviewed counterevidence, with its capture identifiers and scope. A missing search hit is not novelty evidence. Do not claim existing papers were fully read.',
    'The held candidates remain preserved and on hold. Do not revive them through a title, material, load-source, solver, optimizer or parameter change. Explain functional and causal separation from each held candidate.',
    'Check conservation laws, symmetry, dimensional consistency, boundary conditions and passive limits. Distinguish an unmet function from an impossible premise. Identify the strongest known explanation that could account for the predicted effect.',
    'Consider solver and fabrication feasibility only after function, mechanism and prediction. Installed software constrains later execution and does not rank the initial ideas.',
    'Return invention hypotheses or abstain. Do not select an admitted topic, declare novelty established, run a solver, retrieve more literature, delegate authorship or repair an error. Stop after one response.'
]


def invention_contract():
    return {'invention':{'decision':'propose | abstain','directions':[{
        'id':'new identity','unmet_function':'specific missing capability and beneficiary',
        'operating_conditions':'conditions under which that capability matters',
        'strongest_existing_solution':'matched conventional approach and its exact limitation',
        'mechanism':'model-owned physical mechanism','causal_chain':['intervention','internal state','observable effect'],
        'prediction':{'observable':'measurable quantity','units':'units','intervention':'controlled change',
            'baseline':'matched comparison','expected_difference':'distinguishing prediction','null':'null explanation',
            'falsifier':'observation that defeats the mechanism'},
        'closest_prior_work':'retained evidence references or explicitly unknown; no invented citations',
        'novelty_hypothesis':'what may differ; novelty unverified',
        'why_not_known_combination':'why a known-effect composition is insufficient',
        'physical_consistency':'laws, units, symmetry, boundary conditions and applicability',
        'strongest_counterexample':'known explanation or limit that could defeat the idea',
        'held_candidate_separation':{'each held candidate id':'functional and causal separation'},
        'decisive_experiment':{'intervention':'minimum discriminating change','control':'matched control',
            'measurement':'observable','failure_criterion':'stop criterion'},
        'later_feasibility':'constraints considered after the invention hypothesis',
        'novelty_status':'unverified'}], 'limitations':['unresolved evidence and theory']}}


def verify_observation(observation, project, *, candidate_ids=None):
    if not isinstance(observation,dict) or observation.get('outcome')!='ok':
        raise StateError('invention requires its owned completed literature observation')
    path=Path(observation['receipt']).resolve();project=Path(project).resolve()
    raw=path.read_bytes()
    if (not path.is_relative_to(project/'intake-controller')
            or sha(raw)!=observation['receipt_sha256'] or path.stem!=sha(raw)
            or json.loads(raw)!={k:v for k,v in observation.items() if k not in {'receipt','receipt_sha256'}}):
        raise StateError('invention observation lost its owned source receipt')
    if (observation.get('schema_version')!='literature-acquisition-1'
            or observation.get('action',{}).get('operation')!='collect_literature'
            or not isinstance(observation.get('rows'),list)):
        raise StateError('invention requires its declared literature acquisition')
    ids=[row.get('candidate_id') for row in observation['rows']]
    requested=[row['candidate_id'] for row in observation['action']['arguments']['requests']]
    if (len(ids)!=len(set(ids)) or ids!=requested or observation.get('not_attempted')
            or candidate_ids is not None and set(ids)!=set(candidate_ids)):
        raise StateError('invention literature belongs to different held candidates')
    for row in observation['rows']:
        result=row['result'];capture=result['capture'];body=base64.b64decode(capture['body'],validate=True)
        if (sha(body)!=capture['sha256'] or sha(body)!=result['capture_sha256']
                or sha((path.parent/'captures'/capture['sha256']).read_bytes())!=sha(body)):
            raise StateError('invention retained source capture changed')



def source_metadata(observation):
    """Expose captured metadata with literal strings split for bounded line readers."""
    sources=[]
    for row in observation['rows']:
        result=row['result']
        sources.append({'candidate_id':row['candidate_id'],'query':row['query'],
            'capture_sha256':result['capture_sha256'],'evidence_scope':observation['evidence_scope'],
            'works':[{key:([value[i:i+1000] for i in range(0,len(value),1000)] if isinstance(value,str) else value)
                for key,value in work.items()} for work in result['works']]})
    return sources

def validate_invention(value, request, exclusions):
    contract=invention_contract()['invention'];template=contract['directions'][0]
    if (not isinstance(value,dict) or set(value)!=set(contract) or value['decision'] not in {'propose','abstain'}
            or not isinstance(value['directions'],list) or not isinstance(value['limitations'],list)
            or any(not isinstance(x,str) for x in value['limitations'])
            or (value['decision']=='propose')!=bool(value['directions'])):
        raise ValidationError('invention response differs from its declared hypothesis contract')
    ids=[];held=set(request['held_candidate_ids'])
    held_candidates=[{k:''.join(v) for k,v in c.items()} for c in request['held_candidate_literal_chunks']]
    forbidden={' '.join(c['question'].casefold().split()) for c in held_candidates}
    forbidden.update(' '.join(c['research_question'].casefold().split()) for c in exclusions)
    for direction in value['directions']:
        if not isinstance(direction,dict) or set(direction)!=set(template):
            raise ValidationError('invention direction lacks function, mechanism or falsifiable prediction')
        for key,prototype in template.items():
            item=direction[key]
            if isinstance(prototype,str) and (not isinstance(item,str) or not item.strip()):
                raise ValidationError('invention direction requires literal '+key)
            if isinstance(prototype,dict):
                keys=held if key=='held_candidate_separation' else set(prototype)
                if (not isinstance(item,dict) or set(item)!=keys
                        or any(not isinstance(v,str) or not v.strip() for v in item.values())):
                    raise ValidationError('invention requires exact '+key+' fields')
        chain=direction['causal_chain']
        if not isinstance(chain,list) or len(chain)<3 or any(not isinstance(x,str) or not x.strip() for x in chain):
            raise ValidationError('invention requires a causal chain')
        if (direction['id'] in held or direction['novelty_status']!='unverified'
                or ' '.join(direction['unmet_function'].casefold().split()) in forbidden):
            raise ValidationError('invention revives a held question or claims scientific admission')
        ids.append(direction['id'])
    if len(set(ids))!=len(ids):raise ValidationError('invention identities must be unique')
