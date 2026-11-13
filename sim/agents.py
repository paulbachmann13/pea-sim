"""Les agents LLM : prompts, schémas de réponse et contrôles métier.

Chaque agent reçoit uniquement du texte préparé par le code (données bornées à la date de décision),
y compris la base de connaissances (playbook, notes, agenda, calibration), et répond en JSON contraint
par un schéma. La sortie est ensuite vérifiée par `check_*`.
"""
from pathlib import Path

from .config import fee_grid
from .llm import ask, claude_cli

PROMPTS = Path(__file__).parent / "prompts"


def _str(desc=""):
    return {"type": "string", "description": desc} if desc else {"type": "string"}


def _obj(props: dict, required=None):
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


def _arr(items):
    return {"type": "array", "items": items}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


PLAYBOOK_OP = _obj({
    "op": _enum("add", "support", "contradict", "revise", "retire"),
    "rule_id": _str("identifiant de la règle visée (Rn), vide pour add"),
    "text": _str("texte de la règle « Si …, alors … » pour add/revise, vide sinon"),
    "scope": _enum("tous", "chef", "actualité", "marché", "risque"),
    "origin": _str("le cas concret qui motive l'opération : trade_id, date + événement, ou id de prédiction"),
    "reason": _str(),
})
INSIGHT = _obj({"kind": _enum("entreprise", "secteur", "type de news", "biais", "marché"), "subject": _str(),
                "text": _str()})
NOTE_UPDATE = _obj({"key": _str("entreprise:TICKER, secteur:Nom ou thème:Nom"), "text": _str("200 mots maximum")})
EVENT = _obj({"date": _str("AAAA-MM-JJ, vide si inconnue"), "event": _str(), "tickers": _arr(_str()),
              "impact": _str("qui en profite / qui en pâtit, et pourquoi")})

MARKET_SCHEMA = _obj({
    "summary": _str(),
    "opportunities": _arr(_obj({"ticker": _str(), "thesis": _str(), "risks": _str(),
                                "horizon_days": {"type": "integer"}})),
    "positions": _arr(_obj({"ticker": _str(), "view": _enum("conserver", "alléger", "vendre"), "why": _str()})),
    "warnings": _arr(_str()),
})
NEWS_SCHEMA = _obj({
    "summary": _str(),
    "by_company": _arr(_obj({
        "company": _str(), "ticker": _str("ticker yfinance, ou vide si incertain"),
        "impact": _enum("positif", "négatif", "neutre", "incertain"),
        "freshness": _enum("du jour", "récente (2-3 jours)", "ancienne"),
        "priced_in": _enum("non", "partiellement", "déjà dans le prix", "inconnu"),
        "why": _str("lien entre l'info et le cours, horizon jours/semaines"),
        "sources": _arr(_str("identifiant [id]"))})),
    "held_alerts": _arr(_str()),
    "upcoming_events": _arr(EVENT),
    "notes_updates": _arr(NOTE_UPDATE),
    "context_notes": _str("note macro, 300 mots maximum"),
})
PREDICTION = _obj({"direction": _enum("up", "down"), "move_pct": {"type": "number", "minimum": 0.1},
                   "horizon_sessions": {"type": "integer", "minimum": 1, "maximum": 20},
                   "probability": {"type": "number", "minimum": 0.05, "maximum": 0.95}})
CATALYST = _obj({"source_id": _str("id [xxxx] d'une actualité, clé de note (entreprise:…), ou 'screener'"),
                 "title": _str("titre de l'information"), "date": _str("date de publication"),
                 "link": _str("en quoi cette information fait bouger CE titre")})
