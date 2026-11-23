"""
Familiar route demo (Week 6): walk the same corridor twice.

    python -m landmarks.familiar_demo

Walk 1 is a new route: every alert is spoken. Walk 2 is the same route: the landmarks are
recognised, so their repeated alerts are vibration only, while urgent alerts, moving
objects and the new pothole are still spoken. Walk 2 also sends landmark_match messages
(for Mariam) and checks that they correct a localization that drifted 1.5 m.

Fingerprints (landmarks/fake.py) and risk alerts (FakeRisk below) are stand-ins until the
Hailo model and the risk module exist.
"""

from __future__ import annotations

import argparse
import math
import tempfile
from collections import Counter

from contracts import OutputCommand, Reason, RiskAlert
from landmarks.demo import NEW_ON_WALK_2
from landmarks.fake import LANDMARKS, FakeFingerprints, fake_walk
from landmarks.familiar import FamiliarAlerts
from landmarks.memory import STATIC_CLASSES, LandmarkMemory, Selector, landmark_place
from landmarks.messages import landmark_match, where_is_the_user
from landmarks.store import open_store

DRIFT_M = 1.5            # on walk 2 the localization thinks the user is 1.5 m further than they are

# Stand-in risk module: an alert each time a track comes closer than these distances.
# The last one only when the object is straight ahead (passing beside a pole is not a collision).
STAGES = [(6.0, "medium", "corridor_blocked"), (3.5, "high", "ttc_below_threshold"), (1.4, "critical", "close_range")]


class FakeRisk:
    def __init__(self):
        self.stage: dict[int, int] = {}
        self.alert_id = 0

    def alerts(self, track) -> list:
        out, done = [], self.stage.get(track.track_id, 0)
        while done < len(STAGES) and track.distance_m <= STAGES[done][0]:
            _, priority, code = STAGES[done]
            done += 1
            if code == "close_range" and track.bearing.value != "ahead":
                continue
            self.alert_id += 1
            out.append(RiskAlert(seq=self.alert_id, t_mono_ns=track.t_mono_ns, t_origin_ns=track.t_origin_ns,
                                 source="risk", confidence=0.9, bearing=track.bearing, alert_id=self.alert_id,
                                 hazard=track.hazard, priority=priority, track_id=track.track_id,
                                 reason=Reason(code=code, ttc_s=track.ttc_s, distance_m=track.distance_m)))
        self.stage[track.track_id] = done
        return out


def commands(alert) -> list:
    """Every alert becomes one vibration and one spoken line."""
    base = dict(t_mono_ns=alert.t_mono_ns, t_origin_ns=alert.t_origin_ns, source="output", confidence=0.9,
                bearing=None, priority=alert.priority, interrupt=alert.priority.value == "critical",
                alert_id=alert.alert_id)
    return [OutputCommand(seq=2 * alert.alert_id, command_id=2 * alert.alert_id, channel="haptic",
                          haptic_pattern=f"obstacle_{alert.bearing.value}", intensity=0.8, **base),
            OutputCommand(seq=2 * alert.alert_id + 1, command_id=2 * alert.alert_id + 1, channel="voice",
                          voice_line=f"{alert.hazard.value}_{alert.bearing.value}", language="ar", **base)]


def urgent(alert, familiar: FamiliarAlerts) -> bool:
    r = alert.reason
    return (alert.priority.value == "critical" or (r.distance_m is not None and r.distance_m < familiar.close_m)
            or (r.ttc_s is not None and r.ttc_s < familiar.min_ttc_s))


def walk(memory, familiar, session, landmarks, fingerprints, drift_m=0.0) -> dict:
    selector, risk = Selector(), FakeRisk()
    familiar.new_walk()
    alerts, voice, haptic = Counter(), Counter(), Counter()
    wrongly_quiet, matches = [], []
    for n, (track, user) in enumerate(fake_walk(landmarks)):
        familiar.see(track)
        if selector.wants(track):
            true_place = landmark_place(user, 0.0, track.distance_m, track.bearing_deg)
            believed = (user[0], user[1] + drift_m)                  # where the localization thinks the user is
            place = landmark_place(believed, 0.0, track.distance_m, track.bearing_deg)
            fp = fingerprints.of(track.hazard.value, true_place, sighting=session * 100_000 + n)
            answer = memory.remember(fp, track.hazard.value, place, session=session,
                                     t=round(track.t_mono_ns / 1e9, 2), track_id=track.track_id)
            familiar.note(answer)
            if answer["seen_before"]:
                msg = landmark_match(answer, track, seq=len(matches), t_mono_ns=track.t_mono_ns + 2_000_000)
                fixed = where_is_the_user(msg, 0.0)
                matches.append((msg, math.dist(believed, user), math.dist(fixed, user)))
        for alert in risk.alerts(track):
            familiar.decide(alert)
            hazard = alert.hazard.value
            alerts[hazard] += 1
            for cmd in commands(alert):
                if familiar.keep(cmd):
                    (voice if cmd.channel.value == "voice" else haptic)[hazard] += 1
                elif urgent(alert, familiar) or hazard not in STATIC_CLASSES:
                    wrongly_quiet.append(alert)
    return {"alerts": alerts, "voice": voice, "haptic": haptic, "wrongly_quiet": wrongly_quiet, "matches": matches}


