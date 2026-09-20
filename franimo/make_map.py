"""Regenerate franimo/web/maps.js — the locator outlines used in the detail panel.

    python3 -m franimo.make_map --world ne_50m_admin_0_countries.geojson \
                                --departements departements-version-simplifiee.geojson

Sources: Natural Earth (public domain) for country outlines, and
https://github.com/gregoiredavid/france-geojson (IGN, Licence Ouverte) for the
French departements. The output is committed, so building the site needs no
network and no GIS dependency.

Every country is projected into its own box so it fills the small map, and each
carries its projection constants so the client can place a pin from lat/lon.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"

# Countries we can draw, and the window to draw them in. Clipping matters where
# a country's far-flung territories would otherwise shrink the mainland to a
# speck: France's DOM, Spain's Canaries, Portugal's Azores.
COUNTRIES: dict[str, tuple[float, float, float, float] | None] = {
    "FR": (-5.3, 41.2, 9.8, 51.3),
    "ES": (-9.5, 35.9, 3.4, 44.0),
    "PT": (-9.6, 36.9, -6.1, 42.2),
    "IT": (6.6, 35.4, 18.6, 47.2),
    "GR": (19.3, 34.7, 29.8, 41.8),
    "MA": (-13.5, 27.5, -0.8, 36.2),
    "JP": (127.0, 26.0, 146.2, 45.7),
    "BG": None,
    "ME": None,
    "RS": None,
    "AL": None,
    "GE": None,
}

# A flat tolerance is too coarse for a small country: at 0.02° it smoothed
# Portugal's coastline straight past Lisbon on the Tagus estuary, so the capital
# fell outside its own outline. Scale it to the country's own extent instead.
TOLERANCE_RATIO = 1 / 600
MIN_TOLERANCE = 0.004
MAX_TOLERANCE = 0.03
DEPT_TOLERANCE = 0.012


def tolerance_for(rings_) -> float:
    xs = [x for r in rings_ for x, _ in r]
    ys = [y for r in rings_ for _, y in r]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    return min(MAX_TOLERANCE, max(MIN_TOLERANCE, span * TOLERANCE_RATIO))


def rdp(points: list, eps: float) -> list:
    """Ramer-Douglas-Peucker, iterative so long coastlines can't blow the stack."""
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, idx = -1.0, lo
        for i in range(lo + 1, hi):
            px, py = points[i]
            d = (abs(dy * px - dx * py + bx * ay - by * ax) / norm if norm
                 else math.hypot(px - ax, py - ay))
            if d > worst:
                worst, idx = d, i
        if worst > eps:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [p for p, k in zip(points, keep) if k]


def rings(geom) -> list:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [poly[0] for poly in polys]


def inside(ring, box) -> bool:
    """Keep a ring if its centre is in the window.

    Testing that the *whole* ring fits would drop a mainland polygon that pokes
    a fraction past the edge (Morocco did exactly that) while still being the
    thing we want to draw. The centre test keeps mainlands and drops the
    far-flung islands the window exists to exclude.
    """
    if box is None:
        return True
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    cx = (min(lons) + max(lons)) / 2
    cy = (min(lats) + max(lats)) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def fit(rings_: list, width: float = 1000.0):
    """Project lon/lat into an SVG box, scaling x by cos(mid-latitude) so the
    country isn't stretched sideways."""
    xs = [x for r in rings_ for x, _ in r]
    ys = [y for r in rings_ for _, y in r]
    lat0 = (min(ys) + max(ys)) / 2
    kx = math.cos(math.radians(lat0))
    minx, maxx = min(xs) * kx, max(xs) * kx
    miny, maxy = min(ys), max(ys)
    span_x = maxx - minx or 1e-6
    height = width * (maxy - miny) / span_x

    def project(lon, lat):
        return ((lon * kx - minx) / span_x * width, (maxy - lat) / (maxy - miny) * height)

    proj = {"kx": round(kx, 6), "minx": round(minx, 6), "maxx": round(maxx, 6),
            "miny": round(miny, 6), "maxy": round(maxy, 6)}
    return project, round(width, 1), round(height, 1), proj


def path_of(points) -> str:
    return "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in points) + "Z"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", required=True, help="ne_50m_admin_0_countries.geojson")
    ap.add_argument("--departements", help="departements-version-simplifiee.geojson")
    args = ap.parse_args(argv)

    world = json.loads(Path(args.world).read_text(encoding="utf-8"))
    by_iso = {}
    for feat in world["features"]:
        p = feat["properties"]
        code = p.get("ISO_A2_EH") or p.get("ISO_A2")
        if code in COUNTRIES:
            by_iso[code] = feat

    missing = sorted(set(COUNTRIES) - set(by_iso))
    if missing:
        print(f"! not found in world file: {', '.join(missing)}", file=sys.stderr)

    maps: dict[str, dict] = {}
    for code, box in COUNTRIES.items():
        feat = by_iso.get(code)
        if not feat:
            continue
        raw = [[(p[0], p[1]) for p in r] for r in rings(feat["geometry"])
               if inside(r, box)]
        if not raw:
            continue
        eps = tolerance_for(raw)
        kept = [r for r in (rdp(r, eps) for r in raw) if len(r) >= 4]
        if not kept:
            continue
        project, w, h, proj = fit(kept)
        maps[code] = {
            "w": w, "h": h, "proj": proj,
            "path": " ".join(path_of([project(x, y) for x, y in r]) for r in kept),
        }

    if args.departements and "FR" in maps:
        data = json.loads(Path(args.departements).read_text(encoding="utf-8"))
        box = COUNTRIES["FR"]
        # Reuse France's projection so departements land on the country outline.
        pr = maps["FR"]["proj"]
        w, h = maps["FR"]["w"], maps["FR"]["h"]

        def project(lon, lat):
            return ((lon * pr["kx"] - pr["minx"]) / (pr["maxx"] - pr["minx"]) * w,
                    (pr["maxy"] - lat) / (pr["maxy"] - pr["miny"]) * h)

        depts: dict[str, list[str]] = {}
        for feat in data["features"]:
            for ring in rings(feat["geometry"]):
                if not inside(ring, box):
                    continue
                simple = rdp([(p[0], p[1]) for p in ring], DEPT_TOLERANCE)
                if len(simple) >= 4:
                    depts.setdefault(feat["properties"]["nom"], []).append(
                        path_of([project(x, y) for x, y in simple]))
        maps["FR"]["depts"] = {k: " ".join(v) for k, v in depts.items()}

    out = WEB / "maps.js"
    out.write_text("window.MAPS = " + json.dumps(maps, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    print(f"{len(maps)} countries -> {out} ({out.stat().st_size / 1024:.0f} KB)")
    for code, m in sorted(maps.items()):
        pts = m["path"].count("L") + m["path"].count("M")
        extra = f", {len(m['depts'])} departements" if "depts" in m else ""
        print(f"  {code}: {pts} points{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
