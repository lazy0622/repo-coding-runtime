# Why the Pi service result was 2/5

Supplementary diagnosis after the original five-case run. No model was rerun,
no historical score or task state was changed, and no generated source was
manually repaired. These checks do not constitute new service acceptances.

The original service accepted two itsdangerous patches. Three other outcomes:

| Case | Original service result | Post-hoc finding |
| --- | --- | --- |
| intcomma-large-int | Adapter stopped after response budget; nonzero exit before patch sealing | Retained candidate still raises OverflowError when converting a very large integer to float; the independent behavior check fails |
| fractional-whole | Natural final, sealed patch, independent verification failed | Patch handles carrying nonzero whole parts, but misses 0.9999999 where whole_number is zero; returns 1/1 instead of 1 |
| naturalsize-rollover | Adapter stopped after response budget; nonzero exit before patch sealing | Retained source passes independent behavior checks; original full-patch validation would reject the changed upstream regression file. The diff only adds five test cases, rather than deleting or weakening existing assertions |

For naturalsize, a separate fresh worktree was created at the same frozen base,
and only the already-generated src/humanize/filesize.py diff was applied.
The same verifier then passed independent behavior checks plus all 70 unchanged
original regression tests. Candidate-added tests were omitted explicitly for
this diagnostic; no source code was edited. The temporary worktree was removed.
This is evidence about the source fix, not a retroactive passing service result.

There are two acceptance-policy confounders:

1. Pico can return zero after a bounded stopped run, allowing the service to seal
   and verify a candidate. The experimental Pi adapter returns nonzero on its
   response-budget stop, so the service skips sealing and verification. The two
   budget-stop paths are not symmetric.
2. The benchmark requires original regression files to remain byte-identical.
   This catches weakening tests, but also rejects ordinary additions. The task
   said to make a minimal source change; it did not explicitly explain the
   byte-identical test-file rule. Adding tests is not, by itself, bad engineering.

Therefore 2/5 is the original service acceptance count. It must not be presented
as proof that Pi produced only two behaviorally valid source fixes. Conversely,
the diagnostic cannot be relabeled 3/5 original acceptance or a general solve
rate: one source-only diagnostic passed, and two actual behavior defects remain.

Future comparisons should predefine symmetric stopped-run handling and a
server-owned copy of immutable tests outside the candidate tree, allowing
candidate-added tests without letting the agent weaken the acceptance oracle.
Such a policy change requires a newly labeled experiment; do not change the
old verifier or silently replace its results.

Artifacts:

- [Retained-workspace checks](results/budget-candidate-diagnostic.json)
- [Fresh source-only check](results/naturalsize-source-only-diagnostic.json)
- [Original Pi result](../service_real/reruns/nov11-pi-service/RESULTS.json)