def run(backend: str = "simple") -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store = open_store(tmp, backend)
        try:
            memory, familiar, fingerprints = LandmarkMemory(store), FamiliarAlerts(), FakeFingerprints()
            first = walk(memory, familiar, 1, LANDMARKS, fingerprints)
            second = walk(memory, familiar, 2, LANDMARKS + [NEW_ON_WALK_2], fingerprints, drift_m=DRIFT_M)
        finally:
            store.close()
    known = [h for h, _ in LANDMARKS]
    spoken1 = sum(first["voice"][h] for h in known)
    spoken2 = sum(second["voice"][h] for h in known)
    m = second["matches"]
    return {"walk1": first, "walk2": second, "known": known, "spoken1": spoken1, "spoken2": spoken2,
            "fewer_pct": round(100 * (spoken1 - spoken2) / spoken1) if spoken1 else 0,
            "drift_before_m": round(sum(b for _, b, _ in m) / len(m), 2) if m else None,
            "drift_after_m": round(sum(a for _, _, a in m) / len(m), 2) if m else None}


def passed(r: dict) -> bool:
    w1, w2 = r["walk1"], r["walk2"]
    new, moving = NEW_ON_WALK_2[0], "pedestrian"
    return (r["spoken2"] < r["spoken1"]
            and not w1["wrongly_quiet"] and not w2["wrongly_quiet"]
            and w2["voice"][new] == w2["alerts"][new] > 0
            and w2["voice"][moving] == w2["alerts"][moving]
            and all(w2["haptic"][h] == w2["alerts"][h] for h in w2["alerts"])         # every alert still vibrates
            and all(w1["voice"][h] == w1["alerts"][h] for h in w1["alerts"])          # a new route: all spoken
            and r["drift_after_m"] is not None and r["drift_after_m"] < 0.3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Walk the same corridor twice: fewer spoken alerts the second time.")
    ap.add_argument("--backend", default="simple", choices=["simple", "chroma"])
    r = run(ap.parse_args(argv).backend)
    w1, w2 = r["walk1"], r["walk2"]

    print("Familiar route demo: the same corridor walked twice (fake fingerprints and fake risk alerts)\n")
    print(f"  {'':<12}{'Walk 1 (new route)':>22}{'Walk 2 (familiar)':>22}")
    print(f"  {'object':<12}{'alerts':>10}{'spoken':>12}{'alerts':>10}{'spoken':>12}")
    notes = {NEW_ON_WALK_2[0]: "new on walk 2", "pedestrian": "moves"}
    for h in r["known"] + [NEW_ON_WALK_2[0], "pedestrian"]:
        a1, v1 = (w1["alerts"][h], w1["voice"][h]) if h in w1["alerts"] else ("-", "-")
        print(f"  {h:<12}{a1:>10}{v1:>12}{w2['alerts'][h]:>10}{w2['voice'][h]:>12}   {notes.get(h, '')}")
    print(f"\nSpoken alerts for the 4 familiar landmarks: {r['spoken1']} -> {r['spoken2']} "
          f"({r['fewer_pct']}% fewer). Every alert still vibrates.")
    print(f"Urgent, new or moving alerts made quieter: {len(w1['wrongly_quiet']) + len(w2['wrongly_quiet'])}")
    print(f"\nlandmark_match messages for Mariam on walk 2: {len(w2['matches'])}")
    if w2["matches"]:
        print(f"  example: {w2['matches'][0][0].to_json()}")
        print(f"Loop closure check: the localization was {r['drift_before_m']} m off; "
              f"from landmark_match it is {r['drift_after_m']:.2f} m off.")
    ok = passed(r)
    print("\nRESULT: PASS" if ok else "\nRESULT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
