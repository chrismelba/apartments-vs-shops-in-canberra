"""
Step 3: Fetch ACT Territory Plan zoning within ZONING_RADIUS_M of each centre.

Uses the ACTMAPI ArcGIS REST API (free, no key required) to query the Territory
Plan Land Use Zone layer.  Results are cached to data/raw/zoning_{place_id}.json.

Usage:
    python src/fetch_zoning.py            # all centres
    python src/fetch_zoning.py --limit 5  # first 5 centres only (for testing)
"""

import argparse
import json
import math
import os
import sys
import time

import requests
from tqdm import tqdm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import ACTMAPI_ZONE_URL, ZONING_RADIUS_M

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def metres_to_degrees_lat(metres):
    """Approximate: 1 degree latitude ≈ 111,320 m."""
    return metres / 111_320


def metres_to_degrees_lng(metres, lat):
    """Approximate: 1 degree longitude ≈ 111,320 * cos(lat) m."""
    return metres / (111_320 * math.cos(math.radians(lat)))


def build_bbox(lat, lng, radius_m):
    dlat = metres_to_degrees_lat(radius_m)
    dlng = metres_to_degrees_lng(radius_m, lat)
    return {
        "xmin": lng - dlng,
        "ymin": lat - dlat,
        "xmax": lng + dlng,
        "ymax": lat + dlat,
    }


def query_zoning(lat, lng, radius_m):
    """
    Query ACTMAPI for all zoning polygons that intersect a bounding box
    centred on (lat, lng) with the given radius.

    Returns a GeoJSON FeatureCollection dict (may have many features).
    """
    bbox = build_bbox(lat, lng, radius_m)

    all_features = []
    offset = 0
    page_size = 1000

    while True:
        params = {
            "geometry":           json.dumps(bbox),
            "geometryType":       "esriGeometryEnvelope",
            "inSR":               "4326",
            "spatialRel":         "esriSpatialRelIntersects",
            "outFields":          "*",
            "returnGeometry":     "true",
            "outSR":              "4326",
            "f":                  "geojson",
            "resultOffset":       offset,
            "resultRecordCount":  page_size,
        }

        try:
            resp = requests.get(ACTMAPI_ZONE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"ACTMAPI request failed: {e}") from e

        data = resp.json()

        # Handle ArcGIS error envelope
        if "error" in data:
            raise RuntimeError(f"ACTMAPI error: {data['error']}")

        features = data.get("features", [])
        all_features.extend(features)

        # If fewer than page_size returned, we've got everything
        if len(features) < page_size:
            break
        offset += page_size

    return {
        "type": "FeatureCollection",
        "features": all_features,
        "centre": {"lat": lat, "lng": lng, "radius_m": radius_m},
    }


def extract_zone_code(properties: dict) -> str:
    """
    Find the zone code from a feature's properties dict.
    Primary field is LAND_USE_ZONE_CODE_ID (e.g. "RZ1", "CZ1").
    """
    for field in ("LAND_USE_ZONE_CODE_ID", "ZONE_CLASS", "ZONE_CODE", "LUZ_CODE", "ZONE"):
        val = properties.get(field)
        if val:
            return str(val).upper().strip()
    return "UNKNOWN"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    centres_path = os.path.join(RAW_DIR, "centres.json")
    if not os.path.exists(centres_path):
        sys.exit("Run fetch_centres.py first.")

    with open(centres_path) as f:
        centres = json.load(f)

    if args.limit:
        centres = centres[: args.limit]

    os.makedirs(RAW_DIR, exist_ok=True)

    for centre in tqdm(centres, desc="Fetching zoning per centre"):
        out_path = os.path.join(RAW_DIR, f"zoning_{centre['place_id']}.json")
        if os.path.exists(out_path):
            tqdm.write(f"  Skipping {centre['name']} (cached)")
            continue

        tqdm.write(f"  Querying zoning near: {centre['name']}")
        try:
            geojson = query_zoning(centre["lat"], centre["lng"], ZONING_RADIUS_M)
        except Exception as e:
            tqdm.write(f"  ERROR for {centre['name']}: {e}")
            geojson = {"type": "FeatureCollection", "features": []}

        # Annotate each feature with the resolved zone code for easy downstream use
        for feat in geojson.get("features", []):
            props = feat.get("properties", {})
            feat["properties"]["_zone_code"] = extract_zone_code(props)

        with open(out_path, "w") as f:
            json.dump(geojson, f)  # no indent — keeps files smaller

        n = len(geojson.get("features", []))
        tqdm.write(f"    → {n} zone polygons")
        time.sleep(0.2)  # polite rate limiting for ACTMAPI

    print("Done.")


if __name__ == "__main__":
    main()
