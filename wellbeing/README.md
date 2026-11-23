# wellbeing

Owner: Yasmin Sherif Mohamed. Week 7 of the team plan.

When the user has not moved for a while, the device asks "are you okay?". If there is no answer, the caregiver's app is told where the user is.

## Run

```
python -m wellbeing.cases                  # the 11 test cases, replayed as bus messages
python -m wellbeing.cases --save FOLDER    # also write each case as a .jsonl recording
```

## The rule (team decision)

| Step | What happens |
|---|---|
| Still for 15 min | The device asks "are you okay?" (voice and vibration). The caregiver can change the 15 min |
| The user answers | Only by voice ("I'm okay") or a short press on a button. Walking away is **not** an answer |
| No answer in 30 s | Asked a second time |
| Still no answer after 30 s | The caregiver's app is told, with the last trusted place |
| After "I'm okay" | The timer starts again: the next question comes after another 15 min still |

Never in the way of something more urgent:

| While the question is open | What happens |
|---|---|
| A fall | The fall flow takes over, the check stops |
| A long press (emergency button) | The emergency flow takes over, the check stops |
| A danger alert (critical or high) | The alert plays first, then the question is asked again and the wait starts again |

No new check during the caregiver's quiet hours (e.g. 23:00-07:00) or when the motion signal is lost. All the numbers are in `config/visionpath.toml`, section `[wellbeing]`.

## Files

| File | What it is |
|---|---|
| `check.py` | The logic, and `save()` that writes a check to the on-device database (`wellbeing_checks`, `emergency_events`) |
| `cases.py` | The 11 test cases as recordings, and the runner |
| `tests_plan.md` | The 11 test cases written out (Week 7 deliverable) |
| `inputs.md` | The messages the check needs from the other modules, and what it sends back |

## Done now, and what waits

| Part | Status |
|---|---|
| The logic, the 11 cases, saving to the database | Done, tested on replays |
| Motion signal | Waits for Mariam (`motion_state`). The cases use a stand-in |
| "I'm okay" by voice | Waits for Mostafa (`voice_intent`) |
| Buttons | Waits for Malak Emad (`button_press`) |
| Fall | Waits for Malak Ibrahim (`fall_detected`) |
| Telling the caregiver | Waits for Abdelrahman (backend) and Tasneem (FCM) |
| Live on the device | Week 8 |
