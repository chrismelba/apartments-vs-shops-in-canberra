"""
Step 4: Compute per-centre shop quality and apartment density scores.

Reads all cached raw data from data/raw/ and produces:
    data/processed/analysis.csv

Each row = one shopping centre with:
  - Shop quality score (0–10) and its four components
  - Apartment density score (0–10) from ACT zoning
  - Raw counts (num_shops, total_reviews, etc.)
  - Top-3 rated shops (for map popups)
"""

import json
import math
import os
import sys

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, shape

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import (
    POPULATION_RADIUS_M,
    SCORE_WEIGHTS,
    VARIETY_MAX_POINTS,
    VARIETY_TYPES,
    ZONE_SCORES,
    ZONING_RADIUS_M,
)

RAW_DIR  = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
PROC_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")


# ---------------------------------------------------------------------------
# Shop quality components
# ---------------------------------------------------------------------------

def score_avg_rating(shops) -> float:
    """Mean rating of all shops with a rating, scaled 0–10."""
    rated = [s["rating"] for s in shops if s.get("rating")]
    if not rated:
        return 0.0
    return (sum(rated) / len(rated)) / 5.0 * 10.0


def score_review_density(shops) -> float:
    """
    log10(total_reviews / num_shops + 1) → normalized.
    High = shops that each attract many reviews (popular, busy centre).
    Capped at log10(1001) ≈ 3 → maps to 10.
    """
    if not shops:
        return 0.0
    total = sum(s.get("user_ratings_total", 0) or 0 for s in shops)
    per_shop = total / len(shops)
    raw = math.log10(per_shop + 1)
    return min(raw / math.log10(1001) * 10, 10.0)


def score_variety(shops) -> float:
    """
    Points for each category present in the centre (see VARIETY_TYPES in config).
    Normalized to 0–10.
    """
    all_types = set()
    for s in shops:
        all_types.update(s.get("types", []))

    points = 0
    awarded = set()
    for t, pts in VARIETY_TYPES.items():
        if t in all_types and t not in awarded:
            points += pts
            awarded.add(t)

    return min(points / VARIETY_MAX_POINTS * 10, 10.0)


def score_hours(shops) -> float:
    """Proportion of shops that have weekend opening hours."""
    with_hours = [s for s in shops if s.get("has_hours")]
    if not with_hours:
        return 5.0  # neutral if no data
    weekend = sum(1 for s in with_hours if s.get("weekend_open"))
    return weekend / len(with_hours) * 10.0


def shop_quality_score(components, weights=SCORE_WEIGHTS) -> float:
    return (
        components["avg_rating"]     * weights["avg_rating"]
        + components["review_density"] * weights["review_density"]
        + components["variety"]        * weights["variety"]
        + components["hours"]          * weights["hours"]
    )


# ---------------------------------------------------------------------------
# Zoning / apartment density
# ---------------------------------------------------------------------------

def zone_score_from_code(code: str) -> float:
    """Look up zone score; default to 0 for unknown zones."""
    # Strip trailing numbers from complex codes like "RZ1a" → "RZ1"
    for key in ZONE_SCORES:
        if code.startswith(key):
            return float(ZONE_SCORES[key])
    return 0.0


