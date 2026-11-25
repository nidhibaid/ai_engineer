"""Load and validate a county pack. Errors name the file and the field."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from elnino_planner.config.schema import CountyPack

COUNTIES_DIR = Path(__file__).resolve().parent / "counties"


class CountyPackError(Exception):
    pass


def load_county_pack(fips: str, directory: Path = COUNTIES_DIR) -> CountyPack:
    path = directory / f"{fips}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in directory.glob("*.yaml"))
        raise CountyPackError(f"No county pack at {path}. Available: {available or 'none'}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise CountyPackError(f"{path} is not valid YAML: {exc}") from exc
    try:
        pack = CountyPack.model_validate(raw)
    except ValidationError as exc:
        lines = [f"  {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        raise CountyPackError(f"{path} failed validation:\n" + "\n".join(lines)) from exc
    if pack.county_fips != fips:
        raise CountyPackError(f"{path} declares county_fips={pack.county_fips}, expected {fips}")
    return pack
