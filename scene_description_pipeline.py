"""
Scene Description Hybrid Pipeline — v3
Deliverable #2: Smart Assistive Navigation System

Changes from v2:
  - Positions are now CLOCK-FACE ("2 o'clock") instead of left/right/ahead/behind,
    matching the convention used by established tools like BlindSquare and callmaps.
  - Distances are now PERCEPTUAL BUCKETS ("a step away") instead of raw meters,
    which is easier to process quickly than a precise decimal.

Everything else (validation, safety-layer coordination, filtering, fallback,
TTS, latency instrumentation) is unchanged from v2 — this file only touches
the "how do we phrase what we detected" layer, as agreed.

NOTE ON MOCK DATA: MOCK_DETECTIONS below is placeholder input standing in for
Talal's (#1) and Malak Mohammed's (#4) real model output, so this pipeline
can be built and tested before their models are ready. It does not represent
a claim about the user's real surroundings — swap it for their live output
once available; nothing else in this file needs to change to do that.

Run it directly:
    python3 scene_description_pipeline_v3.py
"""

import socket
import time
from typing import Optional, Callable


# ---------------------------------------------------------------------------
# 1. INPUT SCHEMA + VALIDATION
#    clock_position replaces position; distance_estimate_m is still the raw
#    input (kept in meters from the sensor/model — bucketing happens only at
#    the phrasing stage, so the raw number stays available for anything else
#    that needs precision, e.g. Tasneem's safety thresholds).
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "label": str,
    "confidence": float,
    "clock_position": int,   # 1-12, like a clock face, 12 = straight ahead
}

def validate_detection(d: dict) -> None:
    for field_name, expected_type in REQUIRED_FIELDS.items():
        if field_name not in d:
            raise SchemaError(f"Missing required field '{field_name}' in detection: {d}")
        if not isinstance(d[field_name], expected_type):
            raise SchemaError(
                f"Field '{field_name}' expected {expected_type.__name__}, "
                f"got {type(d[field_name]).__name__} in detection: {d}"
            )
    cp = d["clock_position"]
    if not (1 <= cp <= 12):
        raise SchemaError(f"'clock_position' must be 1-12, got: {cp}")
    dist = d.get("distance_estimate_m")
    if dist is not None and not isinstance(dist, (int, float)):
        raise SchemaError(f"'distance_estimate_m' must be numeric or None, got: {dist}")


class SchemaError(ValueError):
    pass


def validate_batch(detections: list) -> list:
    valid = []
    for d in detections:
        try:
            validate_detection(d)
            valid.append(d)
        except SchemaError as e:
            print(f"[SCHEMA WARNING] Dropping invalid detection: {e}")
    return valid


# ---------------------------------------------------------------------------
# 2. MOCK INPUT DATA
#    Placeholder standing in for Talal's / Malak Mohammed's real output —
#    see module docstring. clock_position: 12 = straight ahead, 3 = right,
#    6 = behind, 9 = left, etc.
# ---------------------------------------------------------------------------

MOCK_DETECTIONS = [
    {"label": "chair", "confidence": 0.91, "clock_position": 10, "distance_estimate_m": 1.2},
    {"label": "person", "confidence": 0.87, "clock_position": 12, "distance_estimate_m": 3.5},
    {"label": "trash can", "confidence": 0.42, "clock_position": 3, "distance_estimate_m": 2.0},
    {"label": "car", "confidence": 0.95, "clock_position": 12, "distance_estimate_m": 8.0},
    {"label": "pole", "confidence": 0.35, "clock_position": 12, "distance_estimate_m": 0.6},
]


# ---------------------------------------------------------------------------
# 3. DISTANCE BUCKETING
#    Perceptual buckets instead of raw meters. Thresholds are a starting
#    point — worth field-testing and tuning with Malak Emad's QA process.
# ---------------------------------------------------------------------------

DISTANCE_BUCKETS = [
    (0.5, "right next to you"),
    (1.5, "a step away"),
    (3.0, "a few steps ahead"),
    (5.0, "further off"),
]

def bucket_distance(distance_m: Optional[float]) -> str:
    if distance_m is None:
        return "nearby"
    for threshold, phrase in DISTANCE_BUCKETS:
        if distance_m <= threshold:
            return phrase
    return "further off"


# ---------------------------------------------------------------------------
# 4. DESCRIPTION-FILTERING LOGIC (natural-sounding output, NOT safety-critical)
# ---------------------------------------------------------------------------

def filter_for_description(detections, min_confidence=0.6, max_distance_m=5.0, max_items=3):
    filtered = [
        d for d in detections
        if d["confidence"] >= min_confidence
        and (d.get("distance_estimate_m") is None or d["distance_estimate_m"] <= max_distance_m)
    ]
    filtered.sort(key=lambda d: d.get("distance_estimate_m", float("inf")))
    return filtered[:max_items]