def apartment_density_score(geojson: dict, centre_lat: float, centre_lng: float) -> dict:
    """
    Residential-zone-quality score for the area within ZONING_RADIUS_M.

    Separates two concerns:
      - rz_avg: of the residential land nearby, how apartment-heavy is it? (RZ1=low … RZ5=high)
      - coverage: what fraction of the buffer is residential at all?

    density_score = (rz_avg / 5 × 10) × min(coverage / 0.4, 1.0)

    The coverage multiplier means commercial/civic precincts (low RZ coverage) are
    naturally discounted without needing special-casing. Suburban centres with
    ≥40% residential coverage get full credit based purely on zone quality.

    Returns a dict with:
        density_score        float 0–10
        zone_breakdown       dict  {zone_code: area_m2}
        rz_avg               float weighted avg RZ score (1–5 scale, RZ-only)
        residential_coverage float fraction of buffer that is RZ (0–1)
    """
    features = geojson.get("features", [])
    if not features:
        return {
            "density_score": 0.0,
            "zone_breakdown": {},
            "rz_avg": 0.0,
            "residential_coverage": 0.0,
        }

    centre_point = Point(centre_lng, centre_lat)
    deg_per_m = 1 / 111_320
    buffer_circle = centre_point.buffer(ZONING_RADIUS_M * deg_per_m)
    lat_scale = (111_320 ** 2) * math.cos(math.radians(centre_lat))

    total_area = 0.0
    rz_total_area = 0.0
    rz_weighted_area = 0.0
    zone_breakdown: dict[str, float] = {}

    for feat in features:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        try:
            poly = shape(geom_raw)
        except Exception:
            continue

        clipped = poly.intersection(buffer_circle)
        if clipped.is_empty:
            continue

        area_m2 = clipped.area * lat_scale
        code = feat.get("properties", {}).get("_zone_code", "UNKNOWN")
        z_score = zone_score_from_code(code)

        total_area += area_m2
        zone_breakdown[code] = zone_breakdown.get(code, 0.0) + area_m2

        if z_score > 0:  # residential zones only
            rz_total_area += area_m2
            rz_weighted_area += z_score * area_m2

    if total_area == 0:
        return {
            "density_score": 0.0,
            "zone_breakdown": zone_breakdown,
            "rz_avg": 0.0,
            "residential_coverage": 0.0,
        }

    rz_avg   = rz_weighted_area / rz_total_area if rz_total_area > 0 else 0.0
    coverage = rz_total_area / total_area

    # Full credit at ≥40% residential coverage; proportionally discounted below that
    coverage_weight = min(coverage / 0.4, 1.0)
    density_score = rz_avg / 5.0 * 10.0 * coverage_weight

    return {
        "density_score":        round(density_score, 2),
        "zone_breakdown":       {k: round(v) for k, v in zone_breakdown.items()},
        "rz_avg":               round(rz_avg, 3),
        "residential_coverage": round(coverage, 3),
    }


# ---------------------------------------------------------------------------
# ABS population density
# ---------------------------------------------------------------------------

def population_within_500m(sa1_geojson: dict, centre_lat: float, centre_lng: float) -> int:
    """
    Area-weighted estimate of 2021 Census residents within POPULATION_RADIUS_M.

    Each SA1 polygon that intersects the buffer contributes:
        population × (clipped_area / full_sa1_area)

    Returns total estimated residents (int).
    """
    features = sa1_geojson.get("features", []) if sa1_geojson else []
    if not features:
        return 0

    centre_point = Point(centre_lng, centre_lat)
    deg_per_m = 1 / 111_320
    buffer_circle = centre_point.buffer(POPULATION_RADIUS_M * deg_per_m)

    total_pop = 0.0
    for feat in features:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue
        try:
            poly = shape(geom_raw)
        except Exception:
            continue

        clipped = poly.intersection(buffer_circle)
        if clipped.is_empty:
            continue

        full_area = poly.area
        if full_area <= 0:
            continue

        pop = feat.get("properties", {}).get("population", 0) or 0
        total_pop += pop * (clipped.area / full_area)

    return int(round(total_pop))


# ---------------------------------------------------------------------------
# Top shops helper
# ---------------------------------------------------------------------------

