# November 11 regression runs

These are reruns of the same five known historical tasks, not held-out tasks.
The November 8 results remain unchanged. All runs use deepseek-flash with
thinking disabled and the frozen independent verifier.

| Run | Independent acceptance | Model final answer | Configured verified stop |
| --- | --- | --- | --- |
| nov11-windows-fix | 4/5 | 1/5 | not enabled |
| nov11-verified-stop | 5/5 | 0/5 | 0/5 |
| nov11-verified-env | 3/5 | 0/5 | 5/5 |

The first run includes one model error. In the second run, Windows development
verification failed because the filtered subprocess environment omitted
SystemRoot; all five attempts exhausted the step budget. Preserving Windows
prerequisites and UTF-8 output fixed execution of that verifier.

The third run exposed a policy regression: existing upstream tests can pass
after an incomplete edit. Automatically ending on those tests stopped all five
agents, but two incomplete patches failed independent acceptance. The worker
therefore no longer enables --stop-after-verification. The explicit CLI option
remains available for callers whose checks actually define completion; it is
not a generic bug-fix completion criterion. No automatic retry replaced these
failures, and no claim of improved repair success is made.

Verified fixes include Windows inline Python execution/cwd/exit codes,
subprocess environment and encoding, builtin format() false positives, explicit
no-change results, temporary verification-worktree removal, and opt-in archival
cleanup of terminal workspaces. Agent workspaces remain until explicit cleanup.
The targeted service, Windows, verification, security and checkpoint suite
passed 60 tests; an additional regression checks that development checks do not
force early completion (1 passed).

## Architecture decision still pending

The current Python agent owns model adaptation, command execution, tools,
context, completion and session recovery as well as a separate task service.
The observed failures justify evaluating a maintained inner agent, rather than
adding more features to the handwritten loop.

Preferred candidate: Pi via its language-independent RPC subprocess interface.
Retain the Python API, PostgreSQL ownership/leases, generation fencing, isolated
worktrees, sealed patches, independent acceptance and audit records. A Pi
session is not a database job or an exactly-once execution guarantee.

Before switching, pin a release/commit and compare both engines with the same
model, reasoning setting, task set and independent acceptance. Record accepted
patches, true final states, tokens, time, cancellation, worker death and stale
publication. Add frozen unseen tasks; the five known tasks are regression only.
No Pi integration or performance improvement has been implemented or claimed.

Sources checked November 11:

- [Pi](https://github.com/earendil-works/pi), commit 74dcca7d4140403eaa81c3e7124e73b63a0cb106
- [RPC contract](https://github.com/earendil-works/pi/blob/74dcca7d4140403eaa81c3e7124e73b63a0cb106/packages/coding-agent/docs/rpc.md)
- [Windows shell support](https://github.com/earendil-works/pi/blob/74dcca7d4140403eaa81c3e7124e73b63a0cb106/packages/coding-agent/docs/windows.md)
- [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk)

This is a documentation/interface assessment, not an installed-engine bakeoff.
