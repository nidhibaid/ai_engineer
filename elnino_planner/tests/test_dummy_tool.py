"""WP-0 acceptance: a dummy tool returns success, timeout and upstream-error states."""
import unittest

import httpx

from elnino_planner.settings import Settings
from elnino_planner.tools.dummy import dummy_tool
from elnino_planner.tools.http_client import HttpClient, TTLCache
from elnino_planner.tools.result import FailureReason, Status

URL = "https://dummy.test/data"


def client_for(handler, cache=None) -> HttpClient:
    return HttpClient(Settings(contact="test@example.com"), transport=httpx.MockTransport(handler), cache=cache)


class DummyToolStates(unittest.IsolatedAsyncioTestCase):
    async def test_success(self):
        c = client_for(lambda r: httpx.Response(200, json={"value": 42}))
        res = await dummy_tool(c, URL)
        self.assertEqual(res.status, Status.OK)
        self.assertEqual(res.data, {"value": 42})
        self.assertIsNotNone(res.fetched_at)
        self.assertIsNone(res.failure_reason)

    async def test_timeout_retries_once_then_unavailable(self):
        calls = []

        def handler(req):
            calls.append(1)
            raise httpx.ReadTimeout("slow", request=req)

        res = await dummy_tool(client_for(handler), URL)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertEqual(res.failure_reason, FailureReason.TIMEOUT)
        self.assertIsNone(res.data)  # never a substitute value
        self.assertIsNotNone(res.fallback_url)
        self.assertEqual(len(calls), 2)  # first try + one retry

    async def test_upstream_500_retries_once_then_unavailable(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(503)

        res = await dummy_tool(client_for(handler), URL)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertEqual(res.failure_reason, FailureReason.UPSTREAM_ERROR)
        self.assertEqual(len(calls), 2)

    async def test_retry_can_recover(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(502) if len(calls) == 1 else httpx.Response(200, json={"value": 1})

        res = await dummy_tool(client_for(handler), URL)
        self.assertEqual(res.status, Status.OK)
        self.assertEqual(len(calls), 2)

    async def test_4xx_is_not_retried(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(404)

        res = await dummy_tool(client_for(handler), URL)
        self.assertEqual(res.failure_reason, FailureReason.CLIENT_ERROR)
        self.assertEqual(len(calls), 1)

    async def test_network_error(self):
        def handler(req):
            raise httpx.ConnectError("refused", request=req)

        res = await dummy_tool(client_for(handler), URL)
        self.assertEqual(res.failure_reason, FailureReason.NETWORK_ERROR)

    async def test_non_json_and_wrong_shape(self):
        res = await dummy_tool(client_for(lambda r: httpx.Response(200, text="<html>")), URL)
        self.assertEqual(res.failure_reason, FailureReason.BAD_RESPONSE)
        res = await dummy_tool(client_for(lambda r: httpx.Response(200, json={"nope": 1})), URL)
        self.assertEqual(res.failure_reason, FailureReason.BAD_RESPONSE)

    async def test_user_agent_sent(self):
        seen = {}

        def handler(req):
            seen["ua"] = req.headers["user-agent"]
            return httpx.Response(200, json={"value": 1})

        await dummy_tool(client_for(handler), URL)
        self.assertIn("test@example.com", seen["ua"])


class Caching(unittest.IsolatedAsyncioTestCase):
    async def test_cache_hit_keeps_original_fetched_at_and_skips_network(self):
        now = [0.0]
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(200, json={"value": 7})

        c = client_for(handler, cache=TTLCache(clock=lambda: now[0]))
        first = await dummy_tool(c, URL, ttl_s=60)
        now[0] = 30
        second = await dummy_tool(c, URL, ttl_s=60)
        self.assertEqual(len(calls), 1)
        self.assertTrue(second.from_cache)
        self.assertEqual(first.fetched_at, second.fetched_at)
        now[0] = 61  # expired
        third = await dummy_tool(c, URL, ttl_s=60)
        self.assertEqual(len(calls), 2)
        self.assertFalse(third.from_cache)

    async def test_failures_are_not_cached(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(404) if len(calls) == 1 else httpx.Response(200, json={"value": 1})

        c = client_for(handler)
        self.assertEqual((await dummy_tool(c, URL, ttl_s=60)).status, Status.UNAVAILABLE)
        self.assertEqual((await dummy_tool(c, URL, ttl_s=60)).status, Status.OK)

    async def test_no_ttl_means_no_caching(self):
        calls = []

        def handler(req):
            calls.append(1)
            return httpx.Response(200, json={"value": 1})

        c = client_for(handler)
        await dummy_tool(c, URL)
        await dummy_tool(c, URL)
        self.assertEqual(len(calls), 2)


class Privacy(unittest.IsolatedAsyncioTestCase):
    async def test_logs_never_contain_query_params(self):
        c = client_for(lambda r: httpx.Response(200, json={"value": 1}))
        with self.assertLogs("planner.http", level="INFO") as cm:
            await c.get_json(URL, params={"latitude": 37.4219, "longitude": -122.0841}, tool="dummy")
        text = " ".join(cm.output)
        self.assertNotIn("37.42", text)
        self.assertNotIn("122.08", text)
        self.assertIn("tool=dummy", text)
        self.assertIn("host=dummy.test", text)


class LoggingSetup(unittest.TestCase):
    def test_httpx_url_logging_is_silenced(self):
        import logging
        from elnino_planner.logging_setup import setup_logging
        setup_logging("DEBUG")
        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
