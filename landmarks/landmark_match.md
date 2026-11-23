# Proposal: `landmark_match` message (contract 1.x.0)

From: Yasmin (landmark memory). For: Tasneem (contract owner) and Mariam (localization).
Status: **proposed**, waiting for sign-off. A new message type is a minor bump.

## Why

Week 6 of the team plan: "send the landmark matches to Mariam for loop closure". When the landmark memory recognises a fixed object from an earlier walk, the localization can correct the user's place to the stored one. Mariam's Week 8 step uses it: "when Yasmin says this place was seen before, correct the user's place to the stored place".

## When it is sent

About once a second per recognised landmark: every time a fingerprint matches one from an **earlier** walk (same class, within 3 m, similarity of 0.90 or more). Never for moving objects, and never for a match on the same walk.

## Fields

Envelope as every message (`seq`, `t_mono_ns`, `t_origin_ns`, `source`, `confidence`, `bearing`, `schema_version`). `t_origin_ns` is copied from the track, so its latency is measured like every other stage. `confidence` = `similarity`.

| Field | Type | Meaning |
|---|---|---|
| `track_id` | int | The track that was recognised |
| `hazard` | HazardClass | A fixed class: curb, step, pole, overhead, manhole, pothole, hanging_cable, drop |
| `landmark_x_m`, `landmark_y_m` | float | Where the earlier walk stored it, in the localization's map (metres) |
| `distance_m`, `bearing_deg` | float | Where it is now from the user, copied from the track (negative bearing = left) |
| `similarity` | float 0..1 | How alike the two fingerprints are |
| `matched_session` | int or null | The earlier walk it was stored on |

Sample: [`samples/landmark_match.json`](samples/landmark_match.json).

## How Mariam uses it

```python
from landmarks.messages import parse, where_is_the_user
match = parse(line)
user_x, user_y = where_is_the_user(match, heading_deg)     # stored landmark place minus the offset to it
```

`python -m landmarks.familiar_demo` checks this: on the second walk the localization is 1.5 m off on purpose, and the place from `landmark_match` is within 0.3 m of the true place.

## Questions for sign-off

1. Tasneem: add it together with `ego_motion` in 1.2.0, or as its own 1.3.0? Same names and units as above?
2. Mariam: is one message per second per landmark enough, or do you want only the best match per landmark?
3. Mariam: the place is in the map frame of the walk it was stored on. Is that the same frame on every walk (start of the route = 0, 0)?

Until it is signed off, the message lives in `landmarks/messages.py` and is not sent on the bus: the datalogger would count it as an unknown message.
