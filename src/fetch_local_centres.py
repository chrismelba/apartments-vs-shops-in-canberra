"""
Step 1b: Discover local shopping centre seed points from ACTMAPI.

The supermarket-based search (fetch_centres.py) misses smaller local centres
that have no supermarket — e.g. Evatt, Weetangera, Macquarie.

This script queries ACTMAPI for CZ4 (LOCAL CENTRE) zone polygons across
Canberra, computes their centroids, de-duplicates clustered polygons,
and appends any new centres to data/raw/centres.json.

Usage:
    python src/fetch_local_centres.py
"""

import json
import math
import os
import sys
import time

import requests
from shapely.geometry import shape
from tqdm import tqdm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import ACTMAPI_ZONE_URL, CANBERRA_BBOX, DEDUP_RADIUS_M

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def haversine_m(lat1, lng1, lat2, lng2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def fetch_cz4_polygons():
    """
    Query ACTMAPI for all CZ4 (Local Centre) zone polygons across Canberra.
    Returns a GeoJSON FeatureCollection.
    """
    bbox = CANBERRA_BBOX
    params = {
        "geometry":          json.dumps({
            "xmin": bbox["west"], "ymin": bbox["south"],
            "xmax": bbox["east"], "ymax": bbox["north"],
        }),
        "geometryType":      "esriGeometryEnvelope",
        "inSR":              "4326",
        "spatialRel":        "esriSpatialRelIntersects",
        "where":             "LAND_USE_ZONE_CODE_ID = 'CZ4'",
        "outFields":         "OBJECTID,LAND_USE_ZONE_CODE_ID,LAND_USE_POLICY_DESC,DIVISION_NAME,DISTRICT_NAME",
        "returnGeometry":    "true",
        "outSR":             "4326",
        "f":                 "geojson",
        "resultRecordCount": 5000,
    }
    resp = requests.get(ACTMAPI_ZONE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ACTMAPI error: {data['error']}")
    return data.get("features", [])


def features_to_centroids(features):
    """
    Convert CZ4 polygon features to centroid points with suburb labels.
    Returns list of {lat, lng, division_name}.
    """
    centroids = []
    for feat in features:
        geom = feat.get("geometry")
        props = feat.get("properties", {})
        if not geom:
            continue
        try:
            poly = shape(geom)
        except Exception:
            continue
        c = poly.centroid
        centroids.append({
            "lat":           c.y,
            "lng":           c.x,
            "division_name": props.get("DIVISION_NAME") or "Unknown",
        })
    return centroids


def deduplicate_centroids(centroids, threshold_m):
    """
    Greedy spatial de-duplication: if two centroids are within threshold_m,
    keep only the first. Returns de-duplicated list.
    """
    kept = []
    for c in centroids:
        too_close = any(
            haversine_m(c["lat"], c["lng"], k["lat"], k["lng"]) < threshold_m
            for k in kept
        )
        if not too_close:
            kept.append(c)
    return kept


def filter_against_existing(new_centroids, existing, threshold_m):
    """Remove centroids already covered by an existing centre."""
    result = []
    for c in new_centroids:
        covered = any(
            haversine_m(c["lat"], c["lng"], e["lat"], e["lng"]) < threshold_m
            for e in existing
        )
        if not covered:
            result.append(c)
    return result


def make_fake_place_id(lat, lng):
    """Generate a stable synthetic place_id for ACTMAPI-sourced centres."""
    return f"actmapi_cz4_{lat:.5f}_{lng:.5f}".replace("-", "m").replace(".", "d")


def main():
    centres_path = os.path.join(RAW_DIR, "centres.json")
    if not os.path.exists(centres_path):
        sys.exit("Run fetch_centres.py first (need existing centres.json).")

    with open(centres_path) as f:
        existing = json.load(f)

    print(f"Existing centres: {len(existing)}")
    print("Fetching CZ4 (Local Centre) polygons from ACTMAPI…")

    features = fetch_cz4_polygons()
    print(f"  CZ4 polygons fetched: {len(features)}")

    centroids = features_to_centroids(features)

    # De-duplicate closely spaced CZ4 polygons (same local centre, multiple parcels)
    deduped = deduplicate_centroids(centroids, threshold_m=DEDUP_RADIUS_M)
    print(f"  After de-duplication ({DEDUP_RADIUS_M}m): {len(deduped)} unique local centres")

    # Remove any already covered by the supermarket-seeded centres
    new_only = filter_against_existing(deduped, existing, threshold_m=DEDUP_RADIUS_M)
    print(f"  New centres not in existing list: {len(new_only)}")

    if not new_only:
        print("Nothing to add — all local centres already covered.")
        return

    # Build centre records using a synthetic place_id (ACTMAPI has no place_id)
    new_centres = [
        {
            "place_id": make_fake_place_id(c["lat"], c["lng"]),
            "name":     f"Local Centre — {c['division_name'].title()}",
            "lat":      c["lat"],
            "lng":      c["lng"],
            "address":  c["division_name"].title() + ", ACT",
            "source":   "actmapi_cz4",
        }
        for c in new_only
    ]

    for c in new_centres:
        print(f"  + {c['name']} ({c['lat']:.4f}, {c['lng']:.4f})")

    updated = existing + new_centres
    with open(centres_path, "w") as f:
        json.dump(updated, f, indent=2)

    print(f"\nSaved {len(updated)} centres total → {os.path.relpath(centres_path)}")
    print("Run fetch_shops.py and fetch_zoning.py to collect data for the new centres.")


if __name__ == "__main__":
    main()
