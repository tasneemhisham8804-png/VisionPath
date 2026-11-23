# What the wellbeing check needs from each module

Proposed messages. Only `risk_alert` is in the data contract (1.0.0) today; the others are stand-ins in the replays until their owners send them. Each is one JSON line with `msg_type` and `t_mono_ns`, like every contract message, so they can join the contract later (a minor bump, Tasneem signs off).

| From | Message | Fields the check uses | Used for |
|---|---|---|---|
| Mariam (localization) | `motion_state`, about once a second | `moving` (true/false), `still_since_ns` (optional), `place` (last trusted place: `lat`, `lon`) | When to ask, and the place sent to the caregiver |
| Mostafa (voice) | `voice_intent` | `intent` = `im_okay` | The voice answer. Any other intent is not an answer |
| Malak Emad (buttons) | `button_press` | `press` = `short` or `long` | Short = the answer. Long = the emergency button (the check stops) |
| Malak Ibrahim (fall) | `fall_detected` | (none) | A fall always wins: the check stops |
| Tasneem (risk) | `risk_alert` (contract) | `priority` | A critical or high alert plays first, then the question is asked again |

## What the check sends back

| To | Action | Contents |
|---|---|---|
| Output (Baraa, Mostafa) | `ask` | Voice line `wellbeing_question`, vibration `wellbeing_question`, priority `low` (danger and emergencies cut it off) |
| Output | `thanks` | Voice line `wellbeing_thanks` ("Okay, thanks") |
| Backend (Abdelrahman), then the caregiver app (Tasneem, FCM) | `notify_caregiver` | Last place, minutes still, tries. Saved as an `emergency_events` row with trigger `wellbeing` |
| Backend | `tell_caregiver_okay` | The user answered after the caregiver was told |

## Questions for the owners

1. Mariam: can `motion_state` come once a second, with the last trusted place in it?
2. Mostafa: what does the question say, in Arabic and English? Is `im_okay` the intent name?
3. Malak Emad: is a long press 3 s? Does a short press on any button count?
4. Baraa: the vibration pattern for the question must differ from the obstacle and "system down" patterns (D17).
