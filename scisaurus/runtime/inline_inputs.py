"""Exact frozen input delivery in the owning primary message."""
import json


def inline_inputs(files):
    return '\n<current-inputs>\n'+json.dumps({name:raw.decode('utf-8') if isinstance(raw,bytes) else raw
        for name,raw in sorted(files.items())},ensure_ascii=False,sort_keys=True)+'\n</current-inputs>\n'
