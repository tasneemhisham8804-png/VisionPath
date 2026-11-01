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

## Fault injection (Week 5)

```
python -m qa.faults          # break a recording on purpose and check what happens, row by row
```

It walks the degraded modes table of the TDR (section 7). For each failure it changes a recorded session the way the real fault would, then checks what exists today:

- **The health watch** (`qa/health.py`) notices the failure as the table's "Detected by" column says: camera down after 0.5 s without a frame, ultrasonic down when silent or frozen (the same reading sent again and again), a detection module down after 1 s.
- **The datalogger and the black box** keep working: the other sensors are still logged, broken messages are counted and skipped, the black box file stays in time order, a system fault saves a fault snapshot.
- **The regression gate** fails a stage that gets slower.
- **The bus** can restart mid-session: the logger reconnects by itself.

The system's own behaviour in each mode (ultrasonic only, earlier alerts, the outbox, the fail-safe buzzer...) belongs to modules that are not in the repository yet. Those rows are skipped in the tests and say who they wait for; they turn into real checks as each module arrives.

The same checks run in CI on every push, through `tests/test_fault_injection.py`.
