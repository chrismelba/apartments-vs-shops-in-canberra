"""
Step 2.5: Download ABS 2021 Census SA1 boundaries + population for ACT.

Fetches:
  - SA1 polygon boundaries from the ABS ArcGIS REST API
  - Total population (Tot_P_P) from the ABS 2021 General Community Profile DataPack ZIP

Joins them by SA1_CODE_2021 and saves to:
    data/raw/abs_sa1_act.geojson

This file is loaded once by analyse.py to compute population_within_500m for each centre.

Usage:
    python src/fetch_population.py
"""

import io
import json
import os
import sys
import time
import zipfile

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import ABS_DATAPACK_URL, ABS_SA1_URL

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
OUT_PATH = os.path.join(RAW_DIR, "abs_sa1_act.geojson")

# Population CSV lives inside the DataPack ZIP at this name
DATAPACK_CSV_NAME = "2021Census_G01_ACT_SA1.csv"
# Fallback: user can place the CSV manually
FALLBACK_CSV_PATH = os.path.join(RAW_DIR, "abs_population_sa1.csv")


# ---------------------------------------------------------------------------
# Fetch SA1 boundaries
# ---------------------------------------------------------------------------

def fetch_sa1_boundaries() -> list:
    """
    Query ABS ArcGIS API for all SA1 polygons in ACT (state_code_2021 = '8').
    Returns a list of GeoJSON feature dicts, each with SA1_CODE_2021 in properties.
    """
    features = []
    offset = 0
    page_size = 1000

    while True:
        params = {
            "where":              "STATE_CODE_2021='8'",
            "outFields":          "SA1_CODE_2021",
            "returnGeometry":     "true",
            "outSR":              "4326",
            "f":                  "geojson",
            "resultOffset":       offset,
            "resultRecordCount":  page_size,
        }
        resp = requests.get(ABS_SA1_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise RuntimeError(f"ABS ArcGIS error: {data['error']}")

        batch = data.get("features", [])
        features.extend(batch)
        print(f"  Fetched {len(features)} SA1s so far…")

        if len(batch) < page_size:
            break
        offset += page_size
        time.sleep(0.3)

    return features


# ---------------------------------------------------------------------------
# Fetch population from DataPack
# ---------------------------------------------------------------------------

def fetch_sa1_population() -> dict:
    """
    Download the ABS 2021 General Community Profile DataPack ZIP for ACT SA1.
    Extracts the G01 CSV and returns {sa1_code_str: total_persons_int}.

    Falls back to FALLBACK_CSV_PATH if the download URL fails.
    """
    # Try DataPack ZIP download first
    try:
        print(f"  Downloading DataPack from ABS…")
        resp = requests.get(ABS_DATAPACK_URL, timeout=120, stream=True)
        resp.raise_for_status()

        content = b""
        for chunk in resp.iter_content(chunk_size=65536):
            content += chunk

        zf = zipfile.ZipFile(io.BytesIO(content))
        # Find the G01 CSV (name may vary slightly)
        csv_name = next(
            (n for n in zf.namelist() if "G01" in n and "SA1" in n and n.endswith(".csv")),
            None,
        )
        if csv_name is None:
            # Broader search
            csv_name = next(
                (n for n in zf.namelist() if "G01" in n and n.endswith(".csv")),
                None,
            )
        if csv_name is None:
            raise RuntimeError(
                f"Could not find G01 CSV in ZIP. Contents: {zf.namelist()[:20]}"
            )

        print(f"  Reading {csv_name} from ZIP…")
        with zf.open(csv_name) as f:
            import csv
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            population = {}
            for row in reader:
                code = str(row.get("SA1_CODE_2021", "")).strip()
                pop_str = str(row.get("Tot_P_P", "0")).strip()
                try:
                    population[code] = int(float(pop_str))
                except (ValueError, TypeError):
                    population[code] = 0
        return population

    except Exception as e:
        print(f"  DataPack download failed: {e}")

    # Fallback: manually placed CSV
    if os.path.exists(FALLBACK_CSV_PATH):
        print(f"  Using fallback CSV: {FALLBACK_CSV_PATH}")
        import csv
        population = {}
        with open(FALLBACK_CSV_PATH, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = str(row.get("SA1_CODE_2021", "")).strip()
                pop_str = str(row.get("Tot_P_P", "0")).strip()
                try:
                    population[code] = int(float(pop_str))
                except (ValueError, TypeError):
                    population[code] = 0
        return population

    raise RuntimeError(
        "Could not obtain population data.\n"
        "Please download the ABS 2021 General Community Profile for SA1/ACT from:\n"
        "  https://www.abs.gov.au/census/find-census-data/datapacks\n"
        "Extract the G01 CSV and save it as:\n"
        f"  {FALLBACK_CSV_PATH}\n"
        "with columns SA1_CODE_2021 and Tot_P_P."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if os.path.exists(OUT_PATH):
        print(f"Already cached: {os.path.relpath(OUT_PATH)}  (delete to re-fetch)")
        return

    os.makedirs(RAW_DIR, exist_ok=True)

    print("Fetching ACT SA1 boundaries from ABS ArcGIS…")
    features = fetch_sa1_boundaries()
    print(f"  {len(features)} SA1 boundary polygons")

    print("Fetching 2021 Census population data…")
    population = fetch_sa1_population()
    print(f"  {len(population)} SA1 population records")

    # Join population onto boundaries
    # ArcGIS returns lowercase field names; DataPack CSV uses uppercase — both same code value
    matched = 0
    for feat in features:
        props = feat.get("properties", {})
        code = str(
            props.get("SA1_CODE_2021") or props.get("sa1_code_2021") or ""
        ).strip()
        pop = population.get(code, 0)
        feat["properties"]["population"] = pop
        feat["properties"]["sa1_code"] = code
        if pop > 0:
            matched += 1

    print(f"  Matched population for {matched}/{len(features)} SA1s")

    geojson = {"type": "FeatureCollection", "features": features}
    with open(OUT_PATH, "w") as f:
        json.dump(geojson, f)

    print(f"Saved → {os.path.relpath(OUT_PATH)}")


if __name__ == "__main__":
    main()