# ---------------------------------------------------------------------------
# 5. SAFETY-LAYER COORDINATION HOOK (unchanged logic from v2)
# ---------------------------------------------------------------------------

SAFETY_DISTANCE_THRESHOLD_M = 1.0
SAFETY_CONFIDENCE_FLOOR = 0.3

def check_safety_escalation(detections, notify_safety_layer: Callable[[dict], None]):
    for d in detections:
        dist = d.get("distance_estimate_m")
        if (
            d["confidence"] >= SAFETY_CONFIDENCE_FLOOR
            and dist is not None
            and dist <= SAFETY_DISTANCE_THRESHOLD_M
        ):
            notify_safety_layer(d)


def notify_safety_layer_stub(detection: dict) -> None:
    print(f"[SAFETY HANDOFF -> Tasneem's layer] Close hazard: {detection['label']} "
          f"at {detection.get('distance_estimate_m')}m (conf={detection['confidence']:.2f})")


# ---------------------------------------------------------------------------
# 6. DESCRIPTION GENERATION — now clock-face + bucketed distance
# ---------------------------------------------------------------------------

def generate_description(detections):
    if not detections:
        return "No obstacles detected."
    phrases = []
    for d in detections:
        dist_phrase = bucket_distance(d.get("distance_estimate_m"))
        phrases.append(f"a {d['label']} at {d['clock_position']} o'clock, {dist_phrase}")
    if len(phrases) == 1:
        return f"There's {phrases[0]}."
    return f"There's {', '.join(phrases[:-1])}, and {phrases[-1]}."


# ---------------------------------------------------------------------------
# 7. CONNECTIVITY CHECK
# ---------------------------------------------------------------------------

def is_online(host="8.8.8.8", port=53, timeout=0.5) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 8. ONLINE DESCRIPTION BACKEND (unchanged shape from v2)
# ---------------------------------------------------------------------------

class OnlineDescriptionClient:
    def __init__(self, api_key: Optional[str] = None, endpoint: Optional[str] = None):
        self.api_key = api_key
        self.endpoint = endpoint

    def is_configured(self) -> bool:
        return bool(self.api_key and self.endpoint)

    def generate(self, detections: list) -> str:
        if not self.is_configured():
            raise ConnectionError("Online description client not configured (no api_key/endpoint)")
        return self._call_api(detections)

    def _call_api(self, detections: list) -> str:
        raise NotImplementedError("Wire up the real LLM/description API call here")


# ---------------------------------------------------------------------------
# 9. TEXT-TO-SPEECH (unchanged from v2)
# ---------------------------------------------------------------------------

class TextToSpeech:
    def __init__(self):
        self.engine = None
        try:
            import pyttsx3
            self.engine = pyttsx3.init()
        except Exception as e:
            print(f"[TTS] No audio backend available ({e.__class__.__name__}) — "
                  f"falling back to text-only output.")

    def speak(self, text: str):
        if self.engine:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
                return
            except Exception as e:
                print(f"[TTS] Speech failed at runtime ({e.__class__.__name__}) — "
                      f"falling back to text-only output.")
        print(f"[SPOKEN OUTPUT] {text}")


# ---------------------------------------------------------------------------
# 10. FULL PIPELINE (unchanged structure from v2)
# ---------------------------------------------------------------------------

class SceneDescriber:
    def __init__(self, online_client: Optional[OnlineDescriptionClient] = None):
        self.online_client = online_client

    def describe(self, detections) -> str:
        if self.online_client and is_online():
            try:
                return self.online_client.generate(detections)
            except (ConnectionError, TimeoutError, NotImplementedError):
                pass
        return generate_description(detections)


def run_pipeline(raw_detections, tts: TextToSpeech, online_client=None):
    timings = {}

    t0 = time.perf_counter()
    validated = validate_batch(raw_detections)
    timings["validation_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    check_safety_escalation(validated, notify_safety_layer_stub)
    timings["safety_check_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for_narration = filter_for_description(validated)
    timings["filter_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    describer = SceneDescriber(online_client=online_client)
    description = describer.describe(for_narration)
    timings["describe_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    tts.speak(description)
    timings["tts_ms"] = (time.perf_counter() - t0) * 1000

    timings["total_ms"] = sum(timings.values())
    return description, timings


# ---------------------------------------------------------------------------
# 11. DEMO
# ---------------------------------------------------------------------------

def run_demo():
    tts = TextToSpeech()

    print("=" * 70)
    print("RUN 1 — offline path, clock-face + bucketed distance phrasing")
    print("=" * 70)
    description, timings = run_pipeline(MOCK_DETECTIONS, tts, online_client=None)
    print(f"\nFinal description: {description}")
    print("\nLatency breakdown:")
    for stage, ms in timings.items():
        print(f"  {stage:<18} {ms:.3f} ms")


if __name__ == "__main__":
    run_demo()
