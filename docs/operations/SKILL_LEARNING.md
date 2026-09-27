# Aurora Skill Learning and Adaptive Routing

## Evaluation

After a worker executes a task, every effective specialist skill is evaluated from observable
execution evidence. A successful task without verification markers receives only partial
credit when the skill declares verification or quality checks. Failed checks lower the score;
missing checks are not treated as passes.

Agents can make evidence machine-observable with:
- `VERIFY:<check>=pass|fail`
- `QUALITY:<check>=pass|fail`

## Learning

Evaluations are persisted in the skill learning SQLite ledger. The ledger aggregates:
- skill success and average score
- failure counts
- historical model scores for each skill

The TaskEngine can feed learned model scores back into inference routing before the next
execution. Cold-start models are not treated as superior merely because they lack history.

## Evolution

When a skill has enough observations and its average score remains below the configured
threshold, Aurora creates a pending evolution proposal. The proposal receives a patch version,
evidence count and an evidence-derived change suggestion.

Pending proposals are not active skills. An activation workflow must validate the candidate,
run regression/security checks and explicitly replace the active policy artifact. This prevents
a single bad run or prompt injection from silently rewriting a trusted specialist procedure.

## Dynamic model selection

For model tasks, Aurora combines:
- required inference capability
- skill tags and detected work type
- context-window fit
- configured quality, latency and cost priors
- organizational/skill model preferences
- learned model performance

This is an adaptive score rather than a hard-coded single-model mapping. The inference catalog
must contain multiple enabled compatible models before model choice can vary in practice.
