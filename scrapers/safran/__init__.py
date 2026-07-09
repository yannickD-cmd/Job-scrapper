# Two distinct Safran career boards live under this package, scraped as separate
# `scrape()` entry points so each is registered in run.py / CI under its own
# COMPANY_NAMES key:
#
#   safran.group -> Safran (group Drupal board, WAF-blocked from CI — run locally)
#   safran.ai    -> Safran.AI (ex-Preligens, AI/defense; public Lever board, CI-safe)
#
# There is deliberately no `from . import scrape` here: importing the package must
# not pull in either board's dependencies (group.py needs curl_cffi/bs4). run.py
# always imports the dotted board module directly (scrapers.safran.group / .ai).
