"""VisionPath data flow contract.

Single source of truth for every message passed between pipeline stages:
camera -> detection -> fusion -> tracking/TTC -> risk scoring -> haptic/voice.

Standard library only (Python 3.10+), so it runs on the Pi, in CI and in the
replay harness with nothing to install.

Rules every producer follows (see contracts/README.md for the full spec):
  * t_mono_ns   = time.monotonic_ns() when the message is published (Pi clock).
  * t_origin_ns = t_mono_ns of the sensor reading this message derives from.
                  Copied unchanged down the pipeline, so
                  t_mono_ns - t_origin_ns is the latency up to that stage.
  * confidence  = 0.0..1.0 on every message.
  * bearing     = left / ahead / right on every message; null is allowed only
                  on message types where SPATIAL is False.
  * Units: metres, seconds, metres per second, degrees (negative = left).
"""
from __future__ import annotations

import json
import time
import typing
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import ClassVar, Optional

SCHEMA_VERSION = "1.0.0"

# |bearing_deg| <= this is "ahead". Tunable, but changing it is a minor bump.
AHEAD_HALF_WIDTH_DEG = 15.0


class ContractError(ValueError):
    """Raised when a message breaks the contract."""


# --------------------------------------------------------------------------
# Enums (adding a member = minor bump; removing or renaming = major bump)
# --------------------------------------------------------------------------
class Bearing(str, Enum):
    LEFT = "left"
    AHEAD = "ahead"
    RIGHT = "right"


class HazardClass(str, Enum):
    CURB = "curb"
    STEP = "step"
    POLE = "pole"
    PEDESTRIAN = "pedestrian"
    OVERHEAD = "overhead"          # branches, signs, hanging objects at head height
    MANHOLE = "manhole"            # open manhole
    POTHOLE = "pothole"
    MOTORBIKE = "motorbike"
    TUKTUK = "tuktuk"
    HANGING_CABLE = "hanging_cable"
    VEHICLE = "vehicle"
    DROP = "drop"                  # negative obstacle from the ground plane
    UNKNOWN_OBSTACLE = "unknown_obstacle"  # class agnostic corridor check / ultrasonic only


class SensorSource(str, Enum):
    CAMERA = "camera"
    DEPTH = "depth"
    ULTRASONIC = "ultrasonic"


