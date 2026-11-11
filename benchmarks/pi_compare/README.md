# Isolated Pi integration experiment — November 11

Decision: retain Pico as the default executor. Pi is an explicitly registered
experimental alternative, not a replacement service. PostgreSQL ownership,
generation fencing, cancellation, patch sealing and independent verification
remain in the existing Python service. No Robomp/Orbi queue was imported.

## Actual results

| Five known historical tasks | Independent acceptance | Natural final | Budget stop | Observed input/output tokens |
| --- | --- | --- | --- | --- |
| Pico current-head rerun | 4/5 | 5/5 | 0/5 | 35,788 / 2,880 |
| Pi service integration | 2/5 | 3/5 | 2/5 | 270,816 / 6,628 |

Pico ran at `0ecb78e`; Pi integration ran at `1e4a562`. Every task was submitted
once to its selected executor; the first two Pi integration jobs were adopted
by the five-case collector through the same idempotency keys, not rerun.
Both used deepseek-flash, thinking disabled, the same frozen task descriptions,
source snapshots and independent verifier. Service task/verification deadlines
were 240/60 seconds. The verifier and cases were unchanged.

Pi 1.5.1 was installed with an npm lockfile and ignore-scripts, using Node
24.16.0 and explicit Git Bash. Its provider is the same DeepSeek Anthropic
endpoint; an official request hook sets thinking disabled and max_tokens 4096.
Pico verifies public development checks automatically after edits; this Pi
adapter uses Pi's native loop without that post-edit callback. Internal prompts,
tools and orchestration differ. This is an integration comparison, not a
controlled causal claim about the inner loop alone.

Budgets also differ: Pico limits tool steps to 12; the evaluated Pi adapter
stops on the next assistant start after 13 finalized assistant messages, with a
230-second host deadline. Historical Pi JSON calls this max_model_calls, but
it is **not a hard cap of 13 HTTP requests**: the next request may already have
started. model_calls/usage are observed message_end metadata and may omit an
aborted/failed provider attempt. Current source labels this response budget
explicitly. No hard-provider-cap claim or billed monetary-cost claim is made.

Pi accepted the two itsdangerous patches. fractional-whole ended naturally but
failed the independent 0.9999999 assertion. intcomma-large-int and
naturalsize-rollover hit the response budget. The evaluated adapter retained
tool names and usage, but not full tool arguments/results, so the exact causes
of those two budget overruns were not fully diagnosed. Current source adds
redacted local tool-detail records for future runs; these five were not rerun.

One fixed-order sample on five already-known tasks does not establish general
engine quality or causal performance. It is sufficient to reject an immediate
default switch on the assumption that Pi must improve this deployment.

Raw service outcomes and all sealed patches, including rejected patches:

- [Pico rerun](../service_real/reruns/nov11-current-head/RESULTS.json)
- [Pico analysis](../service_real/reruns/nov11-current-head/ANALYSIS.md)
- [Pi rerun](../service_real/reruns/nov11-pi-service/RESULTS.json)

## Execution contracts and integration failures

No-model real Pi RPC command checks passed 8/8 with explicit Git Bash:
multiline Python + spaced cwd + Unicode, nonzero exit propagation, asyncio
Windows prerequisites, builtin format, command-environment credential removal,
non-UTF-8 output, RPC cancellation and host-deadline child-process reaping.

All earlier attempts are retained under [results](results/):

- Default shell resolved to an unusable legacy WSL Bash on this machine: 0/6
  observed command checks passed; cancellation was not reached.
- Explicit Git Bash passed six command checks, but the smoke harness's Windows
  PID-marker quoting failed. Normalizing the marker path fixed that test input.
- The next attempt passed 7/8; the host-deadline check sampled immediately
  after asynchronous job termination. A bounded wait of up to five seconds
  established reaping in the final 8/8 run. This is not an instantaneous-exit
  guarantee.
- First live model smoke failed with HTTP 404 because the adapter added /v1
  before Anthropic SDK appended /v1/messages. Correcting the base mapping
  produced one real model-side bash execution and a natural final (two assistant
  messages), with host-checked proof that credentials were absent and format()
  produced 1,234,567. Both results are retained.

The RPC smoke exercises user_bash; the additional live proof exercises the
model-side bash override. These are separate paths. Shell environment filtering
does not isolate host files or other processes; worktrees remain Host execution.
No arbitrary repository/security-boundary guarantee is claimed.

Current protocol tests reject extension_error, handled/queued/missing prompt
dispositions, low-level agent_end without settlement, assistant errors and
cleanup failure. An accepted prompt alone is not completion. Final service
success still requires independent verification of the sealed patch.

## Reproduce

The original service, five prepared snapshots, Python dependencies and local
credentials must already exist; see ../service_real/README.md. From this folder:

```powershell
npm ci --ignore-scripts --no-audit --no-fund
```

From the repository root, register trusted Pi aliases (use absolute installed
paths; this machine's tested installation lives outside the repository):

```powershell
python -m benchmarks.pi_compare.register --node <node.exe> --cli <pi/dist/cli.js> --shell <Git/bin/bash.exe>
# Restart the idle API/workers to load new repository IDs.
python -m benchmarks.pi_compare.contract_smoke --node <node.exe> --cli <pi/dist/cli.js> --shell <Git/bin/bash.exe> --output artifacts/pi-new-contract.json
python -m benchmarks.service_real.run --engine pi --run-label a-new-label
```

The original IDs retain Pico; aliases ending --pi select Pi. A task placeholder
is substituted only when it occupies a complete trusted argv element, followed
after -- in the adapter command. User text is not parsed as shell or CLI options.
No global Pi install, user Pi settings modification or hidden-verifier prompt
injection is needed. Project extension/MCP/skill/context discovery is disabled
for this bounded comparison; only service_extension.ts is loaded.

## Checks and follow-up boundary

The combined service/Windows/verification/security/checkpoint/Pi suite passed
70 tests before the final protocol additions; the final Pi protocol suite
passed 12 tests. Ruff passed. These are targeted checks, not the whole repository.

Do not tune the known cases until Pi appears to win. Future work, if justified,
should freeze acceptance/budget definitions before collecting new tasks and
include known unsupported security and failure boundaries. No independent
held-out repair benchmark has been completed in this round.
