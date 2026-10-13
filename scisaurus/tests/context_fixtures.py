"""Source-only native context package closure for composition unit tests."""
import hashlib
import json
from pathlib import Path

from scisaurus.runtime.dsh_context import CONTEXT_PACKAGES


def context_backend(root, backend):
    backend.setdefault('max_output_tokens', 32768)
    pins = backend.setdefault('pinned_files', {})
    for identity, name in CONTEXT_PACKAGES.items():
        directory = Path(root) / 'context-packages' / identity
        directory.mkdir(parents=True, exist_ok=True)
        files = {directory / 'package.json': json.dumps({'name': name, 'main': 'index.js'}).encode(),
                 directory / 'index.js': b'export const fixture = true;\n'}
        for path, raw in files.items():
            path.write_bytes(raw)
            pins[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
    return backend
