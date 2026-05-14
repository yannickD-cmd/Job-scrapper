"""Probe 9: extract the exact JS code around the API call site.

The bundle is a single-line minified blob. Grep -A can't show context,
so we read the file directly and dump 2KB on either side of the API
URL literal. That should reveal the function that prepares the
FormData payload + the exact param names.
"""
from __future__ import annotations

import pathlib
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

JS_PATH = pathlib.Path(__file__).parent / "clientlib_site.min.js"
LIT = "/api/accenture/jobsearch/result"


def main() -> None:
    js = JS_PATH.read_text(encoding="utf-8")
    print(f"bundle length: {len(js):,}")

    for m in re.finditer(re.escape(LIT), js):
        start = max(0, m.start() - 2500)
        end = min(len(js), m.end() + 2500)
        ctx = js[start:end]
        print(f"\n=== context around offset {m.start()} ===")
        print(ctx)
        print()


if __name__ == "__main__":
    main()
