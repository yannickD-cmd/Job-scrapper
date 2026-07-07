"""One-shot A/B/C diagnostic for Pernod Ricard's residential HTTP_400.

Question: is the empty-message HTTP_400 a token-bucket BAN, or a COOKIE-FREE
REGRESSION on residential (the original api_notes.md @ ea6a091 said residential
faceted POSTs need seeded CXS cookies; the cookie-free rewrite was only ever
validated on datacenter/CI)?

Runs at most THREE requests, NO retries (far less pressure than the scraper's
3-attempt retry storm). Still: run SPARINGLY and ideally AFTER a cooldown, since
the endpoint has an escalating ban and a live ban makes the result inconclusive.

    .venv\\Scripts\\python.exe scrapers\\pernodricard\\material\\diag_cookie_ab.py

Reading the verdict:
  [A] cookie-free faceted POST  -> what the current scraper does
  [bare] cookie-free UNfaceted POST -> should be 200 (seeds session cookies)
  [B] faceted POST REUSING those cookies -> the "seeded" path

  A ok                      -> cookie-free works now; earlier 400s were a transient BAN. No code change.
  A 400, bare 400           -> even the plain listing is blocked -> hard ban / IP block. Wait longer.
  A 400, bare 200, B ok     -> COOKIE REGRESSION: faceted POST needs prior cookies. Fix: seed on residential.
  A 400, bare 200, B 400    -> faceted specifically rejected even with cookies -> throttle on the facet bucket (or facet-body issue).
"""
from __future__ import annotations

import requests

HOST = "https://pernodricard.wd3.myworkdayjobs.com"
SITE = "pernod-ricard"
TENANT = "pernodricard"
LIST_URL = f"{HOST}/wday/cxs/{TENANT}/{SITE}/jobs"
TECH_FAMILY = "5c4276c36b5a1001e317a08d36940000"
REGULAR = "371688745b5701d8d14db11fa6174024"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": HOST,
    "Referer": f"{HOST}/en-US/{SITE}",
    "X-Calypso-Selected-Locale": "en-US",
}

FACETED_BODY = {
    "appliedFacets": {"jobFamilyGroup": [TECH_FAMILY], "workerSubType": [REGULAR]},
    "limit": 20, "offset": 0, "searchText": "",
}
BARE_BODY = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}


def _post(session: requests.Session, body: dict) -> tuple[int, bool, str]:
    r = session.post(LIST_URL, json=body, timeout=30)
    ok = r.status_code == 200 and '"errorCode"' not in r.text[:120]
    return r.status_code, ok, r.text[:160].replace("\n", " ")


def main() -> None:
    # [A] cookie-free faceted POST == exactly what the scraper does today.
    sa = requests.Session(); sa.headers.update(HEADERS); sa.cookies.clear()
    a_code, a_ok, a_body = _post(sa, FACETED_BODY)
    print(f"[A]   cookie-free faceted POST : HTTP {a_code}  ok={a_ok}")
    print(f"      {a_body}")

    # [bare] cookie-free UNfaceted POST -- should be 200 and seed session cookies.
    sb = requests.Session(); sb.headers.update(HEADERS); sb.cookies.clear()
    bare_code, bare_ok, bare_body = _post(sb, BARE_BODY)
    print(f"[bare] cookie-free UNfaceted   : HTTP {bare_code}  ok={bare_ok}  "
          f"cookies_now={list(sb.cookies.keys())}")
    print(f"      {bare_body}")

    # [B] faceted POST reusing the cookies the bare POST just set (do NOT clear).
    b_code, b_ok, b_body = _post(sb, FACETED_BODY)
    print(f"[B]   seeded faceted POST      : HTTP {b_code}  ok={b_ok}")
    print(f"      {b_body}")

    print("\n=== verdict ===")
    if a_ok:
        print("cookie-free WORKS now -> earlier 400s were a transient BAN. No code change needed.")
    elif not bare_ok:
        print("even the plain listing 400s -> hard ban / IP block. Wait (hours), re-run. Inconclusive for cookie theory.")
    elif b_ok:
        print("cookie-free 400s but SEEDED (bare-then-faceted) works -> RESIDENTIAL COOKIE REGRESSION.")
        print("  Fix: on residential, do a bare unfaceted POST (or CXS-root GET) to seed cookies before faceting.")
    else:
        print("bare 200 but faceted 400 even seeded -> faceted bucket throttled (or facet-body issue), not a cookie problem.")


if __name__ == "__main__":
    main()
