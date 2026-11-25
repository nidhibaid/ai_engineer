"""See each failure state with your own eyes, with verbose logs, using a local fake server.

    uv run python -m planner.scripts.demo_dummy_tool            # all scenarios
    uv run python -m planner.scripts.demo_dummy_tool timeout    # one scenario
    uv run python -m planner.scripts.demo_dummy_tool --live https://httpbin.org/json   # real network

Scenarios: success, timeout, upstream_error, flaky (500 then 200), not_found, bad_json, cache.
"""
import asyncio
import json
import sys

import httpx

from elnino_planner.logging_setup import setup_logging
from elnino_planner.settings import load_settings
from elnino_planner.tools.dummy import dummy_tool
from elnino_planner.tools.http_client import HttpClient

URL = "https://demo.test/data"


def make_handler(name: str):
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if name == "timeout":
            raise httpx.ReadTimeout("simulated", request=req)
        if name == "upstream_error":
            return httpx.Response(503)
        if name == "flaky":
            return httpx.Response(500) if state["n"] == 1 else httpx.Response(200, json={"value": 1})
        if name == "not_found":
            return httpx.Response(404)
        if name == "bad_json":
            return httpx.Response(200, text="<html>oops</html>")
        return httpx.Response(200, json={"value": 42})

    return handler


async def run_scenario(name: str) -> None:
    print(f"\n--- {name} ---")
    settings = load_settings()
    client = HttpClient(settings, transport=httpx.MockTransport(make_handler(name)))
    ttl = 60 if name == "cache" else 0
    for i in range(2 if name == "cache" else 1):
        res = await dummy_tool(client, URL, ttl_s=ttl)
        print(f"call {i + 1}:", json.dumps(res.to_dict(), indent=2, default=str))
    await client.aclose()


async def run_live(url: str) -> None:
    client = HttpClient(load_settings())
    res = await dummy_tool(client, url)
    print(json.dumps(res.to_dict(), indent=2, default=str))
    await client.aclose()


async def main(argv: list[str]) -> None:
    setup_logging(load_settings().log_level)
    if argv and argv[0] == "--live":
        await run_live(argv[1])
        return
    names = argv or ["success", "timeout", "upstream_error", "flaky", "not_found", "bad_json", "cache"]
    for n in names:
        await run_scenario(n)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
