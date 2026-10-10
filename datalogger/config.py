"""
Read config/visionpath.toml (the bus endpoint and the datalogger settings).

The bus endpoint lives only in the config file, never in the code: if the file
or the endpoint is missing, this module raises ConfigError instead of guessing.

    from datalogger.config import load_config, bus_endpoints, datalogger_settings
    cfg = load_config()                 # VISIONPATH_CONFIG, else config/visionpath.toml
    endpoints = bus_endpoints(cfg)      # list of "tcp://host:port" from [bus]
    settings = datalogger_settings(cfg)

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

# Settings that are not the bus endpoint may fall back to these values.
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


def load_config(path=None) -> dict:
    if tomllib is None:
        raise ConfigError("reading the config needs Python 3.11 or newer (tomllib)")
    p = config_path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    with open(p, "rb") as f:
        try:
            return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{p}: {e}") from None


def bus_endpoints(cfg: dict) -> list[str]:
    """[bus] endpoint = "..."  or  [bus] endpoints = ["...", ...]"""
    bus = cfg.get("bus") or {}
    value = bus.get("endpoints", bus.get("endpoint"))
    if isinstance(value, str):
        value = [value]
    if not value or not all(isinstance(v, str) and v for v in value):
        raise ConfigError("the config file needs [bus] endpoint = \"tcp://host:port\"")
    return list(value)


def datalogger_settings(cfg: dict) -> dict:
    """[datalogger] values with defaults; relative paths resolved from the repository root."""
    settings = dict(DATALOGGER_DEFAULTS)
    settings.update(cfg.get("datalogger") or {})
    for key in PATH_KEYS:
        p = Path(settings[key])
        settings[key] = (p if p.is_absolute() else REPO_ROOT / p).resolve()
    return settings