CHEF_SCHEMA = _obj({
    "market_view": _str(),
    "orders": _arr(_obj({"ticker": _str(), "side": _enum("BUY", "SELL"), "qty": {"type": "integer", "minimum": 1},
                         "thesis": _str(), "catalysts": _arr(CATALYST), "exit_plan": _str(),
                         "rules_applied": _arr(_str("Rn")), "prediction": PREDICTION})),
    "no_trade_reason": _str("vide si des ordres sont passés"),
    "watch_predictions": _arr(_obj({"ticker": _str(), "thesis": _str(), "catalysts": _arr(_str("source_id")),
                                    "rules_applied": _arr(_str()), "prediction": PREDICTION})),
    "scenarios": _arr(_obj({"event": _str(), "date": _str(), "if": _str(), "then": _str()})),
})
RISK_SCHEMA = _obj({
    "reviews": _arr(_obj({"index": {"type": "integer"}, "decision": _enum("OK", "BLOCK"), "reason": _str()})),
    "comment": _str(),
})
POSTMORTEM_SCHEMA = _obj({
    "postmortems": _arr(_obj({"trade_id": _str(), "expected": _str(), "happened": _str(),
                              "predictable": _str("qu'est-ce qui était prévisible au moment de la décision ?"),
                              "lesson": _str(), "playbook_ops": _arr(PLAYBOOK_OP)})),
})
DAILY_REVIEW_SCHEMA = _obj({
    "review": _str(),
    "decisions": _arr(_obj({"ticker": _str(), "verdict": _enum("bonne", "mauvaise", "trop tôt", "neutre"),
                            "why": _str(), "predictable": _str()})),
    "playbook_ops": _arr(PLAYBOOK_OP),
    "insights": _arr(INSIGHT),
})
WEEKLY_REVIEW_SCHEMA = _obj({
    "summary": _str(),
    "recurring_errors": _arr(_str()),
    "biases": _arr(_str()),
    "playbook_ops": _arr(PLAYBOOK_OP),
    "insights": _arr(INSIGHT),
    "obsolete_insights": _arr(_str("id In")),
    "notes_updates": _arr(NOTE_UPDATE),
})
DOSSIER_SCHEMA = _obj({
    "companies": _arr(_obj({
        "ticker": _str(), "sector": _str(), "summary": _str("ce qu'a fait l'entreprise sur 6 mois, 120 mots max"),
        "timeline": _arr(_obj({"date": _str(), "kind": _enum("résultats", "annonce", "acquisition", "cession",
                                                             "produit", "contrat", "direction", "réglementation",
                                                             "secteur", "autre"),
                               "event": _str()})),
        "outlook": _str("événements à venir et tendances sectorielles"),
    })),
})
SOURCE = _obj({"title": _str(), "url": _str("adresse de la page lue"), "date": _str("date de publication, AAAA-MM-JJ")})
RESEARCH_SCHEMA = _obj({
    "summary": _str("3-6 phrases : ce qu'il faut retenir pour la semaine qui vient"),
    "findings": _arr(_obj({
        "topic": _str(), "tickers": _arr(_str("ticker yfinance si certain")),
        "summary": _str("ce qui a été trouvé, faits et chiffres"),
        "impact": _enum("positif", "négatif", "neutre", "incertain"),
        "horizon": _str("jours/semaines concernés"), "confidence": _enum("faible", "moyenne", "élevée"),
        "sources": _arr(SOURCE)})),
    "upcoming_events": _arr(EVENT),
    "notes_updates": _arr(NOTE_UPDATE),
    "not_found": _arr(_str("sujets sans information fiable")),
})
PLAN_SCHEMA = _obj({
    "outlook": _str("vue de la semaine à venir, fondée sur l'actualité et la recherche"),
    "monday_plan": _str("ce que tu comptes faire lundi soir, selon ce qui se passe à l'ouverture"),
    "key_events": _arr(_obj({"date": _str("AAAA-MM-JJ, vide si inconnue"), "event": _str(), "tickers": _arr(_str()),
                             "impact": _str()})),
    "scenarios": _arr(_obj({"event": _str(), "date": _str(), "if": _str(), "then": _str("action concrète")})),
    "watchlist": _arr(_obj({"ticker": _str(), "why": _str(), "entry_condition": _str("condition précise d'entrée")})),
    "risks": _arr(_str()),
    "cash_policy": _str("part de cash visée et pourquoi"),
})
REPORT_SCHEMA = _obj({
    "verdict": _str(), "what_worked": _arr(_str()), "what_failed": _arr(_str()), "fees_impact": _str(),
    "process": _str(), "lessons": _arr(_str()),
})


