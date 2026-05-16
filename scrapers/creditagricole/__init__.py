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
#   creditagricole.sofinco    -> cacf.talentview.io      (Talentview SPA)
#   creditagricole.bforbank   -> groupecreditagricole.jobs filtered to BforBank
#   creditagricole.assurances -> groupecreditagricole.jobs filtered to CA Assurances
#
# The five Talentsoft tenants share the same engine — see _talentsoft.py for
# the shared crawl helper. Dassault Aviation also runs Talentsoft but predates
# this helper and is kept self-contained.