class CaptureSource(str, Enum):
    WEBCAM = "webcam"
    OAKD = "oakd"
    REPLAY = "replay"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Lower is more urgent. Use this for sorting, never the string."""
        return ["critical", "high", "medium", "low", "info"].index(self.value)


class ReasonCode(str, Enum):
    TTC_BELOW_THRESHOLD = "ttc_below_threshold"
    CLOSE_RANGE = "close_range"
    CORRIDOR_BLOCKED = "corridor_blocked"
    DROP_AHEAD = "drop_ahead"
    OVERHEAD_HAZARD = "overhead_hazard"
    LOW_CONFIDENCE = "low_confidence"     # e.g. rain or fog lowered system confidence
    SYSTEM_FAULT = "system_fault"


class Channel(str, Enum):
    HAPTIC = "haptic"
    VOICE = "voice"


class Language(str, Enum):
    AR = "ar"
    EN = "en"


def bearing_from_deg(deg: float) -> Bearing:
    """Single shared rule for turning an angle into left / ahead / right."""
    if deg < -AHEAD_HALF_WIDTH_DEG:
        return Bearing.LEFT
    if deg > AHEAD_HALF_WIDTH_DEG:
        return Bearing.RIGHT
    return Bearing.AHEAD


def now_ns() -> int:
    """The one clock the whole pipeline uses."""
    return time.monotonic_ns()


# --------------------------------------------------------------------------
# Generic (de)serialisation helpers
# --------------------------------------------------------------------------
def _coerce(value, hint):
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Union:                       # Optional[X]
        if value is None:
            return None
        inner = [a for a in args if a is not type(None)][0]
        return _coerce(value, inner)
    if origin is list:
        if not isinstance(value, list):
            raise ContractError(f"expected list, got {type(value).__name__}")
        return [_coerce(v, args[0]) for v in value]
    if isinstance(hint, type) and issubclass(hint, Enum):
        try:
            return hint(value)
        except ValueError:
            allowed = [m.value for m in hint]
            raise ContractError(f"{value!r} is not one of {allowed}") from None
    if isinstance(hint, type) and is_dataclass(hint):
        if isinstance(value, hint):
            return value
        if not isinstance(value, dict):
            raise ContractError(f"expected object for {hint.__name__}")
        return _build(hint, value)
    if hint is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if hint in (int, float, str, bool):
        if not isinstance(value, hint) or (hint is int and isinstance(value, bool)):
            raise ContractError(f"expected {hint.__name__}, got {value!r}")
    return value


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ContractError(f"{cls.__name__}: unknown field(s) {sorted(unknown)}")
    try:
        return cls(**data)
    except TypeError as e:  # missing required field
        raise ContractError(f"{cls.__name__}: {e}") from None


def _to_plain(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: _to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


def _check_unit(name: str, value: Optional[float]) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ContractError(f"{name} must be within 0.0..1.0, got {value}")


def _check_non_negative(name: str, value: Optional[float]) -> None:
    if value is not None and value < 0:
        raise ContractError(f"{name} must be >= 0, got {value}")


class _Validated:
    """Coerces every field to its declared type, then runs validate()."""

    def __post_init__(self):
        hints = typing.get_type_hints(type(self))
        for f in fields(self):
            setattr(self, f.name, _coerce(getattr(self, f.name), hints[f.name]))
        self.validate()

    def validate(self) -> None:  # overridden where needed
        pass


# --------------------------------------------------------------------------
# Nested value types
# --------------------------------------------------------------------------
@dataclass
class BBox(_Validated):
    """Normalised image coordinates, 0..1, origin top left."""
    x1: float
    y1: float
    x2: float
    y2: float

    def validate(self):
        for n in ("x1", "y1", "x2", "y2"):
            _check_unit(f"bbox.{n}", getattr(self, n))
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ContractError("bbox needs x2 > x1 and y2 > y1")


@dataclass
class DetectedObject(_Validated):
    hazard: HazardClass
    score: float                       # raw model score 0..1
    bbox: BBox
    bearing: Bearing
    bearing_deg: float
    depth_m: Optional[float] = None    # null until the OAK D depth stream is used

    def validate(self):
        _check_unit("score", self.score)
        _check_non_negative("depth_m", self.depth_m)


@dataclass
class Reason(_Validated):
    """Why an alert fired. Spoken by the 'why?' voice command."""
    code: ReasonCode
    ttc_s: Optional[float] = None
    distance_m: Optional[float] = None
    detail: str = ""

    def validate(self):
        _check_non_negative("reason.ttc_s", self.ttc_s)
        _check_non_negative("reason.distance_m", self.distance_m)


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
_REGISTRY: dict[str, type] = {}


def _register(cls):
    _REGISTRY[cls.MSG_TYPE] = cls
    return cls


@dataclass(kw_only=True)
class Message(_Validated):
    """Envelope carried by every message."""
    MSG_TYPE: ClassVar[str] = ""
    SPATIAL: ClassVar[bool] = True     # if True, bearing must not be null

    seq: int                           # per source, starts at 0, +1 per message
    t_mono_ns: int
    t_origin_ns: int
    source: str                        # producing module, e.g. "detector"
    confidence: float
    bearing: Optional[Bearing]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        super().__post_init__()
        _check_unit("confidence", self.confidence)
        if self.seq < 0:
            raise ContractError("seq must be >= 0")
        if self.t_origin_ns > self.t_mono_ns:
            raise ContractError("t_origin_ns cannot be later than t_mono_ns")
        if self.SPATIAL and self.bearing is None:
            raise ContractError(f"{self.MSG_TYPE}: bearing is required")
        if not self.source:
            raise ContractError("source must not be empty")

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        d = {"msg_type": self.MSG_TYPE}
        d.update(_to_plain(self))
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    def latency_ms(self) -> float:
        """Time from the original sensor reading to this message."""
        return (self.t_mono_ns - self.t_origin_ns) / 1e6


@_register
@dataclass(kw_only=True)
class CameraFrame(Message):
    MSG_TYPE: ClassVar[str] = "camera_frame"
    SPATIAL: ClassVar[bool] = False
    frame_id: int
    capture: CaptureSource
    camera_id: str
    width: int
    height: int
    image_ref: str                     # shared memory key or file path, never pixels


@_register
@dataclass(kw_only=True)
class UltrasonicReading(Message):
    MSG_TYPE: ClassVar[str] = "ultrasonic_reading"
    sensor_id: str
    mount_deg: float                   # where the sensor points, negative = left
    distance_m: Optional[float]        # null = no echo within range

    def validate(self):
        _check_non_negative("distance_m", self.distance_m)


@_register
@dataclass(kw_only=True)
class DetectionSet(Message):
    """One per processed frame, even when objects is empty."""
    MSG_TYPE: ClassVar[str] = "detection_set"
    SPATIAL: ClassVar[bool] = False
    frame_id: int
    model_id: str                      # e.g. "yolo11n-hazard-int8-v3"
    objects: list[DetectedObject] = field(default_factory=list)


@_register
@dataclass(kw_only=True)
class FusedObstacle(Message):
    MSG_TYPE: ClassVar[str] = "fused_obstacle"
    obstacle_id: int                   # unique within one fusion cycle
    frame_id: Optional[int]            # null in ultrasonic only (camera down) mode
    hazard: HazardClass
    distance_m: float
    bearing_deg: float
    sources: list[SensorSource]

    def validate(self):
        _check_non_negative("distance_m", self.distance_m)
        if not self.sources:
            raise ContractError("sources must list at least one sensor")


@_register
@dataclass(kw_only=True)
class Track(Message):
    MSG_TYPE: ClassVar[str] = "track"
    track_id: int                      # persistent across frames
    hazard: HazardClass
    distance_m: float
    bearing_deg: float
    closing_speed_mps: float           # positive = getting closer
    ttc_s: Optional[float]             # null when not approaching
    ego_compensated: bool              # True once the user's own motion is removed
    age_frames: int

    def validate(self):
        _check_non_negative("distance_m", self.distance_m)
        _check_non_negative("ttc_s", self.ttc_s)
        if self.closing_speed_mps <= 0 and self.ttc_s is not None:
            raise ContractError("ttc_s must be null when the object is not approaching")


@_register
@dataclass(kw_only=True)
class RiskAlert(Message):
    MSG_TYPE: ClassVar[str] = "risk_alert"
    alert_id: int
    hazard: HazardClass
    priority: Priority
    reason: Reason
    track_id: Optional[int] = None     # null for system faults or ultrasonic only alerts


@_register
@dataclass(kw_only=True)
class OutputCommand(Message):
    """What the haptic or voice module must play. Pattern and line IDs belong
    to the haptic and voice modules; the contract only carries the ID."""
    MSG_TYPE: ClassVar[str] = "output_command"
    SPATIAL: ClassVar[bool] = False    # faults and low battery have no direction
    command_id: int
    channel: Channel
    priority: Priority
    interrupt: bool                    # may cut off lower priority output
    alert_id: Optional[int] = None
    haptic_pattern: Optional[str] = None
    intensity: Optional[float] = None
    voice_line: Optional[str] = None
    language: Optional[Language] = None

    def validate(self):
        _check_unit("intensity", self.intensity)
        if self.channel is Channel.HAPTIC:
            if not self.haptic_pattern or self.intensity is None:
                raise ContractError("haptic commands need haptic_pattern and intensity")
        if self.channel is Channel.VOICE:
            if not self.voice_line or self.language is None:
                raise ContractError("voice commands need voice_line and language")


# --------------------------------------------------------------------------
# Parsing entry points
# --------------------------------------------------------------------------
def _semver(v: str) -> tuple[int, int, int]:
    try:
        major, minor, patch = (int(p) for p in v.split("."))
    except (ValueError, AttributeError):
        raise ContractError(f"bad schema_version {v!r}") from None
    return major, minor, patch


def check_version(v: str) -> None:
    ours, theirs = _semver(SCHEMA_VERSION), _semver(v)
    if theirs[0] != ours[0]:
        raise ContractError(f"major version {theirs[0]} is incompatible with {ours[0]}")
    if theirs[1] > ours[1]:
        raise ContractError(
            f"message uses contract {v}, this code knows {SCHEMA_VERSION}: pull the latest contracts/"
        )
    # Older minor or any patch is fine: new fields are always optional.


def from_dict(data: dict) -> Message:
    if not isinstance(data, dict):
        raise ContractError("message must be a JSON object")
    data = dict(data)
    msg_type = data.pop("msg_type", None)
    cls = _REGISTRY.get(msg_type)
    if cls is None:
        raise ContractError(f"unknown msg_type {msg_type!r}")
    check_version(data.get("schema_version", ""))
    return _build(cls, data)


def from_json(text: str) -> Message:
    try:
        return from_dict(json.loads(text))
    except json.JSONDecodeError as e:
        raise ContractError(f"invalid JSON: {e}") from None


def message_types() -> dict[str, type]:
    return dict(_REGISTRY)
