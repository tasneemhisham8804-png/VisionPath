"""
Wellbeing check (Week 7): "the user has not moved for a while: are they okay?"

The team's decision: when the user has been still for interval_min (15 min, the caregiver can
change it), the device asks "are you okay?". The user answers only by voice ("I'm okay") or a
short press of a button. Walking away is not an answer. With no answer after every attempt,
the caregiver's app is told, with the last trusted place.

    w = Wellbeing(Settings.from_config(cfg))
    for line in bus_lines:                 # JSON lines, see wellbeing/inputs.md
        for action in w.feed(line):        # Action(kind, ...): ask, thanks, notify_caregiver, ...
            ...
    w.tick(now_ns)                         # live: once a second, so time passes with no message

Rules, in order:
  * A fall always wins: the check stops and the fall flow takes over (Malak Ibrahim).
  * A long press is the emergency button: the check stops and the emergency flow takes over.
  * A danger alert (critical or high) while waiting plays first, then the question is asked
    again and the wait starts again: the question is lower priority than danger.
  * No new check during the caregiver's quiet hours, or when the motion signal is lost.
  * After "I'm okay" the timer starts again: the next check comes after another full interval.
  * After an escalation, no new check until the user moves or answers. A late "I'm okay" is
    passed to the caregiver.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

# what the output module plays (pattern and line IDs belong to Baraa and Mostafa)
QUESTION = {"voice_line": "wellbeing_question", "haptic_pattern": "wellbeing_question", "priority": "low"}
THANKS = {"voice_line": "wellbeing_thanks", "priority": "low"}
DANGER = ("critical", "high")


@dataclass
class Settings:
    interval_min: float = 15.0        # still this long -> ask (the caregiver can change it)
    answer_wait_s: float = 30.0       # wait this long for an answer, per attempt
    attempts: int = 2                 # ask this many times before telling the caregiver (database: 1 or 2)
    quiet_hours: str = ""             # e.g. "23:00-07:00": no new check in this time. Blank = none
    motion_timeout_s: float = 10.0    # no motion message for this long = signal lost

    def __post_init__(self):
        if not 1 <= int(self.attempts) <= 2:
            raise ValueError("attempts must be 1 or 2 (wellbeing_checks.attempts in the database)")
        if self.interval_min <= 0 or self.answer_wait_s <= 0:
            raise ValueError("interval_min and answer_wait_s must be more than 0")
        quiet_window(self.quiet_hours)                     # fails early on a bad value

    @classmethod
    def from_config(cls, cfg: dict | None, **override) -> "Settings":
        s = dict((cfg or {}).get("wellbeing") or {})
        s.update(override)
        known = {k: s[k] for k in ("interval_min", "answer_wait_s", "attempts", "quiet_hours", "motion_timeout_s")
                 if k in s}
        return cls(**known)


def quiet_window(text: str):
    """'23:00-07:00' -> (time(23, 0), time(7, 0)). Blank -> None."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        a, b = (time.fromisoformat(p.strip()) for p in text.split("-"))
    except ValueError:
        raise ValueError(f"quiet_hours {text!r} must look like 23:00-07:00") from None
    return a, b


def in_window(now: time, window) -> bool:
    if window is None:
        return False
    a, b = window
    return a <= now < b if a <= b else (now >= a or now < b)         # a window can cross midnight


@dataclass
class Action:
    t_ns: int
    kind: str                 # ask | thanks | notify_caregiver | tell_caregiver_okay | stop | signal_lost
    detail: dict = field(default_factory=dict)


@dataclass
class Check:
    """One wellbeing check. The fields follow the wellbeing_checks table."""
    stationary_since_ns: int
    started_ns: int
    interval_min: float
    place: dict | None = None
    attempts: int = 1
    questions: int = 1                 # times the question was played (a danger alert makes it play again)
    outcome: str | None = None         # okay_voice | okay_button | escalated | overridden_by_fall
    responded_ns: int | None = None
    ended_ns: int | None = None
    notification_status: str = "not_needed"     # queued when the caregiver must be told
    ended_by: str = ""                 # what ended it, in words


