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

## Walk test report (Week 6)

The numbers of a walk test (or a route round), from the paper field testing sheets and the device database.

```
python -m qa.walk_report --demo                                   # made-up walk test, changes nothing
python -m qa.walk_report --new D:\GRADUATION\walk_test_1          # a folder with the 2 empty sheets
python -m qa.walk_report D:\GRADUATION\walk_test_1 --round wt1    # the report
```

1. Before the walk test: `--new` makes `walk_log.csv` and `labels.csv`, one per paper sheet (pages 7 and 8).
2. After it: copy the paper into them in Excel. Format the time columns as **Text** first, so Excel keeps `2:40` as it is. Save as CSV.
   * `walk_log.csv`: one line per walk. `session` is the session number the datalogger printed. `minutes_walked` can stay blank: it then comes from the database.
   * `labels.csv`: one line per hazard on the video (`type` = `hazard`) and one per false alert (`type` = `false_alert`). `result` can stay blank: Hit when `alert_at` is at or before `reached_2m_at`, Late when after, Miss when there is no `alert_at`.
3. Copy the device database from the Pi (`data/visionpath.db`) to the same place on the laptop, or give it with `--db`.
4. Run the report with the round: `wt1`, `wt2` (walk tests) or `r1`, `r2`, `r3` (route rounds).

It writes `report.md` in the folder (targets, each route, each walk, and every miss, late alert and false alert to check on the video) and puts one row per route plus an `All` row in `dashboard/walk_results.csv`. Running it again replaces those rows, it never doubles them. `--no-dashboard` leaves the dashboard alone.

| Target | How it is worked out |
|---|---|
| R2 recall >= 0.90 | Hits / (Hits + Late + Misses), high-risk hazards only |
| R4 <= 2 false alerts per 10 min | False alerts / minutes walked x 10 |
| R1 p95 <= 300 ms | p95 of the slowest stage, all the walks' sessions together |
| R13 collisions | Counted here, compared with the cane alone in Week 10 |

R2 and R4 are proposals until Dr Khaled approves the evaluation methodology. Exit code: 0 every measured target met, 1 one not met, 2 a problem in the sheets (the message says which file and line).

## End-to-end check (Week 8)

Everything in this branch working together, from the first message to the last.

```
python -m qa.e2e                                  # laptop: two made-up walks through the whole chain
python -m qa.e2e --db data/visionpath.db          # Pi: check the last real session
python -m qa.e2e --db data/visionpath.db --session 12
```

On the laptop, two walks of the same route (the stub sensor stream with a pedestrian and a near miss, plus four fixed landmarks) go through 9 steps: datalogger, latency report (R1), black box, Rerun viewer, dashboard, landmark memory, familiar route, wellbeing check (saved in the walk's session) and the walk test report. Every step must say `ok`.

On the Pi, `--db` checks a session recorded with the real sensors:

| Step | Passes when |
|---|---|
| Session | The session ended (stop the datalogger first) |
| Every stage sent messages | detection, fusion, tracking, risk and output all logged latency rows |
| Latency (R1) | p95 of the slowest stage within the target |
| Alerts reach the output | Every alert got a vibration or voice command |
| Black box on the real sensors | Every snapshot file exists, holds camera and ultrasonic messages, and the camera is not `replay` |

The database can also be copied to the laptop with its `blackbox` folder next to it: the files are found by name.

## On the Pi (Week 8)

1. Start the bus, the modules and the datalogger (`python -m datalogger.bus`, then `python -m datalogger.run`).
2. In another terminal: `python -m wellbeing.live` (the wellbeing check on the bus).
3. Walk, and get close to an obstacle once, so a near miss saves a black box file. Stop the datalogger with Ctrl+C.
4. `python -m qa.e2e --db data/visionpath.db`: all 5 steps `ok`.
5. `python -m landmarks.bench --backend chroma` (after `pip install chromadb`): the landmark store's speed on the Pi.
