"""
How fast is the landmark store, and how big does it get? Run it on the laptop now and on the Pi later.

    python -m landmarks.bench                      # the simple backend
    python -m landmarks.bench --backend chroma     # ChromaDB, after pip install chromadb
    python -m landmarks.bench --n 5000 --dim 512   # more fingerprints, bigger fingerprints

Prints the time to add one fingerprint, the time of one search (p50 and p95) and the
size on disk. The plan asks for about one fingerprint per second per landmark, so adding
must stay well under 1 s and a search well under the 300 ms alert budget.
"""

from __future__ import annotations

import argparse
import math
import random
import tempfile
import time
from pathlib import Path

from landmarks.store import open_store


def percentile(values, p):
    s = sorted(values)
    return s[max(1, math.ceil(p / 100 * len(s))) - 1]


def folder_mb(folder) -> float:
    return sum(f.stat().st_size for f in Path(folder).rglob("*") if f.is_file()) / 1e6


def bench(backend: str = "simple", n: int = 2000, dim: int = 128, queries: int = 100, seed: int = 0) -> dict:
    rng = random.Random(seed)

    def vec():
        return [rng.gauss(0, 1) for _ in range(dim)]

    with tempfile.TemporaryDirectory() as tmp:
        store = open_store(tmp, backend)
        try:
            t0 = time.perf_counter()
            for i in range(n):
                store.add(vec(), {"hazard": "pole", "place_x": rng.uniform(0, 500), "place_y": rng.uniform(0, 500),
                                  "session": i // 200, "t": float(i)})
            add_ms = (time.perf_counter() - t0) * 1000 / n
            times = []
            for _ in range(queries):
                q = vec()
                place = (rng.uniform(0, 500), rng.uniform(0, 500))
                t1 = time.perf_counter()
                store.nearest(q, k=5, place=place, radius_m=20)
                times.append((time.perf_counter() - t1) * 1000)
            size = folder_mb(tmp)
            count = store.count()
        finally:
            store.close()
    return {"backend": backend, "n": count, "dim": dim, "add_ms": add_ms,
            "query_p50_ms": percentile(times, 50), "query_p95_ms": percentile(times, 95), "size_mb": size}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Speed and size of the landmark store.")
    ap.add_argument("--backend", default="simple", choices=["simple", "chroma"])
    ap.add_argument("--n", type=int, default=2000, help="fingerprints to add (default 2000)")
    ap.add_argument("--dim", type=int, default=128, help="numbers per fingerprint (default 128)")
    ap.add_argument("--queries", type=int, default=100, help="searches to time (default 100)")
    args = ap.parse_args(argv)
    try:
        r = bench(args.backend, args.n, args.dim, args.queries)
    except ImportError:
        print("ChromaDB is not installed. Run: pip install chromadb")
        return 2
    print(f"Landmark store: {r['backend']} backend, {r['n']} fingerprints of {r['dim']} numbers")
    print(f"  add one fingerprint : {r['add_ms']:.2f} ms")
    print(f"  one search          : p50 {r['query_p50_ms']:.1f} ms, p95 {r['query_p95_ms']:.1f} ms")
    print(f"  size on disk        : {r['size_mb']:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
