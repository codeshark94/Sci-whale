"""Pinned native DSH context services for prepared and restored deployments."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from scisaurus.core.errors import StateError

CONTEXT_PACKAGES = {
    'token-meter': '@deepseek-ai/dsh-token-meter',
    'tool-result-pruner': '@deepseek-ai/dsh-compaction-tool-result-pruner',
    'compaction-basic': '@deepseek-ai/dsh-compaction-basic',
}


def context_entries(max_output_tokens):
    """Use SDK retention defaults and one summary attempt on the current route."""
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise StateError('context policy requires a positive generation reservation')
    return [
        {'id': identity, 'name': package, **({'config': {
            'auto': False, 'maxTokens': max_output_tokens,
            'compactionRetries': 0, 'maxOverflowRetries': 0,
        }} if identity == 'compaction-basic' else {})}
        for identity, package in CONTEXT_PACKAGES.items()
    ]


def pinned_package_entries(backend, packages):
    """Resolve built entries only from the deployment's verified package closure."""
    found = {name: {} for name in packages}
    pins = backend['pinned_files']
    for filename, expected in pins.items():
        path = Path(filename)
        if path.name != 'package.json':
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise StateError('DSH package manifest changed: ' + filename)
        manifest = json.loads(raw)
        name, main = manifest.get('name'), manifest.get('main')
        if name not in found or not isinstance(main, str):
            continue
        entry = (path.parent / main).resolve()
        digest = pins.get(str(entry))
        if digest is None:
            continue
        if hashlib.sha256(entry.read_bytes()).hexdigest() != digest:
            raise StateError('DSH package entry changed: ' + str(entry))
        found[name][str(entry)] = digest
    result = {}
    for name, entries in found.items():
        if len(entries) != 1:
            raise StateError('DSH context package is missing or ambiguous: ' + name)
        result[name] = min(entries)
    return result


def context_composition(backend, composition):
    """Upgrade missing context services without altering model or session providers."""
    entries = context_entries(backend['max_output_tokens'])
    paths = pinned_package_entries(backend, CONTEXT_PACKAGES.values())
    names = set(CONTEXT_PACKAGES.values()) | set(paths.values())
    result = [deepcopy(row) for row in composition
              if row.get('id') not in {*CONTEXT_PACKAGES, 'context-policy'}
              and row.get('name') not in names]
    for row in entries:
        row['name'] = paths[row['name']]
    return result + entries
