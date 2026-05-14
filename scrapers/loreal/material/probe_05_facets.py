"""Probe 5: enumerate every facet L'Oréal exposes + their option values.

Two angles:
A) Hit the AJAX endpoints (SearchJobsAJAX, SearchJobsAJAXJSON) that the
   site's own JS uses — those typically return either a JSON blob with
   the full facet panel state, or an HTML fragment containing the
   checkbox list for every facet.
B) Scan the saved (curl_cffi) HTML for facet markup we missed —
   Avature commonly renders facets as <a href="...?N_M_K=V"> link
   anchors in a sidebar that gets shown/hidden by JS. Each unique
   N_M_K pair tells us a facet exists; its anchor text is the label.

Goal: build the table { facet_id → human_label → { option_id → option_label } }
so the user can pick which axes to narrow on (e.g. contract type, function).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HOST = "https://careers.loreal.com"
TARGET = HOST + "/en_US/jobs/SearchJobs?3_110_3=18022"
AJAX = HOST + "/en_US/jobs/SearchJobsAJAX"
AJAX_JSON = HOST + "/en_US/jobs/SearchJobsAJAXJSON"
OUT_DIR = pathlib.Path(__file__).parent
SAVED_HTML = OUT_DIR / "cffi_get_https_careers.loreal.com_en_us_jobs_searchjobs_3_110_3_18022.html"


def warm(s):
    s.get(HOST + "/en_US/jobs/SearchJobs", timeout=30)
    time.sleep(2.0)


# -------------- A: AJAX endpoint probes -----------------------------------

def probe_ajax(s, label: str, url: str) -> None:
    print(f"\n>>> {label}\n    URL: {url}")
    r = s.get(
        url,
        headers={
            "Referer": TARGET,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
        timeout=30,
    )
    print(f"    status={r.status_code}  len={len(r.text)}  ct={r.headers.get('content-type')}")
    print(f"    body[:300]={r.text[:300]!r}")
    # try parse as json
    try:
        data = r.json()
        print(f"    JSON ok: top-level keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
        out = OUT_DIR / f"ajax_{label}.json"
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"    saved {out.name}")
    except Exception:
        # HTML fragment
        out = OUT_DIR / f"ajax_{label}.html"
        out.write_text(r.text, encoding="utf-8")
        print(f"    saved {out.name} (not JSON)")


# -------------- B: scan saved page for facet markup -----------------------

# Avature's URL encoding: `<digit>_<digit>_<digit>=<value>`. Examples seen:
#   3_110_3 = country  (we know this maps to "Country" → 18022 = France)
#   3_NNN_N = facet of some kind
# Plus there are FORM-side names like `9139[]` (used in selects). Likely
# the same facets, two encodings. We enumerate every X_Y_Z=V pair found
# in hrefs and try to reconstruct labels from anchor text or sibling text.

def scan_facets_in_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # Every anchor in the doc whose href contains an Avature facet param
    facet_pattern = re.compile(r"[?&](\d+_\d+_\d+)=([^&#]+)")

    seen: dict[str, dict[str, str]] = {}  # facet_id -> {value: anchor_text}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for m in facet_pattern.finditer(href):
            facet_id, value = m.group(1), m.group(2)
            text = a.get_text(" ", strip=True)
            if not text:
                continue
            seen.setdefault(facet_id, {})[value] = text

    # Also: look for headings near facet anchors to learn the facet's
    # human-readable name. Common Avature pattern is a panel like
    #   <h3>Function</h3>
    #     <ul>
    #       <li><a href="?3_50_3=4123">Marketing</a></li>
    #       ...
    headings_for_facet: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        for m in facet_pattern.finditer(a["href"]):
            facet_id = m.group(1)
            if facet_id in headings_for_facet:
                continue
            # walk up to find a heading near this anchor
            node = a
            for _ in range(8):
                if not node or not getattr(node, "parent", None):
                    break
                node = node.parent
                if not hasattr(node, "find_all"):
                    continue
                for h in node.find_all(["h2", "h3", "h4", "h5", "legend", "label"], recursive=False):
                    txt = h.get_text(" ", strip=True)
                    if txt and txt.lower() not in {"job search", "filters"}:
                        headings_for_facet[facet_id] = txt
                        break
                if facet_id in headings_for_facet:
                    break

    out = {}
    for facet_id, opts in sorted(seen.items()):
        out[facet_id] = {
            "label_guess": headings_for_facet.get(facet_id, "?"),
            "options": opts,
        }
    return out


def main() -> None:
    if SAVED_HTML.exists():
        print(f"--- scanning saved HTML ({SAVED_HTML.name}) for facet anchors ---")
        html = SAVED_HTML.read_text(encoding="utf-8")
        facets = scan_facets_in_html(html)
        print(f"  facet_id  | label_guess          | option count")
        for fid, info in facets.items():
            print(f"  {fid:9s} | {info['label_guess']:20s} | {len(info['options'])}")
        # Dump full table for human inspection
        for fid, info in facets.items():
            print(f"\n  --- {fid} :: {info['label_guess']} ---")
            for val, text in list(info["options"].items())[:30]:
                print(f"    {val:8s} = {text!r}")
            if len(info["options"]) > 30:
                print(f"    … {len(info['options']) - 30} more")

    print("\n--- AJAX endpoint probes ---")
    s = cffi_requests.Session(impersonate="chrome131")
    warm(s)
    probe_ajax(s, "json_with_france", AJAX_JSON + "?3_110_3=18022")
    time.sleep(2.0)
    probe_ajax(s, "ajax_with_france", AJAX + "?s=1&3_110_3=18022")
    time.sleep(2.0)
    probe_ajax(s, "json_no_filter", AJAX_JSON)
    time.sleep(2.0)
    probe_ajax(s, "ajax_no_filter", AJAX + "?s=1")


if __name__ == "__main__":
    main()
