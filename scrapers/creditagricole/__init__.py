# Crédit Agricole group scrapers. Each subsidiary is registered under its own
# COMPANY_NAMES key (e.g. `creditagricole.amundi`) and scraped independently
# because the group operates 8+ distinct careers websites running on different
# ATSes:
#
#   creditagricole.carecrute  -> ca-recrute.fr           (Teamtailor RSS — aggregates
#                                                          ~30 regional caisses +
#                                                          CAMCA, CA Business Digital,
#                                                          CA Titres, Doxallia, IFCAM,
#                                                          FNCA, CA Technologies)
#   creditagricole.amundi     -> jobs.amundi.com         (Talentsoft)
#   creditagricole.lcl        -> offres-emploi.lcl.com   (Talentsoft)
#   creditagricole.cacib      -> jobs.ca-cib.com         (Talentsoft)
#   creditagricole.caceis     -> jobs.caceis.com         (Talentsoft)
#   creditagricole.indosuez   -> jobs.ca-indosuez.com    (Talentsoft)
#   creditagricole.sofinco    -> groupecreditagricole.jobs / credit-agricole-personal-finance-mobility
#   creditagricole.bforbank   -> groupecreditagricole.jobs / bforbank
#   creditagricole.assurances -> groupecreditagricole.jobs / credit-agricole-assurances
#
# Sofinco's own site (cacf.talentview.io) is a Talentview SPA with no public
# JSON API; BforBank's own site (welcometothejungle.com/.../bforbank) sits
# behind Cloudflare. groupecreditagricole.jobs surfaces all three with
# server-rendered listings, so we scrape there.
#
# The five Talentsoft tenants share the same engine — see _talentsoft.py for
# the shared crawl helper. The three groupeca-scraped brands share
# _groupeca.py. Dassault Aviation also runs Talentsoft but predates the
# helper and is kept self-contained.
