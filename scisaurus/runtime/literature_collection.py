"""One ordered, receipt-bound scholarly acquisition with no retry or research author."""
import base64
import json
from pathlib import Path
import time

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.literature import OpenAlexClient, validate_arguments
from scisaurus.runtime.run_control import ensure_run_allowed


def literature_contract():
    return {'operation':'collect_literature','arguments':{'requests':[
        {'candidate_id':'exact existing candidate id','query':'targeted OpenAlex stemmed search; no wildcards',
         'limit':5}]}}


def collect_literature(action, *, candidate_ids, root, deadline, client=None):
    if (not isinstance(action,dict) or set(action)!={'operation','arguments'}
            or action['operation']!='collect_literature' or not isinstance(action['arguments'],dict)
            or set(action['arguments'])!={'requests'}):
        raise ValidationError('literature acquisition requires its exact batch contract')
    requests=action['arguments']['requests']
    if not isinstance(requests,list) or not requests:
        raise ValidationError('literature batch must cover every existing candidate')
    normalized=[]
    for request in requests:
        if (not isinstance(request,dict) or set(request)!={'candidate_id','query','limit'}
                or not isinstance(request['candidate_id'],str)):
            raise ValidationError('literature query lacks an exact candidate owner')
        normalized.append(validate_arguments({'operation':'search','query':request['query'],
            'work_id':None,'limit':request['limit'],'cursor':None}))
    ids=[r['candidate_id'] for r in requests]
    if len(set(ids))!=len(ids) or set(ids)!=set(candidate_ids):
        raise ValidationError('literature batch must cover each unchanged candidate exactly once')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    marker=root/'collection.json'
    if marker.exists():
        raise StateError('literature acquisition already exists; no automatic redispatch')
    record={'schema_version':'literature-acquisition-1','action':action,'outcome':'started','rows':[],
        'evidence_scope':'scholarly metadata and reconstructed abstracts; no acquired full text or scientific verdict'}
    marker.write_bytes(canonical_bytes(record))
    try:
        if client is None:
            from scisaurus.runtime.composer import default_openalex_shared_state_path
            client=OpenAlexClient(timeout=min(30,max(.01,deadline-time.monotonic())), max_retries=0,
                auth_env='SCISAURUS_OPENALEX_API_KEY', allow_anonymous_fallback=False,
                rate_state_path=default_openalex_shared_state_path())
        for request,arguments in zip(requests,normalized):
            ensure_run_allowed()
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('literature batch reached its attempt deadline')
            if isinstance(client,OpenAlexClient):client.timeout=min(30,remaining)
            result=client.run(**arguments)
            row={'candidate_id':request['candidate_id'],'query':request['query'],'result':result}
            record['rows'].append(row)
            capture=result.get('capture')
            if capture:
                raw=base64.b64decode(capture['body'],validate=True)
                if sha(raw)!=capture['sha256'] or sha(raw)!=result['capture_sha256']:
                    raise ValidationError('literature capture differs from its raw provider body')
                directory=root/'captures';directory.mkdir(exist_ok=True)
                (directory/capture['sha256']).write_bytes(raw)
            marker.write_bytes(canonical_bytes(record))
            if result.get('outcome') not in {'ok','empty'}:
                record.update(outcome='failed',failed_candidate=request['candidate_id'])
                break
        else:record['outcome']='ok'
    except BaseException as error:
        record.update(outcome='failed',error=f'{type(error).__name__}: {error}')
        raise
    finally:
        record['not_attempted']=[r['candidate_id'] for r in requests[len(record['rows']):]]
        body=canonical_bytes(record);sealed=root/(sha(body)+'.json');sealed.write_bytes(body);sealed.chmod(0o444)
        marker.write_bytes(body)
    return {**record,'receipt':str(sealed),'receipt_sha256':sha(body)}
