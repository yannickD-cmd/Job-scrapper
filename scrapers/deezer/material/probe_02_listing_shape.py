"""Probe 2: parse the saved landing.html and figure out card shape.

Probe 1 confirmed the listing is server-rendered (WordPress) with
job links like /en/job-details/?jid=<numeric>. Now we need to know:

- What's the DOM container for each job card?
- Where are title / location / category / contract-type rendered?
- Is the full listing on one page, or is there pagination / load-more?
- Total job count anywhere on the page?

We work off the saved HTML so this is offline + fast to iterate.
"""
from __future__ import annotations

import pathlib
import re
import sys

from bs4 import BeautifulSoup

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

HTML_FILE = pathlib.Path(__file__).parent / "landing.html"


def main() -> None:
    html = HTML_FILE.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # 1. Find every link to a job-details page and walk up to its container.
    job_links = soup.find_all(
        "a", href=re.compile(r"/job-details/\?jid=\d+")
    )
    print(f"Found {len(job_links)} job-detail links\n")

    if not job_links:
        return

    # Inspect the first few — print the link's outerHTML and its parent's
    # outerHTML truncated, so we can see what the wrapping card looks like.
    print("=== First job link in context ===")
    first = job_links[0]
    print("LINK TAG:")
    print(first)
    print()
    print("CLASSES ON LINK:", first.get("class"))
    print()

    # Walk up a few levels to find the card container.
    for depth in range(1, 5):
        parent = first
        for _ in range(depth):
            parent = parent.parent
            if parent is None:
                break
        if parent is None:
            break
        snippet = str(parent)
        if len(snippet) > 1500:
            snippet = snippet[:1500] + "...[truncated]"
        print(f"--- Parent depth {depth} (tag={parent.name}, classes={parent.get('class')}) ---")
        print(snippet)
        print()

    # 2. Look for filter / facet markup (category, contract-type dropdowns
    #    or sidebars). These tell us how the site itself slices the data.
    print("=== Filter / facet hints ===")
    for tag in soup.find_all(["select", "form"], limit=20):
        print(f"  <{tag.name} class={tag.get('class')} id={tag.get('id')} action={tag.get('action')}>")

    # 3. Look for pagination / load-more.
    print("\n=== Pagination hints ===")
    pag = soup.find_all(string=re.compile(r"load more|page|next|previous|suivant", re.I))
    for p in pag[:20]:
        text = p.strip()
        if text:
            print(f"  text: {text[:120]!r}")

    # 4. Total count if displayed.
    print("\n=== Total count hints ===")
    for m in re.finditer(r"(\d+)\s*(jobs?|opportunit|offres?|positions?|opening)", html, re.I):
        print(f"  {m.group(0)!r} at offset {m.start()}")


if __name__ == "__main__":
    main()