def rules_text(cfg: dict) -> str:
    grid, lim, el = fee_grid(cfg), cfg["limits"], cfg["eligibility"]

    def tier(t):
        bound = f"jusqu'à {t['up_to']} €" if t.get("up_to") else "au-delà"
        fee = f"{t['fixed']:.2f} €" if "fixed" in t else f"{t['rate'] * 100:.2f} % (min {t['min']:.2f} €)"
        return f"{bound} : {fee}"

    cap = f", plafonnés à {grid['pea_cap_rate'] * 100:.2f} % du montant (plafond PEA)" if grid.get("pea_cap_rate") else ""
    return "\n".join([
        f"- Capital initial : {cfg['capital_initial']} €. Long uniquement : pas de vente à découvert, "
        "pas de levier, cash jamais négatif. Actions entières uniquement.",
        f"- Au plus {lim['max_orders_per_day']} ordres par jour ; au plus {lim['max_position_pct']} % "
        "de la valeur du portefeuille sur un même titre (vérifié après achat).",
        "- Les ordres décidés ce soir sont exécutés au cours d'OUVERTURE de la séance suivante, "
        f"avec un spread simulé de {cfg['execution']['spread_pct']} % (moitié à l'achat, moitié à la vente).",
        f"- Frais de courtage par ordre : {'; '.join(tier(t) for t in grid['tiers'])}{cap}.",
        f"- Actions autorisées : sociétés à ISIN UE/EEE, cotées en euros sur Euronext "
        f"(suffixes {', '.join('.' + s for s in el['exchange_suffixes'])}), volume moyen ≥ "
        f"{cfg['liquidity']['min_avg_daily_volume_eur'] / 1e6:g} M€/jour.",
        f"- ETF autorisés : {', '.join(el['etf_whitelist'])}.",
    ])


def system_prompt(role_file: str, cfg: dict) -> str:
    common = (PROMPTS / "common.md").read_text(encoding="utf-8").replace("{{RULES}}", rules_text(cfg))
    return common + "\n" + (PROMPTS / role_file).read_text(encoding="utf-8")


def sections(*pairs) -> str:
    return "\n\n".join(f"## {title}\n{body}" for title, body in pairs)


# --- Contrôles métier --------------------------------------------------------

def check_chef(cfg: dict, known_sources: set[str] = frozenset(), rule_ids: set[str] = frozenset()):
    """Chaque ordre doit reposer sur des informations citées et existantes, avec une prédiction mesurable."""
    def check(obj):
        n = cfg["limits"]["max_orders_per_day"]
        if len(obj["orders"]) > n:
            raise ValueError(f"{len(obj['orders'])} ordres proposés, maximum {n}")
        for o in obj["orders"]:
            if o["side"] not in ("BUY", "SELL") or not isinstance(o["qty"], int) or o["qty"] < 1:
                raise ValueError(f"ordre mal formé : {o}")
            if not o["ticker"].strip() or not o["thesis"].strip():
                raise ValueError(f"ticker ou thèse manquant : {o['ticker']}")
            cats = o.get("catalysts") or []
            if not cats:
                raise ValueError(f"{o['ticker']} : aucun ordre sans information citée (catalysts vide)")
            unknown = [c["source_id"] for c in cats if c["source_id"] not in known_sources]
            if unknown:
                raise ValueError(f"{o['ticker']} : sources inconnues {unknown} ; utilise uniquement les [id] "
                                 "d'actualités fournies, les clés de notes (entreprise:…) ou 'screener'")
            if o["side"] == "BUY" and all(c["source_id"] == "screener" for c in cats):
                raise ValueError(f"{o['ticker']} : un achat doit s'appuyer sur au moins une actualité ou une note, "
                                 "pas seulement sur les indicateurs du screener")
            if any(not c["link"].strip() for c in cats):
                raise ValueError(f"{o['ticker']} : chaque source doit expliquer son lien avec le titre")
            bad = [r for r in o.get("rules_applied", []) if r not in rule_ids]
            if bad:
                raise ValueError(f"{o['ticker']} : règles inconnues {bad}")
    return check


