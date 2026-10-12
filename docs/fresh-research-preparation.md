# Fresh research intake

`scripts/prepare-research-mission.py` seals a dormant intake bundle in a new
directory. It reserves a new local DSH development identity with empty history,
creates fresh input and topic-history files, and records a content-addressed
preparation checkpoint. Preparation performs no provider, solver or literature
requests. The project run control is stopped and contains no execution grant.

The specification declares only a broad objective, scope, excluded topics and
mission time policy. Exclusions contain identity, title, question and domain;
previous hypotheses, coefficients, observations, verdicts and literature are not
positive research inputs. The primary must compare mechanisms and functions,
not merely avoid duplicate topic IDs. Historical topic IDs can repeat across
question revisions; consumers must retain and compare the complete exclusion
records rather than collapse them by ID. A novelty claim requires a closest-work
comparison and coverage limits. A usefulness claim requires an application,
operating constraints, estimand and matched conventional baseline.

```sh
python3 scripts/prepare-research-mission.py \
  --root /absolute/path/to/new-mission \
  --spec /absolute/path/to/intake-spec.json \
  --backend /absolute/path/to/dsh-batch.json \
  --laboratory /absolute/path/to/laboratory.json \
  --attestation /absolute/path/to/laboratory-attestation.json
```

The specification has exactly `objective`, `scope`, `excluded_topics` and
`time_policy`. Each exclusion has `id`, `title`, `research_question` and `domain`.
Preparation refuses an existing target directory. It validates configuration
identities, but does not recompute installed-runtime fingerprints or establish
provider capacity. A reserved session identity has not yet been loaded by DSH.

This bundle is not a migrated Composer workflow or an execution-ready research
checkpoint. The existing mission migration API requires an admitted question;
inventing one or copying an old completed stage would defeat fresh intake. After
external capacity confirmation, an intake tool-exchange controller must bind the
sealed assignment to the reserved development session. That activation remains
unperformed and must recheck the effective sandbox read grants so shared runtime
access cannot expose prior research projects. The primary's `topic_selection`
output is a proposal or abstention,
not scientific admission. Subsequent research must retain that same owner and
require a model-owned frozen pilot, a small installed-solver execution, independent
same-result validation and replay, and a fresh final scientific audit. No timer,
process-policy change or prepared file constitutes recovery authorization.

## One explicit intake step

`scripts/run-prepared-research-turn.py --root <mission> --runtime-python <python>
--preflight` verifies the sealed inputs, unused session and effective backend read
grants without a provider request. Prior sibling mission directories are excluded
from the new primary's read grants.

Actual dispatch additionally requires `--recovery <json>`, containing an externally
observed `source` and a past timezone-aware `observed_at`. The controller binds a
single explicit attempt to the prepared checkpoint and reserved session. It makes
one DSH dispatch, accepts one response, executes at most one declared source/runtime
assessment operation and stops. Installation and scientific computation are absent
from that first step's active tool contract. A proposal remains unreviewed; format,
tool, provider and custody failures stop without another author or repair request.
One DSH dispatch can contain several sequential provider calls; its actual receipt
and usage are retained rather than equating a dispatch with one model call.

Activation seals a dedicated composition with the same provider/model and a native
tool-failure policy; the shared composition stays unchanged. The policy cancels
the owning DSH agent synchronously on tool errors or structured foreground command
failures, including nonzero exits, timeouts, signals and sandbox denial. The runner
waits for the policy's registration marker before sending its first prompt. This
marker proves listener registration; observing an actual error cancellation is a
separate runtime check. The unused session is rebound under its owner lock to the
new pinned composition, preserving its identity and original empty-state snapshot.
An optional `--runtime-env-file` loads existing owner-local credentials only after
external recovery evidence is supplied, without copying secrets into task files.

The attempt marker blocks redispatch even after an error. Any interruption or paid
unknown remains a recovery obligation, not permission to repeat the step. Final
cleanup restores the durable stop and revokes launch permission. No actual intake
step or recovery evidence is supplied by preparation or preflight.
