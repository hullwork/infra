# Benchmark report template

This template is fail-closed. Source checks, CI, runtime reconciliation, and
measured benchmarks are distinct evidence layers. Do not convert an unexecuted
scenario into a score.

## Evidence layers for this snapshot

| Layer | Status | Evidence |
| --- | --- | --- |
| Source | **NOT RUN** | command, revision, exit status |
| CI | **NOT RUN** | workflow, run id, conclusion |
| Runtime | **NOT RUN** | cluster, revision, health/sync evidence |
| Benchmark | **NOT RUN** | profile, iterations, raw output |

Source success is not CI execution. CI success is not runtime reconciliation.
Runtime health is not a performance result. Mocked or fixture-only results must
remain unscored.

## Pending benchmark matrix

| Scenario | Required method | Status | Run ID | Inputs | Trials | Result | Raw evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Fresh clone validation | clean machine, locked dependencies | **NOT RUN** | — | — | — | — | — |
| Deterministic render | byte-identical output for same inputs | **NOT RUN** | — | — | — | — | — |
| External Helm composition | unrelated immutable chart | **NOT RUN** | — | — | — | — | — |
| Render performance | p50/p95 with >=100 cold renders | **NOT RUN** | — | — | — | — | — |
| Argo reconciliation | real cluster sync and health | **NOT RUN** | — | — | — | — | — |
| Drift recovery | controlled mutation then reconcile | **NOT RUN** | — | — | — | — | — |
| NodePool scale-up | real worker, CNI and workload convergence | **NOT RUN** | — | — | — | — | — |
| NodePool fail-closed drain | active pod, local PVC, unsafe emptyDir | **NOT RUN** | — | — | — | — | — |
| Provider phase latency | p50/p95 by event phase | **NOT RUN** | — | — | — | — | — |

## Scoring

An excellent score requires all of:

- pre-registered thresholds;
- immutable inputs recorded by digest or commit;
- enough trials for p50 and p95;
- all trials completed without uncontrolled retries;
- machine-readable raw evidence retained;
- no mock, fixture-only, dry-run, or skipped case counted as passed; and
- a second run within the stated confidence interval.

## Evidence bundle contract

Every scored release must include the tool version, command line, environment
class, input digests, start/end timestamps, raw samples, failure count, and
verification command. Manual summaries are not substitutes for raw output.
