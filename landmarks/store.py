"""
Where landmark fingerprints are kept, and how the closest one is found.

    store = open_store("data/landmarks", backend="simple")   # or "chroma" when chromadb is installed
    store.add(fingerprint, {"hazard": "pole", "place_x": 12.0, "place_y": 3.5, "session": 4, "t": 31.2})
    store.nearest(fingerprint, k=3, place=(12.0, 3.0), radius_m=5)   # [(id, similarity 0..1, info), ...]

A fingerprint (embedding) is a list of numbers that describes how an object looks:
two sightings of the same object give fingerprints close to each other.
Similarity is the cosine similarity: 1 = the same direction, 0 = unrelated.

Two backends with the same methods:
    simple   one SQLite file and a plain search. Standard library only; fast enough for
             thousands of landmarks.
    chroma   ChromaDB (the team plan's choice), when it is installed (pip install chromadb).
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from pathlib import Path

INFO_KEYS = ("hazard", "place_x", "place_y", "session", "t", "track_id")


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def within(info: dict, place, radius_m) -> bool:
    if place is None or radius_m is None:
        return True
    if info.get("place_x") is None or info.get("place_y") is None:
        return False
    return math.hypot(info["place_x"] - place[0], info["place_y"] - place[1]) <= radius_m


def clean(info: dict) -> dict:
    return {k: info[k] for k in INFO_KEYS if info.get(k) is not None}


class SimpleStore:
    """Fingerprints in one SQLite file, searched one by one."""

    backend = "simple"

    def __init__(self, folder):
        Path(folder).mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(Path(folder) / "landmarks.db")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS landmarks ("
            " id TEXT PRIMARY KEY, hazard TEXT, place_x REAL, place_y REAL,"
            " session INTEGER, t REAL, track_id INTEGER, fingerprint TEXT NOT NULL)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_landmarks_place ON landmarks (place_x, place_y)")
        self.conn.commit()

    def add(self, fingerprint, info: dict) -> str:
        lid = uuid.uuid4().hex
        i = clean(info)
        self.conn.execute(
            "INSERT INTO landmarks (id, hazard, place_x, place_y, session, t, track_id, fingerprint)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, i.get("hazard"), i.get("place_x"), i.get("place_y"), i.get("session"), i.get("t"),
             i.get("track_id"), json.dumps([round(float(x), 6) for x in fingerprint])))
        self.conn.commit()
        return lid

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM landmarks").fetchone()[0]

    def nearest(self, fingerprint, k: int = 3, place=None, radius_m=None) -> list:
        sql, args = "SELECT id, hazard, place_x, place_y, session, t, track_id, fingerprint FROM landmarks", []
        if place is not None and radius_m is not None:          # a square around the place, then the exact circle
            sql += " WHERE place_x BETWEEN ? AND ? AND place_y BETWEEN ? AND ?"
            args = [place[0] - radius_m, place[0] + radius_m, place[1] - radius_m, place[1] + radius_m]
        found = []
        for lid, *rest, fp in self.conn.execute(sql, args):
            info = clean(dict(zip(INFO_KEYS, rest)))
            if within(info, place, radius_m):
                found.append((lid, cosine(fingerprint, json.loads(fp)), info))
        found.sort(key=lambda r: r[1], reverse=True)
        return found[:k]

    def close(self) -> None:
        self.conn.close()


class ChromaStore:
    """The same methods on ChromaDB (cosine space)."""

    backend = "chroma"

    def __init__(self, folder, chromadb_module=None):
        chromadb = chromadb_module
        if chromadb is None:
            import chromadb                                     # pip install chromadb
        Path(folder).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(Path(folder) / "chroma"))
        self.col = self.client.get_or_create_collection("landmarks", metadata={"hnsw:space": "cosine"})

    def add(self, fingerprint, info: dict) -> str:
        lid = uuid.uuid4().hex
        self.col.add(ids=[lid], embeddings=[[float(x) for x in fingerprint]], metadatas=[clean(info) or {"hazard": "?"}])
        return lid

    def count(self) -> int:
        return self.col.count()

    def nearest(self, fingerprint, k: int = 3, place=None, radius_m=None) -> list:
        total = self.count()
        if total == 0:
            return []
        n = total if place is not None else min(k, total)       # place filter below, so ask for enough
        res = self.col.query(query_embeddings=[[float(x) for x in fingerprint]], n_results=n)
        found = []
        for lid, dist, info in zip(res["ids"][0], res["distances"][0], res["metadatas"][0]):
            info = clean(dict(info or {}))
            if within(info, place, radius_m):
                found.append((lid, 1.0 - float(dist), info))
        found.sort(key=lambda r: r[1], reverse=True)
        return found[:k]

    def close(self) -> None:
        pass


def open_store(folder, backend: str = "simple"):
    if backend == "simple":
        return SimpleStore(folder)
    if backend == "chroma":
        return ChromaStore(folder)
    raise ValueError(f"unknown backend {backend!r}: use simple or chroma")
