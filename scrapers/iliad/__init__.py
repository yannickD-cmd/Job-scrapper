# Groupe iliad's French entities span TWO ATSes, scraped here as separate
# `scrape()` entry points so each can be registered in run.py / CI under its own
# COMPANY_NAMES key:
#
#   iliad.france    -> SmartRecruiters tenant "Iliad-Free". Covers Free,
#                      Free Pro, Stancer, Opcore and Itrust in one board
#                      (the `Brands` customField separates them).
#   iliad.scaleway  -> Scaleway, the cloud subsidiary, on its own Lever board.
#
# `recrutement.iliad.fr` fronts the SmartRecruiters tenant with a Beekome career
# site (a Nuxt SPA). Beekome is only a proxy — it pages 10 at a time and drops
# the customField facets — so france.py hits SmartRecruiters directly. Beekome
# probe artifacts are kept in material/ purely for reference.
#
# Out of scope (France-only, locked with the user): iliad italia
# (corporate.iliad.it) and Play Polska (kariera.play.pl).
