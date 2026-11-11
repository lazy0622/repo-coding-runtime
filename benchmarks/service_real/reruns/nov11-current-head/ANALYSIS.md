# Current-HEAD regression after withdrawing forced completion

Run date: 2026-11-11 (Asia/Shanghai). Service/source HEAD at submission and
collection: `0ecb78ea59d3edc2c83435a1913daa748431e707`.

This is one fresh run of the same five known historical tasks, not a held-out
evaluation. The label `nov11-current-head` did not exist before submission.
Cases, source snapshots and independent verifier were not changed. Frozen
manifest SHA-256:
`5b33e316a8379fd0708b0a0891e572dbb01ff1b532dcc4c9bafc4af12ee7c575`.

Settings: `deepseek-flash`, thinking disabled, 12 tool steps per attempt,
240-second attempt timeout, 60-second independent-verification timeout, two
existing local workers, Host execution. Public development checks remained
configured; the service did **not** pass `--stop-after-verification`.

## Results

| Case | Independent acceptance | Runtime stop | Tool steps | Model calls | Attempt seconds |
| --- | --- | --- | ---: | ---: | ---: |
| humanize-intcomma-large-int | pass | final_answer_returned | 3 | 5 | 12.72 |
| humanize-fractional-whole | fail | final_answer_returned | 3 | 5 | 14.97 |
| humanize-naturalsize-rollover | pass | final_answer_returned | 3 | 5 | 13.43 |
| itsdangerous-future-timestamp | pass | final_answer_returned | 4 | 6 | 15.13 |
| itsdangerous-none-salt | pass | final_answer_returned | 7 | 9 | 49.19 |

**Independent acceptance: 4/5. Agent natural final answer: 5/5. Configured
verified stops: 0/5. Step-limit stops: 0/5.** All attempts used generation 1;
no failed case was retried or replaced. The failed patch is retained alongside
the accepted patches. All five exported patch digests matched their sealed
service hashes.

Observed usage summed from the existing collector: 35,788 input tokens and
2,880 output tokens, across 30 model calls. Provider-reported usage is not a
currency bill, and this single run does not establish a causal speed or cost
improvement. Queue time is excluded from attempt durations.

## The failure remains a real incomplete repair

All five public development verifiers completed successfully (respectively
232, 227, 70, 112 and 17 existing regression tests). The independent verifier
rejected `humanize-fractional-whole` at the `0.9999999` behavior assertion.

A read-only diagnostic of the retained candidate also returned:

| Input | Candidate output | Required behavior |
| --- | --- | --- |
| 0 | `0` | `0` |
| 0.9999999 | `1/1` | `1` |
| 2.9999999 | `2 1/1` | `3` |
| -2.9999999 | `-2 1/1` | `-3` |

The latter two examples were already in the task description. The patch
changes the zero-numerator branch but does not handle a fractional numerator
equal to its denominator. This is not merely an additional unseen corner
case: the stated repair remains incomplete despite passing existing tests.
The diagnostic did not rerun the agent, change its task or alter the frozen
acceptance checks.

## Interpretation

This run fills the previously missing current-HEAD observation. Runtime
termination no longer exhausted the tool budget on this sample, but natural
termination does not imply a correct fix. The service correctly published the
incomplete candidate as **failed**, rather than accepting the model's final
answer or the passing development suite as sufficient evidence.

The November 8 baseline accepted 5/5 with 1/5 natural endings; this run accepts
4/5 with 5/5 natural endings. Different executions, accumulated changes and
model sampling prevent attributing that difference to one fix. It is not a
claim of improved repair success, proof that the inner loop is mature, or
evidence that replacing it is necessary.

Keep this result unchanged as the Pico reference for a later integration
comparison. New-engine results should disclose model/protocol/reasoning and
budget differences; the known five tasks remain regression data. Frozen new
tasks and fault-contract checks are still needed before a migration verdict.

Machine-readable details: [RESULTS.json](RESULTS.json). Original source and
reference provenance: [cases.json](../../cases.json).
