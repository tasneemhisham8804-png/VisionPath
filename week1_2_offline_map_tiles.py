"""
VisionPath — Weeks 1-2 deliverable
Mariam Mohamed Mahmoud — Cloud, AI Pipeline and Indoor Localization

Task (per "Responsibilities by Team Member", Revision 5.0, 10 Oct 2026):
  "... Offline OpenStreetMap tiles ..."

------------------------------------------------------------------------
WHAT THIS IS FOR
------------------------------------------------------------------------
Map tiles are small square images (256x256px by convention) that together
tile the whole world at a given zoom level — this is the "slippy map"
system used by OpenStreetMap, Google Maps, etc. Each tile is addressed by
(zoom, x, y). To show a map offline (no internet, which is the whole
point of a "fallback"), you need the relevant tiles saved locally ahead
of time, so the device can look them up instead of fetching them live.

------------------------------------------------------------------------
IMPORTANT: OSM'S TILE USAGE POLICY
------------------------------------------------------------------------
OpenStreetMap's own tile servers (tile.openstreetmap.org) have a strict
usage policy that prohibits bulk/automated downloading:
https://operations.osmfoundation.org/policies/tiles/
Key rules: a clear User-Agent identifying your app, no more than ~2
requests/second, and tiles should be cached locally rather than re-fetched
on every use. Heavy bulk scripted downloading (which a test suite running
repeatedly could easily become) risks getting an IP blocked.

So this script is deliberately NOT something that runs automatically or
repeatedly. It's a ONE-TIME pre-caching tool: you run it yourself, once,
for the small test area you'll actually walk during demos/field tests
(e.g. around campus), at a conservative rate. For anything beyond a small
demo area, the standard approach is to download pre-rendered extracts from
a provider like Geofabrik (https://download.geofabrik.de/) and render your
own tiles offline — no live tile server involved at all. Worth raising
with Tasneem/the team which approach fits the demo's actual needs.

------------------------------------------------------------------------
HOW TO SWITCH FROM MOCK TO REAL DOWNLOADING
------------------------------------------------------------------------
This file defaults to MOCK mode — it generates placeholder tile images
locally (no network calls at all), so the caching logic (lookup, storage,
offline fallback) can be built and tested without touching a live server
or needing network access from this environment.

To actually populate a real cache for your demo area:
  1. Set USE_MOCK_DOWNLOADER = False below.
  2. Set a real, honest USER_AGENT identifying the project (required by
     OSM's policy — e.g. "VisionPath-Capstone/1.0 (contact: your email)").
  3. Set DEMO_AREA_BOUNDS to the small lat/lon box you'll actually need
     (e.g. around the test walking route), and a small zoom RANGE.
  4. Run this script ONCE, manually, not as part of automated tests.
------------------------------------------------------------------------
"""

import io
import math
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

USE_MOCK_DOWNLOADER = True   # set False only for a deliberate, manual, one-time real cache run
TILE_CACHE_DIR = "map_tiles_cache"
TILE_SIZE_PX = 256
USER_AGENT = "VisionPath-Capstone/1.0 (contact: your_email@example.com)"  # REQUIRED by OSM policy — fill in before real use
REQUEST_DELAY_S = 0.5  # stay well under OSM's ~2 req/s limit when downloading for real

# Example demo area + zoom range — replace with your actual test route
DEMO_AREA_BOUNDS = {
    "lat_min": 30.040, "lat_max": 30.048,
    "lon_min": 31.232, "lon_max": 31.240,
}
ZOOM_RANGE = range(16, 18)  # street-level zoom; wider range = many more tiles


# ---------------------------------------------------------------------------
# 1. SLIPPY MAP TILE MATH
#    Standard formulas: https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames
# ---------------------------------------------------------------------------

