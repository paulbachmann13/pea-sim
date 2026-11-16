"""Fiches des titres : « qu'est-ce que c'est ? » en français simple, affichées partout dans la PWA.

- ETF : fiches rédigées à la main (ETF_PROFILES), car yfinance ne décrit pas leur contenu.
- Actions : yfinance fournit nom, secteur, pays et un résumé d'activité (en anglais). Une fiche de base (nom, domaine,
  pays en français) est créée sans LLM ; l'agent « fiches » la complète ensuite avec une description en français
  simple, uniquement à partir de ces faits. Les fiches sont conservées dans knowledge/profiles.json.
"""
from . import agents
from .knowledge import Knowledge
from .llm import claude_cli

ETF_NOTE = ("Un ETF est un « panier » d'actions qu'on achète en un seul titre, comme une action. "
            "Éligible au PEA, il se négocie en bourse comme une action.")
ETF_PROFILES = {
    "WPEA.PA": {"name": "iShares MSCI World Swap PEA", "domain": "Actions du monde entier",
                "short": "ETF Monde : environ 1 400 grandes entreprises de pays développés (≈ 70 % USA)",
                "long": ETF_NOTE + " Celui-ci reproduit l'indice MSCI World : environ 1 400 grandes et moyennes entreprises "
                        "de pays développés, dont environ 70 % américaines (Apple, Microsoft, Nvidia…). C'est le « marché "
                        "mondial » en un seul achat, et le benchmark de cette simulation."},
    "CW8.PA": {"name": "Amundi MSCI World", "domain": "Actions du monde entier",
               "short": "ETF Monde (Amundi) : même indice que WPEA, environ 1 400 entreprises de pays développés",
               "long": ETF_NOTE + " Il reproduit le même indice MSCI World que WPEA : environ 1 400 grandes entreprises de "
                       "pays développés, surtout américaines. C'est l'ETF Monde le plus connu du PEA, mais une part coûte "
                       "plusieurs centaines d'euros, ce qui le rend peu pratique avec un petit portefeuille."},
    "PAEEM.PA": {"name": "Amundi PEA Marchés émergents", "domain": "Actions des pays émergents",
                 "short": "ETF Marchés émergents : grandes entreprises de Chine, Inde, Taïwan, Corée du Sud…",
                 "long": ETF_NOTE + " Celui-ci reproduit l'indice MSCI Emerging Markets : de grandes entreprises de pays en "
                         "développement rapide (Chine, Inde, Taïwan, Corée du Sud notamment). Plus risqué et plus volatil "
                         "qu'un ETF Monde."},
    "PUST.PA": {"name": "Amundi PEA Nasdaq-100", "domain": "Actions américaines, technologie",
                "short": "ETF Nasdaq-100 : 100 grandes entreprises américaines, surtout technologiques",
                "long": ETF_NOTE + " Celui-ci reproduit le Nasdaq-100 : les 100 plus grandes entreprises (hors banques) "
                        "cotées au Nasdaq, très tournées vers la technologie (Apple, Microsoft, Nvidia, Amazon…). "
                        "Il bouge beaucoup avec l'actualité de l'intelligence artificielle."},
    "ESE.PA": {"name": "BNP Paribas Easy S&P 500", "domain": "Actions américaines",
               "short": "ETF S&P 500 : les 500 plus grandes entreprises américaines",
               "long": ETF_NOTE + " Celui-ci reproduit l'indice S&P 500 : les 500 plus grandes entreprises américaines "
                       "(Apple, Microsoft, Nvidia, Amazon…). C'est surtout un pari sur l'économie des États-Unis."},
    "C40.PA": {"name": "Amundi CAC 40", "domain": "Actions françaises",
               "short": "ETF CAC 40 : les 40 plus grandes entreprises cotées à Paris",
               "long": ETF_NOTE + " Celui-ci reproduit le CAC 40 : les 40 plus grandes entreprises cotées à Paris "
                       "(TotalEnergies, LVMH, Airbus, Sanofi…)."},
}
SECTOR_FR = {"Energy": "Énergie", "Healthcare": "Santé", "Industrials": "Industrie", "Financial Services": "Finance",
             "Technology": "Technologie", "Consumer Cyclical": "Consommation (biens non essentiels)",
             "Consumer Defensive": "Consommation (biens essentiels)", "Basic Materials": "Matériaux",
             "Communication Services": "Communication et médias", "Real Estate": "Immobilier",
             "Utilities": "Services aux collectivités"}
COUNTRY_FR = {"France": "France", "Belgium": "Belgique", "Netherlands": "Pays-Bas", "Italy": "Italie",
              "Portugal": "Portugal", "Ireland": "Irlande", "Germany": "Allemagne", "Spain": "Espagne",
              "Luxembourg": "Luxembourg", "Finland": "Finlande", "Austria": "Autriche", "Norway": "Norvège",
              "Switzerland": "Suisse", "United Kingdom": "Royaume-Uni", "United States": "États-Unis"}


def fetch_info(ticker: str) -> dict:
    """Nom, secteur, pays et résumé d'activité (anglais) d'une société, via yfinance."""
    import yfinance as yf
    i = yf.Ticker(ticker).info
    return {"name": i.get("longName") or i.get("shortName") or ticker, "sector": i.get("sector") or "",
            "industry": i.get("industry") or "", "country": i.get("country") or "",
            "summary": (i.get("longBusinessSummary") or "")[:800]}


def basic_profile(ticker: str, info: dict) -> dict:
    sector = SECTOR_FR.get(info["sector"], info["sector"]) or "Secteur non précisé"
    country = COUNTRY_FR.get(info["country"], info["country"])
    return {"name": info["name"], "kind": "Action", "domain": sector, "country": country,
            "short": f"{info['name']} — {sector}" + (f" ({country})" if country else ""),
            "long": "", "source": info}


def etf_profiles() -> dict:
    return {t: {**p, "kind": "ETF", "country": ""} for t, p in ETF_PROFILES.items()}


def refresh_profiles(cfg: dict, kb: Knowledge, tickers: list[str], day: str, llm=claude_cli, fetch=fetch_info,
                     max_llm: int = 25) -> tuple[int, list[dict]]:
    """Crée les fiches de base manquantes, puis fait écrire en français simple celles qui n'ont pas de description.
    Renvoie (nombre de fiches de base créées, appels LLM). Les fiches de base existent même si l'appel LLM est
    économisé (budget) ou échoue : l'exception est alors propagée à l'appelant, qui la traite."""
    stocks = [t for t in dict.fromkeys(tickers) if t not in ETF_PROFILES]
    built = 0
    for t in stocks:
        if t in kb.profiles():
            continue
        try:
            kb.set_profile(t, basic_profile(t, fetch(t)), day)
            built += 1
        except Exception:                                  # yfinance capricieux : on réessaiera au prochain run
            continue
    profs = kb.profiles()
    need = [t for t in stocks if t in profs and not profs[t].get("long")][:max_llm]
    if not need:
        return built, []
    companies = "\n\n".join(
        f"### {t}\nNom : {profs[t]['name']}\nSecteur : {profs[t]['source']['sector']} / {profs[t]['source']['industry']}\n"
        f"Pays : {profs[t]['source']['country']}\nActivité (anglais) : {profs[t]['source']['summary'] or '(non fournie)'}"
        for t in need)
    obj, metas = agents.profile_writer(cfg, companies=companies, tickers=need, llm=llm)
    kb.update_profiles(obj["profiles"], day)
    return built, metas
