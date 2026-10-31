# dashboard

Owner: Yasmin Sherif Mohamed (QA and field testing). Week 3 of the team plan.

A web page that shows how VisionPath is doing against its targets, so the team and Dr Khaled can check the numbers in one place.

## Run

```
pip install -r dashboard/requirements.txt     # once: Streamlit (pandas comes with it)
streamlit run dashboard/app.py                # opens http://localhost:8501 in the browser
```

Ctrl+C in the terminal stops it. The first time, Streamlit asks for an email: press Enter to skip.

On Windows, double-click `dashboard\open_dashboard.bat` instead: it activates `.venv` and opens the page. Closing its window stops the dashboard. For a desktop icon: right-click the file, then Send to, then Desktop (create shortcut).

## What it shows

| Tab | Source | Target |
|---|---|---|
| Speed (R1) | The datalogger's database (`data/visionpath.db`): p95 of the slowest stage per session, every session, and one session stage by stage | p95 ≤ `[targets] latency_p95_ms` in `config/visionpath.toml` (300 ms) |
| Walk tests (R2, R4) | `dashboard/walk_results.csv`: typed in the page from the paper results summary, saved with **Save** | Recall ≥ 0.90 (R2) and ≤ 2 false alerts per 10 minutes (R4) |

R2 and R4 are proposals until Dr Khaled confirms them. To change them without touching the code, add `hazard_recall_min` or `false_alerts_per_10min_max` under `[targets]` in the config file.

## How the walk test numbers are computed

- Hazards = hits + late + misses (a blank late or misses counts as 0 once hits is filled)
- Recall = hits ÷ hazards
- False alerts per 10 minutes = false alerts ÷ minutes walked × 10

## Files

| File | What it is |
|---|---|
| `app.py` | The Streamlit page |
| `metrics.py` | The numbers (no Streamlit, tested in CI by `tests/test_dashboard_metrics.py`) |
| `walk_results.csv` | Walk test results, one line per route and round. Commit it after each round |
| `requirements.txt` | Streamlit |
