"""Lossless archive references for a persistent researcher's active context."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from scisaurus.core.errors import StateError
from scisaurus.core.schema import canonical_bytes


def _has_research_context(assignment):
    if not isinstance(assignment,dict):return False
    work=assignment.get('research_work')
    if not isinstance(work,dict):return False
    checkpoint=work.get('checkpoint',{})
    return (isinstance(checkpoint,dict) and
            isinstance(checkpoint.get('review_dispatches',[]),list) and
            all(isinstance(x,dict) for x in checkpoint.get('review_dispatches',[])))


def research_context_projection(assignment):
    """Keep research evidence inline and transport custody in its sealed ledger."""
    result=deepcopy(assignment)
    if not _has_research_context(result):return result
    checkpoint=result.get('research_work',{}).get('checkpoint',{})
    for dispatch in checkpoint.get('review_dispatches',[]):
        if 'backend' in dispatch:
            dispatch['backend_sha256']=hashlib.sha256(canonical_bytes(dispatch.pop('backend'))).hexdigest()
    return result


def input_identity(name,raw):
    """Recognize exact scientific JSON through its prior transport projection."""
    if name=='assignment.json':
        try:
            assignment=json.loads(raw)
            if _has_research_context(assignment):
                raw=canonical_bytes(research_context_projection(assignment))
        except (ValueError,TypeError):
            # Opaque legacy input is identified by its original bytes.
            pass
    return hashlib.sha256(raw).hexdigest()


def input_archive(directory,hashes,*,receipt_sha256=None):
    inputs={}
    for name,expected in hashes.items():
        path=(Path(directory)/name).resolve()
        if not path.is_relative_to(Path(directory).resolve()):
            raise StateError('retained input escapes its receipt')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:
            raise StateError('retained input changed')
        inputs[name]={'path':str(path),'sha256':expected,'context_sha256':input_identity(name,raw)}
    return {'inputs':inputs,**({'receipt_sha256':receipt_sha256} if receipt_sha256 else {})}


def project_inline_research_inputs(text,*,retained_inputs=()):
    """Reference verified historical packets while keeping current inputs inline."""
    def matches(identities,packet):
        return (set(identities)==set(packet['inputs']) and
                all(identity==packet['inputs'][name]['context_sha256'] for name,identity in identities.items()))
    def project(match):
        try:
            files=json.loads(match.group(1))
            if not isinstance(files,dict) or any(not isinstance(x,str) for x in files.values()):
                return match.group(0)
            identities={name:input_identity(name,raw.encode()) for name,raw in files.items()}
            packet=next((p for p in reversed(retained_inputs) if matches(identities,p)),None)
            assignment=json.loads(files['assignment.json'])
        except (ValueError,KeyError,TypeError):
            return match.group(0)
        if packet is not None:
            for item in packet['inputs'].values():
                if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
                    raise StateError('retained input changed during context restoration')
            references={name:{key:item[key] for key in ('path','sha256')}
                        for name,item in packet['inputs'].items()}
            return '<retained-inputs>\n'+json.dumps({
                'receipt_sha256':packet['receipt_sha256'],'inputs':references,
                'retrieval':'Use read_retained_input with path and sha256; continue at end to retrieve complete text.'},
                ensure_ascii=False,sort_keys=True)+'\n</retained-inputs>'
        if not isinstance(assignment,dict) or not isinstance(assignment.get('research_work'),dict):
            return match.group(0)
        projected=research_context_projection(assignment)
        if projected==assignment:return match.group(0)
        from scisaurus.runtime.inline_inputs import inline_inputs
        files['assignment.json']=json.dumps(projected,ensure_ascii=False)
        return inline_inputs(files).strip('\n')
    return re.sub(r'<current-inputs>\n(.*?)\n</current-inputs>',project,text,flags=re.DOTALL)


def project_archived_input_read(text,*,retained_inputs=()):
    """Reference only read-tool text from an owned immutable historical input."""
    tag=re.search(r'<path>(.*?)</path>',text)
    if tag is None:return text
    path=str(Path(tag.group(1)).resolve())
    for packet in retained_inputs:
        for item in packet['inputs'].values():
            if path!=item['path']:continue
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=item['sha256']:
                raise StateError('retained input read changed during context restoration')
            return '<retained-input>\n'+json.dumps({'receipt_sha256':packet['receipt_sha256'],
                'path':path,'sha256':item['sha256'],
                'retrieval':'Use read_retained_input with path and sha256; continue at end to retrieve complete text.'},
                ensure_ascii=False,sort_keys=True)+'\n</retained-input>'
    return text
