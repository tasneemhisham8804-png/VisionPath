"""
Open a black box snapshot in the Rerun viewer: every sensor on one timeline.

    python -m replay_viewer                     # the newest snapshot in the black box folder
    python -m replay_viewer path\\to\\file.jsonl  # one snapshot, or any contract recording
    python -m replay_viewer --save out.rrd      # save a Rerun file instead of opening the viewer

The viewer opens with six boxes:
    top:    Camera (detection boxes) | From above (obstacles around the user) | What happened (alerts)
    bottom: Distance (m) | Seconds to collision | Speed of each stage (ms)
The side panels start folded, so only the boxes and the time bar show.

Needs the Rerun SDK (pip install -r replay_viewer/requirements.txt). The calls
below work with the older and the newer Rerun APIs.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from replay_viewer.timeline import Timeline, load

APP_ID = "visionpath_blackbox"


# ---------- small helpers so one script works across Rerun versions ----------

def set_time(rr, seconds: float) -> None:
    if hasattr(rr, "set_time"):                       # Rerun 0.23 and newer
        try:
            rr.set_time("time", duration=seconds)
            return
        except TypeError:
            pass
    rr.set_time_seconds("time", seconds)              # older Rerun


def scalar(rr, value: float):
    if hasattr(rr, "Scalars"):                        # Rerun 0.23 and newer
        return rr.Scalars([value])
    return rr.Scalar(value)                           # older Rerun


def text_level(rr, level: str):
    levels = getattr(rr, "TextLogLevel", None)
    return getattr(levels, level, level) if levels is not None else level


def log_events(rr, timeline: Timeline) -> int:
    """Send every event to Rerun. Returns how many were sent."""
    for e in timeline.events:
        set_time(rr, e.t)
        d = e.data
        if e.kind == "boxes":
            rr.log(e.path, rr.Boxes2D(mins=d["mins"], sizes=d["sizes"], labels=d["labels"]))
        elif e.kind == "clear":
            if hasattr(rr, "Clear"):
                rr.log(e.path, rr.Clear(recursive=False))
        elif e.kind == "scalar":
            rr.log(e.path, scalar(rr, d["value"]))
        elif e.kind == "point":
            rr.log(e.path, rr.Points2D([d["xy"]], labels=[d["label"]], radii=[d["radius"]]))
        elif e.kind == "text":
            rr.log(e.path, rr.TextLog(d["text"], level=text_level(rr, d["level"])))
    return len(timeline.events)


def layout(rr):
    """The fixed screen: six named boxes, side panels folded. None if this Rerun has no blueprint API."""
    try:
        rrb = importlib.import_module("rerun.blueprint")

        def folded(panel):
            try:
                return panel(state="collapsed")
            except TypeError:                         # older Rerun
                return panel(expanded=False)

        return rrb.Blueprint(
            rrb.Vertical(
                rrb.Horizontal(
                    rrb.Spatial2DView(origin="/camera", name="Camera: what it saw"),
                    rrb.Spatial2DView(origin="/top_view", name="From above (ahead is up)"),
                    rrb.TextLogView(origin="/alerts", name="What happened"),
                ),
                rrb.Horizontal(
                    rrb.TimeSeriesView(origin="/distance", name="Distance (m)"),
                    rrb.TimeSeriesView(origin="/time_to_collision", name="Seconds to collision"),
                    rrb.TimeSeriesView(origin="/latency_ms", name="Speed of each stage (ms)"),
                ),
            ),
            folded(rrb.BlueprintPanel),
            folded(rrb.SelectionPanel),
            folded(rrb.TimePanel),
        )
    except Exception:                                 # any API difference: keep Rerun's own layout
        return None


# ---------- command line ----------

def newest_snapshot(folder: Path) -> Path | None:
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def settings() -> tuple[Path, float, float]:
    """Black box folder, latency target (ms) and near miss TTC (s) from the config file."""
    from datalogger.config import ConfigError, datalogger_settings, latency_target_ms, load_config
    try:
        cfg = load_config()
        s = datalogger_settings(cfg)
        return Path(s["blackbox_dir"]), latency_target_ms(cfg) or 300.0, float(s["near_miss_ttc_s"])
    except ConfigError:
        return Path(__file__).resolve().parents[1] / "data" / "blackbox", 300.0, 1.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Open a black box snapshot in the Rerun viewer.")
    ap.add_argument("file", nargs="?", help="a .jsonl snapshot or recording (default: the newest snapshot)")
    ap.add_argument("--save", metavar="OUT.rrd", help="save a Rerun file instead of opening the viewer")
    args = ap.parse_args(argv)

    folder, target_ms, near_miss_s = settings()
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"File not found: {path}", file=sys.stderr)
            return 2
    else:
        path = newest_snapshot(folder)
        if path is None:
            print(f"No snapshot in {folder}. Run the datalogger with the stub publisher first.", file=sys.stderr)
            return 2

    timeline = load(path, latency_target_ms=target_ms, near_miss_ttc_s=near_miss_s)
    if not timeline.messages:
        print(f"{path.name}: no valid messages ({timeline.skipped} lines skipped).", file=sys.stderr)
        return 2

    try:
        import rerun as rr
    except ImportError:
        print("The Rerun SDK is not installed. Run: pip install -r replay_viewer/requirements.txt", file=sys.stderr)
        return 2

    if args.save:
        rr.init(APP_ID)
        rr.save(args.save)
    else:
        rr.init(APP_ID, spawn=True)
    blueprint = layout(rr)
    if blueprint is not None and hasattr(rr, "send_blueprint"):
        try:
            rr.send_blueprint(blueprint)
        except Exception:
            blueprint = None
    sent = log_events(rr, timeline)

    kinds = ", ".join(f"{k} {n}" for k, n in sorted(timeline.counts.items()))
    print(f"{path.name}: {timeline.messages} messages over {timeline.duration_s:.1f} s ({kinds})")
    if timeline.skipped:
        print(f"  {timeline.skipped} lines skipped (unknown type, newer version or invalid)")
    print(f"  {sent} items sent to " + (args.save if args.save else "the Rerun viewer")
          + ("" if blueprint is not None else " (Rerun's own layout)"))
    return 0
