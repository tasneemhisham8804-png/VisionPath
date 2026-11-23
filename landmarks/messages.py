"""
The landmark_match message: "this place was seen on an earlier walk" (Week 6).

PROPOSED for the data contract. A new message type is a minor bump (1.x.0) and needs
sign-off from Tasneem (contract owner) and Mariam (who uses it). Until then it lives here,
is not registered in contracts/, and is not sent on the bus (the datalogger would count it
as an unknown message). See landmarks/landmark_match.md.

Who uses it: Mariam's localization, for loop closure. The landmark's stored place, minus
where the landmark is now relative to the user, is where the user is:

    user_x, user_y = where_is_the_user(match, heading_deg)

    msg = landmark_match(answer, track, seq=0, t_mono_ns=now_ns())   # answer = LandmarkMemory.remember(...)
    msg.to_json()                                                     # one line, like every contract message
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import ClassVar, Optional

from contracts import ContractError, HazardClass, Message, bearing_from_deg, check_version
from contracts.schema import _check_non_negative, _check_unit


@dataclass(kw_only=True)
class LandmarkMatch(Message):
    MSG_TYPE: ClassVar[str] = "landmark_match"
    track_id: int                       # the track that was recognised
    hazard: HazardClass                 # a fixed class: curb, step, pole, overhead, ...
    landmark_x_m: float                 # where the earlier walk stored it, in the localization's map
    landmark_y_m: float
    distance_m: float                   # where it is now, from the user (copied from the track)
    bearing_deg: float                  # negative = left of the user's heading
    similarity: float                   # how alike the two fingerprints are, 0..1
    matched_session: Optional[int] = None   # the earlier walk it was stored on

    def validate(self):
        _check_non_negative("distance_m", self.distance_m)
        _check_unit("similarity", self.similarity)


def landmark_match(answer: dict, track, seq: int, t_mono_ns: int, source: str = "landmarks") -> LandmarkMatch:
    """Build the message from a 'seen before' answer of LandmarkMemory.remember() and its track."""
    if not answer.get("seen_before") or not answer.get("matched_place"):
        raise ContractError("landmark_match is only sent for a landmark that was seen before")
    x, y = answer["matched_place"]
    return LandmarkMatch(seq=seq, t_mono_ns=t_mono_ns, t_origin_ns=track.t_origin_ns, source=source,
                         confidence=answer["similarity"], bearing=bearing_from_deg(track.bearing_deg),
                         track_id=track.track_id, hazard=answer["hazard"], landmark_x_m=x, landmark_y_m=y,
                         distance_m=track.distance_m, bearing_deg=track.bearing_deg,
                         similarity=answer["similarity"], matched_session=answer.get("matched_session"))


def parse(line_or_dict) -> LandmarkMatch:
    """Read a landmark_match line back (the contract's from_json does not know it yet)."""
    d = dict(json.loads(line_or_dict) if isinstance(line_or_dict, str) else line_or_dict)
    if d.pop("msg_type", None) != LandmarkMatch.MSG_TYPE:
        raise ContractError("not a landmark_match message")
    check_version(d.get("schema_version", ""))
    return LandmarkMatch(**d)


def where_is_the_user(match: LandmarkMatch, heading_deg: float) -> tuple[float, float]:
    """Loop closure: the user's place from the stored landmark place (0 = north = +y)."""
    rad = math.radians(heading_deg + match.bearing_deg)
    return (round(match.landmark_x_m - match.distance_m * math.sin(rad), 2),
            round(match.landmark_y_m - match.distance_m * math.cos(rad), 2))
