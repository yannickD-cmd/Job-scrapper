"""Probe 12: dig into the successful 211-jobs response.

We need:
1. The 'aggregations' structure so we know the valid `employeeType`
   values (and how to construct the filter payload).
2. Per-row data shape: see what's in jobCardData, skill, etc. for the
   real scraper to extract.
3. Try the correct filter format derived from aggregations.
"""
from __future__ import annotations

import json
import pathlib
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

OUT = pathlib.Path(__file__).parent / "api_call_minimal_france.txt"


def main() -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    print(f"top-level keys: {list(data.keys())}")
    print(f"total: {data.get('total')}")
    print(f"status: {data.get('status')}")
    print(f"message: {data.get('message')}")
    print(f"data list length: {len(data.get('data') or [])}")

    print("\n=== aggregations ===")
    aggs = data.get("aggregations") or []
    print(f"aggregations count: {len(aggs)}")
    for agg in aggs:
        if isinstance(agg, dict):
            keys = list(agg.keys())
            field = agg.get("fieldName") or agg.get("metadataFieldName") or agg.get("displayfacet") or "?"
            label = agg.get("label") or agg.get("metadataFieldDisplayName") or ""
            items = agg.get("items") or []
            print(f"\n  --- field={field!r}  label={label!r}  ({len(items)} items)")
            print(f"      agg keys: {keys}")
            for item in items[:25]:
                if isinstance(item, dict):
                    term = item.get("term") or item.get("value") or item.get("name")
                    count = item.get("count") or item.get("docCount")
                    print(f"        {term!r:60s} count={count}")
            if len(items) > 25:
                print(f"        ... {len(items) - 25} more")

    print("\n=== first 3 jobs ===")
    for j in (data.get("data") or [])[:3]:
        print("---")
        for k in [
            "title", "jobId", "jobCityState", "regionDescription",
            "postedDate", "postedDateText",
            "jobDetailUrl", "internalReferUrl",
            "skill", "businessArea", "employeeType",
            "jobTypeDescription", "remoteType",
            "requisitionId", "jobLanguageCd",
        ]:
            if k in j:
                v = j[k]
                print(f"  {k}: {str(v)[:120]!r}")


if __name__ == "__main__":
    main()
