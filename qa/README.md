# qa

Owner: Yasmin Sherif Mohamed (QA and field testing). Week 3 of the team plan.

## Regression gate

A change that makes the numbers worse cannot be merged. On every push and pull request, GitHub runs `python -m qa.gate` (`.github/workflows/regression-gate.yml`). It replays the benchmark sessions through the datalogger and compares the numbers with `qa/baseline.json`. If one got worse, the check turns red and the table on the checks page shows which.

```
python -m qa.gate            # run it on your laptop before you push
python -m qa.gate --update   # accept today's numbers as the new baseline
```

Use `--update` only when a change is meant to move a number, and say why in the commit message. Numbers above their target never become the baseline.

## What fails the gate

| Number | Fails when |
|---|---|
| p95 latency | it rises by more than 10% (+1 ms), or goes above the target in `config/visionpath.toml` (300 ms) |
| Logged messages | fewer than the baseline: a change lost messages |
| Skipped messages | more than the baseline: a change broke the message format |
| Alerts, obstacles, black box snapshots | fewer than the baseline |
| Recall | drops by more than 0.02 |
| False alerts per 10 minutes | rise by more than 0.2 |

## Benchmarks

| Benchmark | What it is |
|---|---|
| `stub_10fps` | The 60 s stub scenario at 10 frames per second, with a near miss |
| `stub_30fps` | The same at 30 frames per second |
| `stub_fault` | The 10 fps scenario plus a system fault at 50 s |
| `qa/benchmarks/*.jsonl` | Real walk recordings, added from round 1 of the benchmark routes |

Today the benchmarks are recordings of the stub, so the gate guards the logging chain and the latency numbers it reports. When the detection, tracking and risk code joins the repository, its replay runs here too. Recall and false alerts are checked once the walk recordings come with ground truth; the rules for them are already in place.