class Wellbeing:
    def __init__(self, settings: Settings | None = None, clock=None):
        """clock(t_ns) -> local time of day, for the quiet hours. Default: the computer's clock now."""
        self.s = settings or Settings()
        self.clock = clock or (lambda t_ns: datetime.now().time())
        self.quiet = quiet_window(self.s.quiet_hours)
        self.still_since: int | None = None
        self.last_motion_ns: int | None = None
        self.place: dict | None = None
        self.signal_lost = False
        self.wait_for_move = False         # after an escalation: no new check until the user moves
        self.check: Check | None = None    # the open check
        self.deadline_ns: int | None = None
        self.checks: list[Check] = []      # every check, open or closed
        self.actions: list[Action] = []

    # -- helpers ----------------------------------------------------------------------------
    def _act(self, t: int, kind: str, **detail) -> Action:
        a = Action(t, kind, detail)
        self.actions.append(a)
        return a

    def _ask(self, t: int, why: str) -> Action:
        self.deadline_ns = t + int(self.s.answer_wait_s * 1e9)
        return self._act(t, "ask", attempt=self.check.attempts, why=why, **QUESTION)

    def _close(self, t: int, outcome: str, ended_by: str) -> None:
        c = self.check
        c.outcome, c.ended_ns, c.ended_by = outcome, t, ended_by
        self.check, self.deadline_ns = None, None

    # -- time passing ---------------------------------------------------------------------
    def tick(self, now: int) -> list[Action]:
        out = []
        if self.check is not None:
            if now >= self.deadline_ns:
                if self.check.attempts < self.s.attempts:
                    self.check.attempts += 1
                    self.check.questions += 1
                    out.append(self._ask(now, "no answer yet"))
                else:
                    c = self.check
                    c.notification_status = "queued"
                    still_min = round((now - c.stationary_since_ns) / 60e9, 1)
                    out.append(self._act(now, "notify_caregiver", place=self.place, still_min=still_min,
                                         attempts=c.attempts))
                    self._close(now, "escalated", "no answer")
                    self.wait_for_move = True
            return out
        if self.last_motion_ns is not None and now - self.last_motion_ns > self.s.motion_timeout_s * 1e9:
            if not self.signal_lost:
                self.signal_lost = True
                out.append(self._act(now, "signal_lost", note="no motion signal: no new check until it is back"))
            return out
        if (self.still_since is not None and not self.wait_for_move and not self.signal_lost
                and now - self.still_since >= self.s.interval_min * 60e9
                and not in_window(self.clock(now), self.quiet)):
            self.check = Check(self.still_since, now, self.s.interval_min, place=self.place)
            self.checks.append(self.check)
            out.append(self._ask(now, f"still for {self.s.interval_min:g} min"))
        return out

    # -- messages -------------------------------------------------------------------------
    def feed(self, line: str) -> list[Action]:
        try:
            d = json.loads(line)
            t = int(d["t_mono_ns"])
        except (ValueError, KeyError, TypeError):
            return []
        kind = d.get("msg_type")
        if kind == "motion_state":                  # first, so a returning signal or a step counts at once
            return self._motion(t, d) + self.tick(t)
        out = self.tick(t)
        if kind == "voice_intent" and d.get("intent") == "im_okay":
            out += self._okay(t, "okay_voice", "said I'm okay")
        elif kind == "button_press":
            if d.get("press") == "long":
                out += self._emergency_button(t)
            else:
                out += self._okay(t, "okay_button", "short press")
        elif kind == "fall_detected":
            out += self._fall(t)
        elif kind == "risk_alert" and self.check is not None and d.get("priority") in DANGER:
            self.check.questions += 1
            out.append(self._ask(t, f"asked again after a {d.get('priority')} alert"))
        return out

    def _motion(self, t: int, d: dict) -> list[Action]:
        self.last_motion_ns = t
        self.signal_lost = False
        if d.get("place") is not None:
            self.place = d["place"]
        if d.get("moving"):
            self.still_since = None                 # moving is not an answer to an open check
            self.wait_for_move = False
        elif self.still_since is None:
            self.still_since = int(d.get("still_since_ns") or t)
        return []

    def _okay(self, t: int, outcome: str, how: str) -> list[Action]:
        if self.check is not None:
            self.check.responded_ns = t
            self._close(t, outcome, how)
            self.still_since = t                    # the next check after another full interval
            return [self._act(t, "thanks", **THANKS)]
        last = self.checks[-1] if self.checks else None
        if last is not None and last.outcome == "escalated" and last.responded_ns is None:
            last.responded_ns = t                   # late answer: the caregiver hears it too
            self.wait_for_move = False
            self.still_since = t
            return [self._act(t, "tell_caregiver_okay", how=how), self._act(t, "thanks", **THANKS)]
        return []                                   # "I'm okay" with no question: nothing to do

    def _fall(self, t: int) -> list[Action]:
        self.still_since = t                        # the fall flow handles the next minutes
        if self.check is None:
            return []
        self._close(t, "overridden_by_fall", "fall")
        return [self._act(t, "stop", why="fall: the fall flow takes over")]

    def _emergency_button(self, t: int) -> list[Action]:
        if self.check is None:
            return []
        self.check.responded_ns = t
        self._close(t, "escalated", "emergency button")        # the emergency flow tells the caregiver
        self.wait_for_move = True
        return [self._act(t, "stop", why="emergency button: the emergency flow takes over")]


# ---------- saving to the on-device database ----------

def save(conn, check: Check, session_id: int, user_id: int, to_utc) -> int:
    """Write one check to wellbeing_checks (and an emergency_events row when the caregiver is told).
    to_utc(t_ns) -> the database's UTC text, e.g. lambda t: mono_to_utc(started_at, mono_s, t)."""
    utc = lambda t: None if t is None else to_utc(t)
    lat = (check.place or {}).get("lat")
    lon = (check.place or {}).get("lon")
    event_id = None
    if check.notification_status == "queued":
        event_id = conn.execute(
            "INSERT INTO emergency_events (user_id, session_id, ts, trigger_type, lat, lon, status) "
            "VALUES (?, ?, ?, 'wellbeing', ?, ?, 'pending')",
            (user_id, session_id, utc(check.ended_ns), lat, lon)).lastrowid
    cur = conn.execute(
        """INSERT INTO wellbeing_checks (user_id, session_id, stationary_since, interval_min, started_at, lat, lon,
                                         attempts, outcome, responded_at, emergency_event_id, notification_status,
                                         ended_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, session_id, utc(check.stationary_since_ns), int(round(check.interval_min)), utc(check.started_ns),
         lat, lon, check.attempts, check.outcome, utc(check.responded_ns), event_id, check.notification_status,
         utc(check.ended_ns)))
    conn.commit()
    return cur.lastrowid


def clock_from(start: str, t0_ns: int):
    """A clock for replays: local time 'HH:MM' at t0_ns, then time passes with t_ns."""
    base = datetime.combine(datetime(2026, 1, 1), time.fromisoformat(start))
    return lambda t_ns: (base + timedelta(seconds=(t_ns - t0_ns) / 1e9)).time()
