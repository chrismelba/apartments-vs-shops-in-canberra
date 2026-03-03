"""
Step 2: Fetch all shops near each shopping centre.

For every centre in data/raw/centres.json:
  - Run a Nearby Search (radius = SHOPS_RADIUS_M) to get up to 60 places
  - For each place, call Place Details to get rating, types, opening_hours, price_level
  - Cache results to data/raw/shops_{place_id}.json

Usage:
    python src/fetch_shops.py            # all centres
    python src/fetch_shops.py --limit 5  # first 5 centres only (for testing)
"""

import argparse
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv
from tqdm import tqdm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import (
    PLACES_BASE_URL,
    PLACES_PAGE_DELAY,
    PLACE_DETAIL_FIELDS,
    SHOPS_RADIUS_M,
)

load_dotenv()
API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def nearby_search_page(lat, lng, radius, page_token=None):
    params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "key": API_KEY,
    }
    if page_token:
        params["pagetoken"] = page_token

    resp = requests.get(f"{PLACES_BASE_URL}/nearbysearch/json", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(f"Nearby Search error: {status} — {data.get('error_message', '')}")

    return data.get("results", []), data.get("next_page_token")


def fetch_nearby_all(lat, lng, radius):
    """Collect up to 60 nearby places (3 pages)."""
    places = []
    page_token = None
    for page in range(3):
        if page > 0:
            time.sleep(PLACES_PAGE_DELAY)
        batch, page_token = nearby_search_page(lat, lng, radius, page_token)
        places.extend(batch)
        if not page_token:
            break
    return places


def place_details(place_id):
    params = {
        "place_id": place_id,
        "fields": PLACE_DETAIL_FIELDS,
        "key": API_KEY,
    }
    resp = requests.get(f"{PLACES_BASE_URL}/details/json", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status != "OK":
        return None

    return data.get("result", {})


def fetch_centre_shops(centre):
    """
    Fetch all shops for one centre, enriched with Place Details.
    Returns a list of shop dicts.
    """
    lat, lng = centre["lat"], centre["lng"]
    nearby = fetch_nearby_all(lat, lng, SHOPS_RADIUS_M)

    shops = []
    for p in nearby:
        pid = p["place_id"]
        detail_cache = os.path.join(RAW_DIR, f"detail_{pid}.json")

        if os.path.exists(detail_cache):
            with open(detail_cache) as f:
                detail = json.load(f)
        else:
            time.sleep(0.1)  # gentle rate limiting
            detail = place_details(pid)
            if detail:
                with open(detail_cache, "w") as f:
                    json.dump(detail, f)

        if not detail:
            continue

        # Extract opening hours: does it open on weekends?
        oh = detail.get("opening_hours", {})
        periods = oh.get("periods", [])
        weekend_open = any(p.get("open", {}).get("day") in (0, 6) for p in periods)

        shops.append({
            "place_id":           pid,
            "name":               detail.get("name", p.get("name", "")),
            "types":              detail.get("types", p.get("types", [])),
            "rating":             detail.get("rating"),
            "user_ratings_total": detail.get("user_ratings_total", 0),
            "price_level":        detail.get("price_level"),
            "has_hours":          bool(periods),
            "weekend_open":       weekend_open,
            "lat":                detail.get("geometry", {}).get("location", {}).get("lat"),
            "lng":                detail.get("geometry", {}).get("location", {}).get("lng"),
        })

    return shops


def main():
    if not API_KEY:
        sys.exit("ERROR: GOOGLE_MAPS_API_KEY not set.")

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Process only first N centres")
    args = parser.parse_args()

    centres_path = os.path.join(RAW_DIR, "centres.json")
    if not os.path.exists(centres_path):
        sys.exit("Run fetch_centres.py first.")

    with open(centres_path) as f:
        centres = json.load(f)

    if args.limit:
        centres = centres[: args.limit]

    os.makedirs(RAW_DIR, exist_ok=True)

    for centre in tqdm(centres, desc="Fetching shops per centre"):
        out_path = os.path.join(RAW_DIR, f"shops_{centre['place_id']}.json")
        if os.path.exists(out_path):
            tqdm.write(f"  Skipping {centre['name']} (cached)")
            continue

        tqdm.write(f"  Fetching shops near: {centre['name']}")
        try:
            shops = fetch_centre_shops(centre)
        except Exception as e:
            tqdm.write(f"  ERROR for {centre['name']}: {e}")
            shops = []

        with open(out_path, "w") as f:
            json.dump(shops, f, indent=2)

        tqdm.write(f"    → {len(shops)} shops saved")

    print("Done.")


if __name__ == "__main__":
    main()
