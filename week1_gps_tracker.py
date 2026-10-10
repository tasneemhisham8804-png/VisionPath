"""
VisionPath — Week 1 deliverable
Mariam Mohamed Mahmoud — Cloud, AI Pipeline, and Indoor Localization Engineer

Task (per VisionPath Role Guide, page 23, and Weekly Plan, page 15):
  "Get the GPS module streaming live location and build basic outdoor route
  tracking. Save the last reliable GPS fix at a building entrance as the
  anchor for indoor handoff."

Hardware:  u-blox NEO-M8N GPS module over UART
Library:   pynmea2 (NMEA 0183 sentence parsing), pyserial (UART access)
Done when: Route tracked outdoors. Entrance anchor stored.
Works with: Malak Emad (confirms module wiring/placement on her side)

------------------------------------------------------------------------
HOW TO SWITCH THIS FROM MOCK DATA TO YOUR REAL GPS MODULE
------------------------------------------------------------------------
This file runs in MOCK mode by default (no hardware needed) so you can
build/test the logic right now on your laptop. Once the NEO-M8N is wired
to the Pi's UART pins:

  1. Set USE_MOCK_GPS = False below.
  2. Set SERIAL_PORT to the right device:
       - Raspberry Pi:  usually "/dev/serial0" or "/dev/ttyAMA0"
       - Windows (USB GPS dongle): something like "COM5" (check Device Manager)
  3. Run: python3 week1_gps_tracker.py

Nothing else needs to change — GPSReader.read_fixes() yields the same
GPSFix objects either way, so route tracking and anchor logic don't care
which source fed them.
------------------------------------------------------------------------
"""

import json
import time
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Iterator

import pynmea2

# ---------------------------------------------------------------------------
# CONFIG — flip these once the real GPS module is wired up
# ---------------------------------------------------------------------------

USE_MOCK_GPS = True          # set False once NEO-M8N is connected
SERIAL_PORT = "/dev/serial0"  # Pi UART port (ignored while USE_MOCK_GPS=True)
SERIAL_BAUDRATE = 9600         # NEO-M8N default baud rate
ANCHOR_FILE = "entrance_anchor.json"  # where the entrance anchor gets saved


# ---------------------------------------------------------------------------
# 1. GPS FIX — a clean, hardware-agnostic shape for one GPS reading
# ---------------------------------------------------------------------------

@dataclass
class GPSFix:
    timestamp: str          # ISO 8601 UTC
    latitude: Optional[float]
    longitude: Optional[float]
    num_satellites: Optional[int]
    fix_quality: int        # 0 = no fix, 1 = GPS fix, 2 = DGPS fix (NMEA GGA standard)
    speed_knots: Optional[float] = None

    @property
    def has_fix(self) -> bool:
        return self.fix_quality > 0 and self.latitude is not None and self.longitude is not None


# ---------------------------------------------------------------------------
# 2. GPS READER — real NMEA/UART path
# ---------------------------------------------------------------------------

class SerialGPSReader:
    """Reads real NMEA sentences from the u-blox NEO-M8N over UART and
    parses them with pynmea2. Only GGA sentences are used here (position +
    fix quality + satellite count) since that's all Week 1 needs."""

    def __init__(self, port: str, baudrate: int):
        import serial  # imported here so mock mode never needs pyserial installed
        self.ser = serial.Serial(port, baudrate, timeout=1)

    def read_fixes(self) -> Iterator[GPSFix]:
        while True:
            try:
                line = self.ser.readline().decode("ascii", errors="replace").strip()
            except Exception as e:
                print(f"[GPS] Serial read error: {e}")
                continue
            if not line.startswith("$GPGGA") and not line.startswith("$GNGGA"):
                continue
            try:
                msg = pynmea2.parse(line)
            except pynmea2.ParseError:
                continue

            # Fields from pynmea2 can be None, '', or a real value depending on
            # what the module actually sent — convert explicitly and fall back
            # to "no fix" rather than letting a bad value crash the pipeline.
            try:
                fix_quality = int(msg.gps_qual) if msg.gps_qual not in (None, "") else 0
            except (ValueError, TypeError):
                fix_quality = 0

            try:
                num_satellites = int(msg.num_sats) if msg.num_sats not in (None, "") else None
            except (ValueError, TypeError):
                num_satellites = None

            has_fix = fix_quality > 0
            try:
                latitude = float(msg.latitude) if has_fix and msg.latitude not in (None, "") else None
                longitude = float(msg.longitude) if has_fix and msg.longitude not in (None, "") else None
            except (ValueError, TypeError):
                latitude, longitude = None, None

            yield GPSFix(
                timestamp=datetime.now(timezone.utc).isoformat(),
                latitude=latitude,
                longitude=longitude,
                num_satellites=num_satellites,
                fix_quality=fix_quality,
            )


