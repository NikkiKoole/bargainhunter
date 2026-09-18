"""Regenerate franimo/web/france.js — the SVG locator map used in the detail panel.

    python3 -m franimo.make_map path/to/departements-version-simplifiee.geojson

Source: https://github.com/gregoiredavid/france-geojson (IGN data, Licence Ouverte).
The output is committed, so building the site needs no network and no GIS deps.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"

# Metropolitan France only; the overseas departments would wreck the framing.
BBOX = (-5.3, 41.2, 9.8, 51.3)
TOLERANCE = 0.012          # degrees, ~1.3km: keeps the coastline readable
LAT0 = 46.5                # x is scaled by cos(lat) so the country isn't stretched


def rdp(points: list, eps: float) -> list:
    """Ramer-Douglas-Peucker: drop points that don't change the outline."""
    if len(points) < 3:
        return points
    ax, ay = points[0]
    bx, by = points[-1]
    dx, dy = bx - ax, by - ay
    norm = math.hypot(dx, dy)
    worst, idx = -1.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = (abs(dy * px - dx * py + bx * ay - by * ax) / norm if norm
             else math.hypot(px - ax, py - ay))
        if d > worst:
            worst, idx = d, i
    if worst <= eps:
        return [points[0], points[-1]]
    return rdp(points[:idx + 1], eps)[:-1] + rdp(points[idx:], eps)


def rings(geom) -> list:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [poly[0] for poly in polys]        # outer ring only; holes don't read at this size


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    data = json.loads(Path(argv[0]).read_text(encoding="utf-8"))

    kept = []
    for feat in data["features"]:
        for ring in rings(feat["geometry"]):
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            if not (BBOX[0] <= min(lons) and max(lons) <= BBOX[2]
                    and BBOX[1] <= min(lats) and max(lats) <= BBOX[3]):
                continue
            simple = rdp([(p[0], p[1]) for p in ring], TOLERANCE)
            if len(simple) >= 4:
                kept.append((feat["properties"]["nom"], simple))

    xs = [x for _, r in kept for x, _ in r]
    ys = [y for _, r in kept for _, y in r]
    kx = math.cos(math.radians(LAT0))
    minx, maxx = min(xs) * kx, max(xs) * kx
    miny, maxy = min(ys), max(ys)
    w, h = 1000.0, 1000.0 * (maxy - miny) / (maxx - minx)

    def project(lon, lat):
        return ((lon * kx - minx) / (maxx - minx) * w,
                (maxy - lat) / (maxy - miny) * h)

    depts: dict[str, list[str]] = {}
    for name, ring in kept:
        pts = [project(x, y) for x, y in ring]
        d = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z"
        depts.setdefault(name, []).append(d)

    payload = {
        "w": round(w, 1), "h": round(h, 1),
        "proj": {"kx": round(kx, 6), "minx": round(minx, 6), "maxx": round(maxx, 6),
                 "miny": round(miny, 6), "maxy": round(maxy, 6)},
        "depts": {name: " ".join(paths) for name, paths in depts.items()},
    }
    out = WEB / "france.js"
    out.write_text("window.FRANCE = " + json.dumps(payload, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    pts = sum(p.count("L") + 1 for paths in depts.values() for p in paths)
    print(f"{len(depts)} departements, {pts} points -> {out} "
          f"({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
