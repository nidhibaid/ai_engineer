"""The king-tide capture script writes one file per success, records failures, and logs no points."""
import json
import logging
import tempfile
import unittest
from pathlib import Path

import httpx

from elnino_planner.scripts import capture_king_tide_nov2026 as cap
from elnino_planner.settings import Settings

SETTINGS = Settings(contact="test@example.com")


def handler(req: httpx.Request) -> httpx.Response:
    if req.url.host == "api.weather.gov":
        return httpx.Response(200, json={"features": [{"properties": {"event": "Coastal Flood Warning"}}]})
    if req.url.params.get("station") == "9414290" and req.url.params.get("product") == "water_level":
        return httpx.Response(503)
    return httpx.Response(200, json={"data": [{"t": "2026-11-24 18:00", "v": "8.230"}]})


class CaptureKingTide(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with self.assertLogs("planner.http", level=logging.DEBUG) as logs:
            self.run_dir, self.manifest = await cap.run(
                SETTINGS, Path(self.tmp.name), transport=httpx.MockTransport(handler), stamp="T")
        self.log_text = "\n".join(logs.output)

    def test_every_request_is_in_the_manifest(self):
        names = [e["name"] for e in self.manifest]
        expected = [r.name for r in cap.build_requests(["9414523", "9414290"], SETTINGS.app_name)]
        self.assertEqual(names, expected)
        on_disk = json.loads((self.run_dir / "manifest.json").read_text())
        self.assertEqual(on_disk, self.manifest)

    def test_successes_written_failure_recorded(self):
        for e in self.manifest:
            path = self.run_dir / f"{e['name']}.json"
            if e["name"] == "coops_9414290_water_level":
                self.assertFalse(e["ok"])
                self.assertEqual(e["reason"], "upstream_error")
                self.assertFalse(path.exists())
            else:
                self.assertTrue(e["ok"], e["name"])
                self.assertTrue(path.exists(), e["name"])

    def test_nws_events_summarized(self):
        alviso = next(e for e in self.manifest if e["name"] == "nws_active_alviso")
        self.assertEqual(alviso["nws_events"], ["Coastal Flood Warning"])

    def test_no_coordinates_in_logs(self):
        for lat, lon in cap.PLACES.values():
            self.assertNotIn(f"{lat:.2f}", self.log_text)
            self.assertNotIn(f"{lon:.2f}", self.log_text)

    def test_coops_200_error_body_is_flagged(self):
        manifest = [{"name": "x", "ok": True, "bytes": 1, "upstream_message": "No data was found"}]
        self.assertIn("No data was found", cap.summarize(manifest))


if __name__ == "__main__":
    unittest.main()
