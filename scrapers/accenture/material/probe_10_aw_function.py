"""Probe 10: extract the full body of the Aw() function from the bundle.

probe_09 showed Aw() destructures hO then starts building FormData with
`d.append("startIndex", Lw(r))`. We need ALL the d.append() calls to
know the exact FormData keys the API expects.
"""
from __future__ import annotations

import pathlib
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

JS = pathlib.Path(__file__).parent / "clientlib_site.min.js"


def main() -> None:
    js = JS.read_text(encoding="utf-8")
    # Find "Aw=function" and dump 4KB after
    m = re.search(r"Aw=function", js)
    if not m:
        print("Aw=function not found")
        return
    start = m.start()
    end = min(len(js), start + 6000)
    print(f"=== Aw body @ offset {start} ===\n")
    print(js[start:end])
    print()
    # Also pull every d.append("XXX", ...) call within next 4KB
    body = js[start:end]
    appends = re.findall(r'd\.append\(["\']([^"\']+)["\'][^,)]*[,)]', body)
    print("\n=== FormData keys (from d.append) ===")
    for k in appends:
        print(f"  {k}")


if __name__ == "__main__":
    main()