def top_shops(shops, n=3):
    rated = [s for s in shops if s.get("rating")]
    rated.sort(key=lambda s: (-(s["rating"] or 0), -(s.get("user_ratings_total") or 0)))
    return [
        {"name": s["name"], "rating": s["rating"], "reviews": s.get("user_ratings_total", 0)}
        for s in rated[:n]
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    centres_path = os.path.join(RAW_DIR, "centres.json")
    if not os.path.exists(centres_path):
        sys.exit("Run fetch_centres.py first.")

    with open(centres_path) as f:
        centres = json.load(f)

    # Load ABS SA1 population data (optional — warns if missing)
    sa1_path = os.path.join(RAW_DIR, "abs_sa1_act.geojson")
    if os.path.exists(sa1_path):
        with open(sa1_path) as f:
            sa1_geojson = json.load(f)
        print(f"Loaded {len(sa1_geojson.get('features', []))} SA1 polygons for population scoring")
    else:
        sa1_geojson = None
        print("Warning: abs_sa1_act.geojson not found — run fetch_population.py for population scores")

    os.makedirs(PROC_DIR, exist_ok=True)
    rows = []

    for centre in centres:
        pid = centre["place_id"]

        # Load shops
        shops_path = os.path.join(RAW_DIR, f"shops_{pid}.json")
        if not os.path.exists(shops_path):
            print(f"  Skipping {centre['name']} — no shops data")
            continue
        with open(shops_path) as f:
            shops = json.load(f)

        # Load zoning
        zone_path = os.path.join(RAW_DIR, f"zoning_{pid}.json")
        if not os.path.exists(zone_path):
            print(f"  Skipping {centre['name']} — no zoning data")
            continue
        with open(zone_path) as f:
            zoning = json.load(f)

        # --- Shop quality ---
        components = {
            "avg_rating":     score_avg_rating(shops),
            "review_density": score_review_density(shops),
            "variety":        score_variety(shops),
            "hours":          score_hours(shops),
        }
        q_score = shop_quality_score(components)

        # --- Density ---
        density = apartment_density_score(zoning, centre["lat"], centre["lng"])

        # --- Population ---
        pop_500m = population_within_500m(sa1_geojson, centre["lat"], centre["lng"])

        # --- Top shops ---
        tops = top_shops(shops)

        rows.append({
            "place_id":            pid,
            "name":                centre["name"],
            "lat":                 centre["lat"],
            "lng":                 centre["lng"],
            "address":             centre.get("address", ""),
            "source":              centre.get("source", "google_places"),
            "num_shops":           len(shops),
            "total_reviews":       sum(s.get("user_ratings_total", 0) or 0 for s in shops),
            # Quality components
            "score_avg_rating":    round(components["avg_rating"], 2),
            "score_review_density":round(components["review_density"], 2),
            "score_variety":       round(components["variety"], 2),
            "score_hours":         round(components["hours"], 2),
            "shop_quality_score":  round(q_score, 2),
            # Zoning density
            "density_score":        density["density_score"],
            "rz_avg":               density["rz_avg"],
            "residential_coverage": density["residential_coverage"],
            "zone_breakdown":       json.dumps(density["zone_breakdown"]),
            # ABS population density (filled in below after percentile normalisation)
            "population_500m":      pop_500m if sa1_geojson else None,
            "population_score":     None,  # placeholder
            # Top shops (stored as JSON string for CSV compatibility)
            "top_shops":           json.dumps(tops),
        })

    # Percentile-normalise population_500m → population_score (0–10)
    pop_values = [r["population_500m"] for r in rows if r["population_500m"] is not None]
    if pop_values:
        pop_series = pd.Series(pop_values)
        p5  = pop_series.quantile(0.05)
        p95 = pop_series.quantile(0.95)
        span = p95 - p5 if p95 > p5 else 1.0
        for r in rows:
            if r["population_500m"] is not None:
                r["population_score"] = round(
                    min(max((r["population_500m"] - p5) / span, 0.0), 1.0) * 10, 2
                )

    df = pd.DataFrame(rows)
    out = os.path.join(PROC_DIR, "analysis.csv")
    df.to_csv(out, index=False)
    print(f"Saved {len(df)} centres → {os.path.relpath(out)}")
    print(df[["name", "num_shops", "shop_quality_score", "density_score", "population_score"]].to_string(index=False))


if __name__ == "__main__":
    main()