def latlon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Converts a GPS coordinate to the (x, y) tile indices at a given zoom."""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_to_latlon(x: int, y: int, zoom: int) -> Tuple[float, float]:
    """Converts tile (x, y) indices back to the GPS coordinate of its top-left corner."""
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lat, lon


def tiles_covering_area(bounds: dict, zoom: int):
    """Yields every (x, y, zoom) tile that covers the given lat/lon bounding box."""
    x_min, y_max = latlon_to_tile(bounds["lat_min"], bounds["lon_min"], zoom)
    x_max, y_min = latlon_to_tile(bounds["lat_max"], bounds["lon_max"], zoom)
    for x in range(min(x_min, x_max), max(x_min, x_max) + 1):
        for y in range(min(y_min, y_max), max(y_min, y_max) + 1):
            yield x, y, zoom


# ---------------------------------------------------------------------------
# 2. TILE DOWNLOADERS — mock (default) and real (for one deliberate run)
# ---------------------------------------------------------------------------

class MockTileDownloader:
    """Generates a placeholder tile image locally — no network call at all.
    Lets us build/test the caching logic (storage, lookup, offline fallback)
    without touching a live tile server or needing network access here."""

    def fetch_tile(self, x: int, y: int, zoom: int) -> bytes:
        img = Image.new("RGB", (TILE_SIZE_PX, TILE_SIZE_PX), color=(220, 230, 220))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, TILE_SIZE_PX - 1, TILE_SIZE_PX - 1], outline=(120, 140, 120))
        draw.text((10, 10), f"MOCK TILE\nz={zoom} x={x} y={y}", fill=(60, 80, 60))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class RealOSMTileDownloader:
    """
    Real downloader — only for a deliberate, manual, one-time pre-caching
    run over a small demo area, respecting OSM's tile usage policy (proper
    User-Agent, rate-limited, cached rather than re-fetched).
    """

    TILE_URL_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

    def __init__(self, user_agent: str, delay_s: float = REQUEST_DELAY_S):
        if "your_email@example.com" in user_agent:
            raise ValueError(
                "Set a real, honest USER_AGENT before downloading from OSM's live "
                "servers — this is required by their usage policy, not optional."
            )
        self.user_agent = user_agent
        self.delay_s = delay_s

    def fetch_tile(self, x: int, y: int, zoom: int) -> bytes:
        import urllib.request
        url = self.TILE_URL_TEMPLATE.format(z=zoom, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        time.sleep(self.delay_s)  # rate-limit ourselves, per OSM's policy
        return data


# ---------------------------------------------------------------------------
# 3. TILE CACHE — the actual Week 1-2 deliverable logic
# ---------------------------------------------------------------------------

@dataclass
class TileCacheStats:
    requested: int = 0
    cache_hits: int = 0
    downloaded: int = 0
    failed: int = 0


class TileCache:
    """
    Looks up map tiles from a local on-disk cache first; only reaches for
    the downloader (mock or real) on a cache miss. This is the core logic
    that lets the device show a map offline: if the tile was pre-cached,
    it's available with zero network dependency.
    """

    def __init__(self, cache_dir: str = TILE_CACHE_DIR, downloader=None):
        self.cache_dir = cache_dir
        self.downloader = downloader or MockTileDownloader()
        self.stats = TileCacheStats()
        os.makedirs(self.cache_dir, exist_ok=True)

    def _tile_path(self, x: int, y: int, zoom: int) -> str:
        tile_dir = os.path.join(self.cache_dir, str(zoom), str(x))
        return os.path.join(tile_dir, f"{y}.png")

    def get_tile(self, lat: float, lon: float, zoom: int, allow_fetch: bool = True) -> Optional[str]:
        """
        Returns the local file path for the tile covering (lat, lon) at the
        given zoom. If it's not cached and allow_fetch is True, tries to
        fetch it (via whichever downloader is configured) and cache it for
        next time. If it's not cached and allow_fetch is False (e.g. the
        device is actually offline right now), returns None — this is the
        "offline fallback" case the caller needs to handle (e.g. show a
        blank/placeholder area, or skip the visual map and rely on
        spoken directions instead).
        """
        x, y = latlon_to_tile(lat, lon, zoom)
        self.stats.requested += 1
        path = self._tile_path(x, y, zoom)

        if os.path.exists(path):
            self.stats.cache_hits += 1
            return path

        if not allow_fetch:
            return None  # offline, and we don't have this tile — caller must handle gracefully

        try:
            data = self.downloader.fetch_tile(x, y, zoom)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            self.stats.downloaded += 1
            return path
        except Exception as e:
            print(f"[TileCache] Failed to fetch tile z={zoom} x={x} y={y}: {e}")
            self.stats.failed += 1
            return None

    def precache_area(self, bounds: dict, zoom_range) -> TileCacheStats:
        """Pre-populates the cache for an entire area/zoom range in one go —
        this is the "run once before the field test" step."""
        for zoom in zoom_range:
            for x, y, z in tiles_covering_area(bounds, zoom):
                lat, lon = tile_to_latlon(x, y, z)
                self.get_tile(lat, lon, z, allow_fetch=True)
        return self.stats


# ---------------------------------------------------------------------------
# 4. DEMO / VERIFICATION
# ---------------------------------------------------------------------------

def run_demo():
    downloader = MockTileDownloader() if USE_MOCK_DOWNLOADER else RealOSMTileDownloader(USER_AGENT)
    cache = TileCache(downloader=downloader)

    print(f"Pre-caching demo area — mode: {'MOCK' if USE_MOCK_DOWNLOADER else 'REAL (OSM)'}")
    print(f"Area: {DEMO_AREA_BOUNDS}")
    print(f"Zoom levels: {list(ZOOM_RANGE)}\n")

    stats = cache.precache_area(DEMO_AREA_BOUNDS, ZOOM_RANGE)

    print("=" * 60)
    print("PRE-CACHE RESULTS")
    print("=" * 60)
    print(f"Tiles requested:  {stats.requested}")
    print(f"Already cached:   {stats.cache_hits}")
    print(f"Newly downloaded: {stats.downloaded}")
    print(f"Failed:           {stats.failed}")
    print("=" * 60)

    # Now simulate going offline and asking for a tile we already cached,
    # and one we never fetched — this proves the offline fallback behavior.
    print("\nSimulating OFFLINE lookups (allow_fetch=False):")
    test_lat = (DEMO_AREA_BOUNDS["lat_min"] + DEMO_AREA_BOUNDS["lat_max"]) / 2
    test_lon = (DEMO_AREA_BOUNDS["lon_min"] + DEMO_AREA_BOUNDS["lon_max"]) / 2

    cached_path = cache.get_tile(test_lat, test_lon, zoom=16, allow_fetch=False)
    print(f"  In-cache tile (center of demo area, z=16): {cached_path}")

    far_away_path = cache.get_tile(lat=0.0, lon=0.0, zoom=16, allow_fetch=False)
    print(f"  Never-cached tile (0,0 — nowhere near demo area): {far_away_path}")
    print("  (None is the expected, correct offline-fallback result here —")
    print("   the caller should handle this by e.g. relying on spoken")
    print("   directions instead of a visual map for that area.)")


if __name__ == "__main__":
    run_demo()
