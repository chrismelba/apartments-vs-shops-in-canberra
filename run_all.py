"""
Convenience runner: executes the full pipeline in order.

Usage:
    python run_all.py           # full run
    python run_all.py --limit 5 # test with 5 centres only
"""

import argparse
import subprocess
import sys


STEPS = [
    ("1/7 Discover centres (supermarkets)", [sys.executable, "src/fetch_centres.py"]),
    ("2/7 Discover local centres (ACTMAPI)",[sys.executable, "src/fetch_local_centres.py"]),
    ("3/7 Fetch ABS population (SA1)",      [sys.executable, "src/fetch_population.py"]),
    ("4/7 Fetch shops",                     [sys.executable, "src/fetch_shops.py"]),
    ("5/7 Fetch zoning",                    [sys.executable, "src/fetch_zoning.py"]),
    ("6/7 Analyse scores",                  [sys.executable, "src/analyse.py"]),
    ("7/7 Generate map",                    [sys.executable, "src/visualise.py"]),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N centres (useful for testing)"
    )
    args = parser.parse_args()

    for label, cmd in STEPS:
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")

        # Inject --limit into steps that support it
        full_cmd = list(cmd)
        if args.limit and "fetch_shops" in cmd[1] or args.limit and "fetch_zoning" in cmd[1]:
            full_cmd += ["--limit", str(args.limit)]

        result = subprocess.run(full_cmd, check=False)
        if result.returncode != 0:
            print(f"\nERROR: step failed (exit code {result.returncode}). Stopping.")
            sys.exit(result.returncode)

    print("\n✓ Pipeline complete. Open output/map.html in your browser.")


if __name__ == "__main__":
    main()
