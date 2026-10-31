"""
Read config/visionpath.toml (bus endpoints, latency target, camera rate and
the datalogger settings).

The bus endpoints live only in the config file, never in the code: if the
file or an endpoint is missing, this module raises ConfigError instead of
guessing.

    from datalogger.config import load_config, sub_endpoints, pub_endpoint
    cfg = load_config()              # $VISIONPATH_CONFIG, else config/visionpath.toml
    sub_endpoints(cfg)               # where modules connect to receive  [bus] sub_endpoint
    pub_endpoint(cfg)                # where modules connect to send     [bus] pub_endpoint
    datalogger_settings(cfg)         # [datalogger] with defaults, paths resolved
    latency_target_ms(cfg)           # [targets] latency_p95_ms, or None
    camera_hz(cfg)                   # [rates] camera_hz, or None

Standard library only (tomllib, Python 3.11+).
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:            # Python 3.10
    tomllib = None

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "visionpath.toml"
ENV_VAR = "VISIONPATH_CONFIG"

# Settings that are not bus endpoints may fall back to these values.
DATALOGGER_DEFAULTS = {
    "db_path": "data/visionpath.db",
    "blackbox_dir": "data/blackbox",
    "user_id": 1,
    "window_s": 30.0,
    "near_miss_ttc_s": 1.0,
    "cooldown_s": 10.0,
    "flush_interval_s": 1.0,
}
PATH_KEYS = ("db_path", "blackbox_dir")


class ConfigError(Exception):
    pass


def config_path(path=None) -> Path:
    """The file to read: the argument, else $VISIONPATH_CONFIG, else config/visionpath.toml."""
    return Path(path or os.environ.get(ENV_VAR) or DEFAULT_CONFIG)


def parse_config(text: str, source: str = "config") -> dict:
    if tomllib is None:
        raise ConfigError("reading the config needs Python 3.11 or newer (tomllib)")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{source}: {e}") from None


def load_config(path=None) -> dict:
    p = config_path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    return parse_config(p.read_text(encoding="utf-8"), str(p))


def _endpoints(cfg: dict, key: str) -> list[str]:
    value = (cfg.get("bus") or {}).get(key)
    if isinstance(value, str):
        value = [value]
    if not value or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f'the config file needs [bus] {key} = "tcp://host:port"')
    return list(value)


def sub_endpoints(cfg: dict) -> list[str]:
    """[bus] sub_endpoint (a string, or a list): where modules connect to receive messages."""
    return _endpoints(cfg, "sub_endpoint")


def pub_endpoint(cfg: dict) -> str:
    """[bus] pub_endpoint: where modules connect to send messages."""
    endpoints = _endpoints(cfg, "pub_endpoint")
    if len(endpoints) != 1:
        raise ConfigError("[bus] pub_endpoint must be a single endpoint")
    return endpoints[0]


def datalogger_settings(cfg: dict) -> dict:
    """[datalogger] values with defaults; relative paths resolved from the repository root."""
    settings = dict(DATALOGGER_DEFAULTS)
    settings.update(cfg.get("datalogger") or {})
    for key in PATH_KEYS:
        p = Path(settings[key])
        settings[key] = (p if p.is_absolute() else REPO_ROOT / p).resolve()
    return settings


def latency_target_ms(cfg: dict) -> float | None:
    value = (cfg.get("targets") or {}).get("latency_p95_ms")
    return float(value) if value is not None else None


def camera_hz(cfg: dict) -> float | None:
    value = (cfg.get("rates") or {}).get("camera_hz")
    return float(value) if value is not None else None
