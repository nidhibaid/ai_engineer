"""Step 1 when something is wrong: print effective settings and county pack status.

    uv run python -m planner.scripts.check_env
"""
import sys

from elnino_planner.config import load_county_pack
from elnino_planner.config.loader import CountyPackError
from elnino_planner.settings import load_settings


def main() -> int:
    s = load_settings()
    print("== settings ==")
    for k, v in s.describe().items():
        print(f"  {k}: {v}")
    for p in s.problems():
        print(f"  WARN {p}")
    print("== county pack ==")
    try:
        pack = load_county_pack(s.county_fips)
    except CountyPackError as exc:
        print(f"  FAIL {exc}")
        return 1
    print(f"  OK   {pack.name} ({pack.county_fips}); {len(pack.tide_stations)} tide stations, "
          f"{len(pack.creek_gauges)} creek gauges")
    for item in pack.unverified:
        print(f"  TODO unverified: {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
