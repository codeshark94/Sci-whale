"""Receipt-bound native requests executed outside the research agent sandbox."""
import hashlib
import json
import time
import uuid

from scisaurus.core.errors import ValidationError, StateError, NotFoundError, ProviderRateLimitError, ProviderConfigurationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.run_control import ensure_run_allowed, RunPausedError

METHOD = 'scisaurus/research-tool'
TOOL = 'research_tool'
RUN_TOOL = 'research_run'


class NativeToolBridge:
    def __init__(self, job, state, handler, save, deadline):
        self.directory = job / 'native-tool-results'
        self.directory.mkdir()
        self.state, self.handler, self.save = state, handler, save
        self.deadline = deadline
        self.channel = uuid.uuid4().hex
        self.calls = {}
        self.failed = False
        self.manifest = {'directory': str(self.directory), 'channel': self.channel,
            'deadline_ms': int((time.time() + max(0, deadline - time.monotonic())) * 1000)}

    def observe(self, payload):
        params = payload.get('params', {})
        if payload.get('method') != 'session.event' or params.get('sessionId') != self.state['session_id']:
            return
        event = params.get('event', {})
        data = event.get('data', {})
        if event.get('type') == 'tool/call' and data.get('name') in {TOOL,RUN_TOOL}:
            identity = data['callId']
            if identity in self.calls:
                raise ValidationError('native tool call identity was repeated')
            action=json.loads(data['arguments'])
            self.calls[identity] = {'operation':'run','arguments':action} if data['name']==RUN_TOOL else action

    def respond(self, payload):
        if self.failed:
            raise ValidationError('native tool failure already stopped this attempt; no further execution')
        ensure_run_allowed()
        if time.monotonic() >= self.deadline:
            raise ValidationError('native tool request reached the owning attempt deadline')
        params = payload.get('params', {})
        if (set(params) != {'channel', 'sessionId', 'callId', 'action'}
                or params['channel'] != self.channel or params['sessionId'] != self.state['session_id']
                or not isinstance(params['callId'], str)
                or params['callId'] not in self.calls
                or canonical_bytes(self.calls.pop(params['callId'], None)) != canonical_bytes(params['action'])):
            raise ValidationError('native tool request lost its exact session/call owner')
        filename = hashlib.sha256(params['callId'].encode()).hexdigest() + '.json'
        path = self.directory / filename
        if path.exists():
            raise ValidationError('native tool request already consumed; no automatic retry')
        record = {'call_id': params['callId'], 'action': params['action'], 'status': 'requested'}
        self.state.setdefault('native_tool_calls', []).append(record)
        self.save()
        try:
            ensure_run_allowed()
            if time.monotonic() >= self.deadline:
                raise ValidationError('native tool request reached the owning attempt deadline')
            observation = self.handler(params['action'])
            if not isinstance(observation, dict) or 'outcome' not in observation:
                raise ValidationError('native controller tool requires its recorded outcome')
            reply = {'channel': self.channel, 'sessionId': params['sessionId'],
                'callId': params['callId'], 'observation': observation}
            self.failed = observation['outcome'] == 'result_unknown'
            if self.failed:reply['terminal']=True
        except Exception as error:
            self.failed = not isinstance(error,(ValidationError,StateError,NotFoundError,ValueError,TypeError,KeyError)) or isinstance(error,(ProviderRateLimitError,ProviderConfigurationError,RunPausedError)) or hasattr(error,'receipt')
            reply = {'channel': self.channel, 'sessionId': params['sessionId'],
                'callId': params['callId'], 'error': f'{type(error).__name__}: {error}'}
            if hasattr(error,'receipt'):
                reply['dispatch']={'receipt':str(error.receipt),'usage':getattr(error,'usage',{}),
                    'failure':error.failure_details() if callable(getattr(error,'failure_details',None)) else None}
            if self.failed:reply['terminal']=True
        raw = canonical_bytes(reply)
        record.update(status='responded', response_file=filename,
            response_sha256=hashlib.sha256(raw).hexdigest(),
            outcome=reply.get('observation', {}).get('outcome', 'error'))
        # Commit ownership and evidence before making the result visible to the agent.
        self.save()
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(raw)
        temporary.chmod(0o444)
        temporary.replace(path)
