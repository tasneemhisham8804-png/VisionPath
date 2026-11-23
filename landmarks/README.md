# landmarks

Owner: Yasmin Sherif Mohamed. Week 4 of the team plan (stretch).

The device remembers fixed objects it has passed: poles, steps, curbs, overhead signs. On a familiar route it can skip the same alert every day, and the localization (Mariam) can use a recognised landmark to correct the user's position.

## Run

```
python -m landmarks.demo                     # walk the same corridor twice: the second walk recognises the landmarks
python -m landmarks.familiar_demo            # Week 6: fewer spoken alerts on the familiar walk, landmark_match for Mariam
python -m landmarks.bench                    # speed and size of the store (run it on the Pi too)
pip install chromadb                         # optional: the team plan's database
python -m landmarks.demo --backend chroma
python -m landmarks.bench --backend chroma
```

## How it works

1. **Which objects.** A track gets a fingerprint when it is a fixed object (curb, step, pole, overhead, manhole, pothole, hanging cable, drop), it has been followed for at least 15 frames, and its last fingerprint is at least 1 s old. Moving things (pedestrians, motorbikes, tuktuks, vehicles) are never landmarks. Once ego motion arrives, an object that moves by itself is left out too.
2. **Fingerprint.** A list of numbers that describes how the object looks. Two sightings of the same object give close fingerprints.
3. **Place.** Where the object is, in metres: the user's place plus the track's distance and bearing.
4. **Memory.** Every fingerprint is stored with its class, place, walk and time. A new sighting is "seen before" when a fingerprint from an earlier walk, of the same class, within 3 m, has a similarity of 0.90 or more.

## Familiar route: fewer repeated alerts (Week 6)

When the memory says a fixed object was seen at the same place on an earlier walk, its first alert on this walk is played in full (voice and vibration). Later alerts for that landmark on this walk are **vibration only**. Every alert still vibrates.

Never made quieter, always voice and vibration:

| Alert | Why |
|---|---|
| Priority critical | Urgent |
| Closer than 1.5 m, or less than 2 s to collision | Urgent |
| Pedestrian, motorbike, tuktuk, vehicle, unknown obstacle | It moves, so it is never familiar |
| Not seen on an earlier walk | New |
| No track behind it (system fault, ultrasonic only) | Not a landmark |

The numbers are in `config/visionpath.toml`, section `[familiar]`. `familiar.py` sits between the risk module and the output module: `decide(risk_alert)` says full or vibration only, and `keep(output_command)` drops only the voice of a vibration-only alert.

`python -m landmarks.familiar_demo` walks the corridor twice: walk 1 speaks all 9 alerts of the 4 landmarks, walk 2 speaks 5 (44% fewer), and no urgent, new or moving alert is made quieter.

## landmark_match for Mariam (Week 6)

Every recognised landmark also gives a `landmark_match` message: the landmark's stored place plus where it is now from the user. The localization uses it for loop closure. It is a **proposed** new contract message, waiting for Tasneem and Mariam: see [`landmark_match.md`](landmark_match.md).

## Done now, and what waits

| Part | Status |
|---|---|
| Store (`simple` SQLite, or `chroma` ChromaDB) | Done, tested on both |
| Which tracks get a fingerprint | Done, tested on the contract `track` messages |
| Seen before on an earlier walk | Done, tested |
| Fewer repeated alerts on a familiar route | Done, tested on fake alerts. Waits for the risk and output modules to call it |
| `landmark_match` message for Mariam | Done, tested. Waits for sign-off to join the contract (Tasneem, Mariam) |
| Real fingerprints from a small model on the Hailo | Waits: the model is chosen with Talal. `fake.py` stands in until then |
| ChromaDB on the Pi | Waits for the Pi: run `python -m landmarks.bench --backend chroma` there |
| The user's place | Waits for the localization (Mariam). The demo walks a straight corridor |
| Ego motion (is the object moving by itself) | Waits for ego motion in the data contract |

## Files

| File | What it is |
|---|---|
| `store.py` | Where fingerprints are kept, and the search for the closest one |
| `memory.py` | Which tracks get a fingerprint, the landmark's place, and "seen before" |
| `fake.py` | Fake fingerprints and a fake corridor walk, until the real parts exist |
| `demo.py` | Two walks of the same corridor |
| `familiar.py` | Week 6: full alert or vibration only on a familiar route |
| `familiar_demo.py` | Week 6: two walks with alerts, spoken lines counted, and the loop closure check |
| `messages.py` | Week 6: the proposed `landmark_match` message |
| `landmark_match.md` | Week 6: the proposal for Tasneem and Mariam |
| `samples/landmark_match.json` | A sample message |
| `bench.py` | Speed and size of the store |