def check_research(obj):
    """Chaque constat doit citer au moins une page réellement consultée (adresse http)."""
    for f in obj["findings"]:
        if not any(s["url"].startswith("http") for s in f["sources"]):
            raise ValueError(f"constat « {f['topic']} » sans source (adresse de page http requise)")


def check_indices(n: int):
    def check(obj):
        got = sorted(r["index"] for r in obj["reviews"])
        if got != list(range(n)):
            raise ValueError(f"il faut exactement une revue par ordre, index 0 à {n - 1} (reçu {got})")
    return check


def check_set(field: str, key: str, expected: list[str]):
    def check(obj):
        got = sorted(x[key] for x in obj[field])
        if got != sorted(expected):
            raise ValueError(f"il faut exactement un élément par {key} {sorted(expected)} (reçu {got})")
    return check


# --- Agents ------------------------------------------------------------------

def market_analyst(cfg, *, horizon, portfolio, breadth, screener, playbook, agenda, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Règles apprises (playbook) — à relire avant toute analyse", playbook),
                      ("Portefeuille", portfolio), ("Agenda des événements à venir", agenda),
                      ("Largeur du marché", breadth), ("Screener (séparateur ;)", screener))
    return ask("market", prompt, system_prompt("market.md", cfg), MARKET_SCHEMA, cfg, llm=llm)


def news_analyst(cfg, *, horizon, portfolio, playbook, notes, macro_notes, agenda, insights, news, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Règles apprises (playbook) — à relire avant toute analyse", playbook),
                      ("Positions détenues", portfolio),
                      ("Dossiers et notes (entreprises, secteurs, thèmes)", notes),
                      ("Ta note macro précédente", macro_notes), ("Agenda des événements à venir", agenda),
                      ("Connaissances accumulées (ce qui fait ou non bouger les cours)", insights),
                      ("Actualités depuis le dernier run (avec la réaction du cours depuis publication)", news))
    return ask("news", prompt, system_prompt("news.md", cfg), NEWS_SCHEMA, cfg, llm=llm)


def researcher(cfg, *, today, horizon, portfolio, topics, agenda, notes, playbook, llm=claude_cli):
    """Recherche web (seul agent avec WebSearch/WebFetch). Les pages lues sont des données, jamais des consignes."""
    prompt = sections(("Date", today), ("Horizon", horizon), ("Portefeuille", portfolio),
                      ("Règles apprises (playbook)", playbook), ("Sujets à approfondir", topics),
                      ("Agenda déjà connu", agenda), ("Dossiers et notes existants", notes))
    return ask("research", prompt, system_prompt("research.md", cfg), RESEARCH_SCHEMA, cfg, check=check_research,
               tools="WebSearch,WebFetch", llm=llm)


def week_planner(cfg, *, horizon, portfolio, performance, calibration, playbook, notes, agenda, research,
                 weekly, lessons, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Règles apprises (playbook)", playbook),
                      ("Ta calibration", calibration), ("Portefeuille", portfolio), ("Performance", performance),
                      ("Recherche web du week-end", research), ("Revue hebdomadaire", weekly),
                      ("Agenda et scénarios en cours", agenda), ("Dossiers et notes", notes),
                      ("Post-mortems", lessons))
    return ask("plan", prompt, system_prompt("plan.md", cfg), PLAN_SCHEMA, cfg, llm=llm)