# ---------------------------------------------------------------------------
# 3. GPS READER — mock path (no hardware required)
#    Simulates walking outdoors (good fixes), approaching a building
#    entrance, then losing signal indoors (fix drops to 0) — exactly the
#    scenario the anchor logic is meant to handle.
# ---------------------------------------------------------------------------

class MockGPSReader:
    """Generates realistic-looking GPSFix objects without any hardware,
    so the route-tracking and anchor logic can be built and tested today.
    Swap for SerialGPSReader once the NEO-M8N is wired up — nothing else
    in this file needs to change."""

    def __init__(self, start_lat=30.0444, start_lon=31.2357, num_outdoor_fixes=8):
        # Example start point: Cairo coordinates, just as a realistic default
        self.lat = start_lat
        self.lon = start_lon
        self.num_outdoor_fixes = num_outdoor_fixes

    def read_fixes(self) -> Iterator[GPSFix]:
        # Walking outdoors: good fixes, small random drift per step
        for i in range(self.num_outdoor_fixes):
            self.lat += random.uniform(-0.0003, 0.0003)
            self.lon += random.uniform(-0.0003, 0.0003)
            yield GPSFix(
                timestamp=datetime.now(timezone.utc).isoformat(),
                latitude=round(self.lat, 6),
                longitude=round(self.lon, 6),
                num_satellites=random.randint(6, 11),
                fix_quality=1,
                speed_knots=round(random.uniform(1.5, 3.0), 2),  # ~ walking pace
            )
            time.sleep(0.05)  # simulate real-time spacing, sped up for demo

        # Approaching the building entrance: fix quality degrades (fewer sats)
        for i in range(2):
            yield GPSFix(
                timestamp=datetime.now(timezone.utc).isoformat(),
                latitude=round(self.lat, 6),
                longitude=round(self.lon, 6),
                num_satellites=random.randint(3, 4),
                fix_quality=1,
            )
            time.sleep(0.05)

        # Walking indoors: GPS lost entirely (this is why the anchor matters)
        for i in range(3):
            yield GPSFix(
                timestamp=datetime.now(timezone.utc).isoformat(),
                latitude=None,
                longitude=None,
                num_satellites=0,
                fix_quality=0,
            )
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# 4. ROUTE TRACKER + ENTRANCE ANCHOR LOGIC
# ---------------------------------------------------------------------------

class RouteTracker:
    """Tracks the outdoor route as a sequence of good fixes, and keeps the
    LAST reliable fix available as the "entrance anchor" — the point where
    GPS was last trustworthy before being lost (e.g. walking indoors).
    This anchor is what Malak Mohammed's indoor localization picks up from,
    per the Week 5 integration task."""

    def __init__(self):
        self.route: list[GPSFix] = []
        self.last_good_fix: Optional[GPSFix] = None
        self.anchor_saved = False

    def process_fix(self, fix: GPSFix):
        if fix.has_fix:
            self.route.append(fix)
            self.last_good_fix = fix
            self.anchor_saved = False  # we're outdoors again; anchor no longer "final"
            print(f"[ROUTE] {fix.timestamp}  lat={fix.latitude}  lon={fix.longitude}  "
                  f"sats={fix.num_satellites}")
        else:
            # Signal lost. Save the last good fix as the entrance anchor,
            # exactly once per loss — not on every subsequent "no fix" reading.
            if self.last_good_fix is not None and not self.anchor_saved:
                self._save_anchor(self.last_good_fix)
                self.anchor_saved = True
            print(f"[ROUTE] {fix.timestamp}  NO FIX (likely indoors) — "
                  f"using stored anchor for indoor handoff")

    def _save_anchor(self, fix: GPSFix):
        anchor_data = asdict(fix)
        with open(ANCHOR_FILE, "w") as f:
            json.dump(anchor_data, f, indent=2)
        print(f"\n[ANCHOR SAVED] Building entrance anchor -> {ANCHOR_FILE}")
        print(f"               lat={fix.latitude}, lon={fix.longitude}, "
              f"at {fix.timestamp}\n")

    def route_summary(self):
        print("\n" + "=" * 60)
        print(f"Route summary: {len(self.route)} good outdoor fixes recorded")
        if self.route:
            print(f"  First fix: {self.route[0].latitude}, {self.route[0].longitude}")
            print(f"  Last fix:  {self.route[-1].latitude}, {self.route[-1].longitude}")
        if self.last_good_fix:
            print(f"Entrance anchor: {self.last_good_fix.latitude}, "
                  f"{self.last_good_fix.longitude}")
        print("=" * 60)


# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"Starting GPS tracking — mode: {'MOCK' if USE_MOCK_GPS else 'REAL (' + SERIAL_PORT + ')'}\n")

    reader = MockGPSReader() if USE_MOCK_GPS else SerialGPSReader(SERIAL_PORT, SERIAL_BAUDRATE)
    tracker = RouteTracker()

    for fix in reader.read_fixes():
        tracker.process_fix(fix)

    tracker.route_summary()


if __name__ == "__main__":
    main()
