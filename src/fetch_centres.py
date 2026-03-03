"""
Step 1: Discover shopping centre seed points.

Uses the Google Places Text Search API to find supermarkets across Canberra.
Each unique supermarket location becomes the anchor point for one "centre" —
the geographic origin for nearby-shop and zoning analysis.

Saves: data/raw/centres.json
"""

import json
import math
import os
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import (
    CANBERRA_CENTER,
    CANBERRA_BBOX,
    DEDUP_RADIUS_M,
    PLACES_BASE_URL,
    PLACES_PAGE_DELAY,
)

load_dotenv()
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


def haversine_m(lat1, lng1, lat2, lng2):
    """Return distance in metres between two WGS84 points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def text_search_page(query, location, radius, page_token=None):
    """
    Perform one page of Google Places Text Search.
    Returns (results_list, next_page_token_or_None).
    """
    params = {
        "query": query,
        "location": f"{location['lat']},{location['lng']}",
        "radius": radius,
        "key": API_KEY,
    }
    if page_token:
        params["pagetoken"] = page_token

    resp = requests.get(f"{PLACES_BASE_URL}/textsearch/json", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(f"Places API error: {status} — {data.get('error_message', '')}")

    return data.get("results", []), data.get("next_page_token")


def fetch_all_supermarkets():
    """
    Search for supermarkets within 30 km of Canberra centre.
    Paginates through all results (max 3 pages = 60 results).
    """
    results = []
    # Use a large radius so we cover all of Canberra
    location = CANBERRA_CENTER
    radius = 30_000  # 30 km

    page_token = None
    page = 0
    while True:
        page += 1
        if page > 1:
            time.sleep(PLACES_PAGE_DELAY)

        print(f"  Fetching page {page}…")
        batch, page_token = text_search_page(
            query="supermarket",
            location=location,
            radius=radius,
            page_token=page_token,
        )
        results.extend(batch)
        print(f"    Got {len(batch)} results (total so far: {len(results)})")

        if not page_token or page >= 3:
            break

    return results


def deduplicate(places):
    """
    Keep only one place per DEDUP_RADIUS_M cluster.
    Iterates in order; if a new place is within threshold of any kept place, drop it.
    """
    kept = []
    for p in places:
        loc = p["geometry"]["location"]
        lat, lng = loc["lat"], loc["lng"]

        too_close = False
        for k in kept:
            if haversine_m(lat, lng, k["lat"], k["lng"]) < DEDUP_RADIUS_M:
                too_close = True
                break

        if not too_close:
            kept.append({
                "place_id": p["place_id"],
                "name":     p["name"],
                "lat":      lat,
                "lng":      lng,
                "address":  p.get("formatted_address", ""),
            })

    return kept


def within_bbox(lat, lng, bbox):
    return bbox["south"] <= lat <= bbox["north"] and bbox["west"] <= lng <= bbox["east"]


def main():
    if not API_KEY:
        sys.exit("ERROR: GOOGLE_MAPS_API_KEY not set. Copy .env.example → .env and add your key.")

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "centres.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print("Fetching supermarkets from Google Places…")
    raw = fetch_all_supermarkets()

    # Filter to ACT bounding box and deduplicate
    in_bbox = [
        p for p in raw
        if within_bbox(
            p["geometry"]["location"]["lat"],
            p["geometry"]["location"]["lng"],
            CANBERRA_BBOX,
        )
    ]
    print(f"Within ACT bbox: {len(in_bbox)} (from {len(raw)} total)")

    centres = deduplicate(in_bbox)
    print(f"After de-duplication ({DEDUP_RADIUS_M}m): {len(centres)} centres")

    with open(out_path, "w") as f:
        json.dump(centres, f, indent=2)
    print(f"Saved → {os.path.relpath(out_path)}")


if __name__ == "__main__":
    main()
