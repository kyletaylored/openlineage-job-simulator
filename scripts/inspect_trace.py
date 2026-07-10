#!/usr/bin/env python3
"""Fetch raw span data for a trace ID via the Datadog API, for debugging
log/trace correlation. Reads DD_API_KEY/DD_APP_KEY/DD_SITE from .env.

Usage: python3 scripts/inspect_trace.py <trace_id>
"""
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

DD_API_KEY = os.environ.get("DD_API_KEY", "").strip()
DD_APP_KEY = os.environ.get("DD_APP_KEY", "").strip()
DD_SITE = os.environ.get("DD_SITE", "datadoghq.com").strip()


def main():
    if len(sys.argv) != 2:
        print("usage: python3 scripts/inspect_trace.py <trace_id>", file=sys.stderr)
        sys.exit(1)
    trace_id = sys.argv[1]

    if not DD_API_KEY or not DD_APP_KEY:
        print("DD_API_KEY and DD_APP_KEY must both be set (in .env)", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.{DD_SITE}/api/v2/trace/{trace_id}"
    resp = requests.get(
        url,
        headers={"DD-API-KEY": DD_API_KEY, "DD-APPLICATION-KEY": DD_APP_KEY},
        timeout=15,
    )
    print(f"GET {url} -> {resp.status_code}", file=sys.stderr)
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(resp.text)


if __name__ == "__main__":
    main()