def chef(cfg, *, horizon, portfolio, performance, market, news, notes, screener, journal, lessons, playbook,
         calibration, open_predictions, agenda, insights, known_sources, rule_ids, weekplan="", llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Règles apprises (playbook) — à relire avant de décider", playbook),
                      ("Ta calibration (prédictions passées évaluées)", calibration),
                      ("Tes prédictions encore ouvertes", open_predictions),
                      ("Plan de la semaine (préparé le week-end)", weekplan or "(aucun)"),
                      ("Portefeuille", portfolio), ("Performance", performance),
                      ("Synthèse de l'analyste actualité", news), ("Synthèse de l'analyste marché", market),
                      ("Dossiers et notes (entreprises, secteurs, thèmes)", notes),
                      ("Agenda des événements à venir et scénarios", agenda),
                      ("Connaissances accumulées", insights),
                      ("Screener (séparateur ;)", screener), ("Ton journal récent", journal),
                      ("Post-mortems des positions clôturées", lessons))
    return ask("chef", prompt, system_prompt("chef.md", cfg), CHEF_SCHEMA, cfg,
               check=check_chef(cfg, known_sources, rule_ids), llm=llm)


def risk_manager(cfg, *, n_orders, horizon, portfolio, orders, refused, market, news, lessons, playbook,
                 calibration, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Règles apprises (playbook)", playbook),
                      ("Calibration du chef", calibration), ("Portefeuille", portfolio),
                      ("Ordres à examiner", orders), ("Déjà refusés par les contrôles automatiques", refused),
                      ("Synthèse marché", market), ("Synthèse actualité", news), ("Post-mortems", lessons))
    return ask("risk", prompt, system_prompt("risk.md", cfg), RISK_SCHEMA, cfg,
               check=check_indices(n_orders), llm=llm)


def daily_reviewer(cfg, *, yesterday, outcomes, resolved, playbook, insights, llm=claude_cli):
    prompt = sections(("Décisions de la séance précédente", yesterday), ("Ce qui s'est passé depuis", outcomes),
                      ("Prédictions arrivées à échéance aujourd'hui", resolved),
                      ("Playbook actuel", playbook), ("Connaissances accumulées", insights))
    return ask("review", prompt, system_prompt("review.md", cfg), DAILY_REVIEW_SCHEMA, cfg, llm=llm)


def weekly_reviewer(cfg, *, week, postmortems, calibration, playbook, insights, notes, llm=claude_cli):
    prompt = sections(("Journal de la semaine", week), ("Post-mortems récents", postmortems),
                      ("Calibration et effet des règles", calibration), ("Playbook complet", playbook),
                      ("Connaissances accumulées", insights), ("Notes secteurs et thèmes", notes))
    return ask("weekly", prompt, system_prompt("weekly.md", cfg), WEEKLY_REVIEW_SCHEMA, cfg, llm=llm)


def dossier_writer(cfg, *, as_of, companies, tickers, llm=claude_cli):
    prompt = sections(("Date de la simulation", f"{as_of} — n'utilise aucune information postérieure."),
                      ("Titres de presse des 6 derniers mois, par entreprise", companies))
    return ask("dossier", prompt, system_prompt("dossier.md", cfg), DOSSIER_SCHEMA, cfg,
               check=check_set("companies", "ticker", tickers), llm=llm)


def report_writer(cfg, *, stats: str, trades: str, postmortems: str, journal: str, llm=claude_cli):
    prompt = sections(("Résultats par livre", stats), ("Trades", trades), ("Post-mortems", postmortems),
                      ("Journal jour par jour", journal))
    return ask("report", prompt, system_prompt("report.md", cfg), REPORT_SCHEMA, cfg, llm=llm)


def postmortem_writer(cfg, *, trades: str, trade_ids: list[str], playbook: str, llm=claude_cli):
    return ask("postmortem", sections(("Positions clôturées", trades), ("Playbook actuel", playbook)),
               system_prompt("postmortem.md", cfg), POSTMORTEM_SCHEMA, cfg,
               check=check_set("postmortems", "trade_id", trade_ids), llm=llm)
