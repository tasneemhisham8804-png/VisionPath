# Wellbeing check: the 11 test cases

Owner: Yasmin Sherif Mohamed (QA). Week 7 of the team plan: "write 11 test cases for the wellbeing check, build its logic and test it on the replay".

`python -m wellbeing.cases` replays all 11 and must print `11 of 11 cases pass`. The same cases run in CI with every push (`tests/test_wellbeing.py`). In Week 8 they are run again live on the device, and in Week 9 they go in the QA matrix.

Settings for every case unless it says otherwise: ask after 15 min still, wait 30 s for an answer, 2 tries, then tell the caregiver.

| # | Case | What the user does | Must happen |
|---|---|---|---|
| 1 | Says "I'm okay" | Still 15 min, says "I'm okay" 5 s after the question, stays still 10 more min | Asked once, "thanks", caregiver not told. No second question in the next 10 min |
| 2 | Short press | Still 15 min, short press on a button after 8 s | Asked once, okay by button, caregiver not told |
| 3 | No answer | Still 15 min, then nothing for 70 s | Asked twice (at 15:00 and 15:30), caregiver told at 16:00 with the last place |
| 4 | Answers the second question | Still 15 min, no answer to the first question, "I'm okay" after the second | Asked twice, okay by voice, caregiver not told |
| 5 | Falls during the question | Still 15 min, falls 3 s after the question | The check stops, the fall flow takes over. The wellbeing check does not tell the caregiver (the fall flow does) |
| 6 | Walks away | Still 15 min, walks away instead of answering | Walking is not an answer: asked twice, caregiver told |
| 7 | Moves before 15 min | Still 14 min, walks 2 min, still 5 min | No question |
| 8 | Quiet hours | Quiet hours 23:00-07:00, still 20 min starting at 23:30 | No question |
| 9 | Danger alert during the question | Still 15 min, a high alert 20 s after the question, "I'm okay" 20 s later | The alert plays first, the question is asked again and the wait starts again. Okay by voice, caregiver not told |
| 10 | Says something else | Still 15 min, says "repeat" instead of "I'm okay" | Not an answer: asked twice, caregiver told |
| 11 | Caregiver changed the time | Interval set to 30 min, still 30 min, short press | No question at 15 min, asked at 30 min, okay by button |

## Also tested (not in the 11)

| Case | Must happen |
|---|---|
| Long press (emergency button) during the question | The check stops, the emergency flow takes over |
| "I'm okay" after the caregiver was told | The caregiver is told the user is okay |
| Motion signal lost | No new check until it is back |
| Every check saved to the on-device database | `wellbeing_checks` row, and an `emergency_events` row (trigger `wellbeing`) when the caregiver is told |
