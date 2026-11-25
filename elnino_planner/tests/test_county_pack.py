import tempfile
import unittest
from pathlib import Path

from elnino_planner.config.loader import CountyPackError, load_county_pack


class CountyPack(unittest.TestCase):
    def test_loads_santa_clara(self):
        p = load_county_pack("06085")
        self.assertEqual(p.name, "Santa Clara County")
        self.assertEqual({s.id for s in p.tide_stations}, {"9414523", "9414290"})
        self.assertEqual(p.retrieval_scopes, ["city", "county", "utility", "state", "federal"])

    def test_missing_pack_lists_available(self):
        with self.assertRaises(CountyPackError) as cm:
            load_county_pack("99999")
        self.assertIn("06085", str(cm.exception))

    def test_invalid_pack_names_the_field(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "06001.yaml").write_text('county_fips: "06001"\nname: X\n')
            with self.assertRaises(CountyPackError) as cm:
                load_county_pack("06001", Path(d))
            self.assertIn("tide_stations", str(cm.exception))

    def test_fips_mismatch_rejected(self):
        src = Path(__file__).resolve().parents[1] / "config" / "counties" / "06085.yaml"
        with tempfile.TemporaryDirectory() as d:
            Path(d, "06001.yaml").write_text(src.read_text())
            with self.assertRaises(CountyPackError):
                load_county_pack("06001", Path(d))

    def test_no_county_specifics_in_code(self):
        """Rule 6: county names, station ids and phone numbers stay out of .py files."""
        root = Path(__file__).resolve().parents[1]
        banned = ["Santa Clara", "9414523", "808-7800", "valleywater"]
        for py in root.rglob("*.py"):
            if "tests" in py.parts:
                continue
            text = py.read_text()
            for word in banned:
                self.assertNotIn(word, text, f"{word!r} found in {py}")


if __name__ == "__main__":
    unittest.main()
