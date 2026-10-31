# replay_viewer

Owner: Yasmin Sherif Mohamed (QA and field testing). Week 3 of the team plan.

Opens a black box snapshot in the [Rerun](https://rerun.io) viewer, with every sensor and module on one timeline. When something goes wrong on a walk, it shows what each part of the system saw, second by second.

## Run

```
pip install -r replay_viewer/requirements.txt         # once: the Rerun SDK and its viewer
python -m replay_viewer                               # the newest snapshot in data/blackbox
python -m replay_viewer data/blackbox/<file>.jsonl    # one snapshot, or any contract recording
python -m replay_viewer <file>.jsonl --save out.rrd   # a Rerun file to send to a teammate
rerun out.rrd                                         # open a saved Rerun file
```

On Windows, double-click `replay_viewer\open_viewer.bat` to open the newest snapshot, or drag a `.jsonl` file onto it.

## What you see

The viewer opens with six boxes and the time bar. The side panels start folded.

| Box | Shows |
|---|---|
| Camera: what it saw | A box around each obstacle the camera detected, with its name and score. The black box keeps no images, so the frame is an empty outline |
| From above (ahead is up) | The user in the middle and each obstacle around them, with its distance |
| What happened | A short log: the warning, the vibration or voice line, and the moment the black box saved |
| Distance (m) | How far the obstacle is, from the camera track and from each ultrasonic sensor |
| Seconds to collision | Time to collision of each obstacle, with a flat line at the near miss limit (1 s) |
| Speed of each stage (ms) | How long each step of the system took, with a flat line at the target (300 ms) |

Drag along the time bar, or press play. Lines that are not valid contract messages (an unknown type, a newer schema version) are skipped, and the command prints how many.

If a Rerun version cannot take the fixed layout, the command says "Rerun's own layout" and the same data shows in Rerun's default boxes.

## 10-minute team walkthrough

1. **Why (1 minute).** On a near miss, a fault or a fall, the black box saves the last 30 seconds. This viewer replays them, so we find the cause without repeating the walk.
2. **Open (2 minutes).** Double-click `open_viewer.bat`. Show the six boxes and the time bar.
3. **Read (3 minutes).** Press play and follow one obstacle: its box appears in Camera, it comes closer in From above, Distance and Seconds to collision fall, then the warning and the vibration appear in What happened.
4. **Find the cause (3 minutes).** Ask three questions in order:
    - Did the camera see it? No box in Camera means detection.
    - Did the tracker follow it? A gap or a jump in Distance means fusion or tracking.
    - Did the warning come in time? A warning after Seconds to collision crossed the 1 s line means risk or output.
5. **Share (1 minute).** Save with `--save`, and attach the `.rrd` file to the issue for the module's owner.

## Files

| File | What it is |
|---|---|
| `timeline.py` | Snapshot lines into timeline events (no Rerun, tested in CI by `tests/test_replay_viewer.py`) |
| `view.py` | Sends the events to Rerun; works with the older and newer Rerun APIs |
| `open_viewer.bat` | Windows: double-click or drag a file onto it |
| `requirements.txt` | The Rerun SDK |
