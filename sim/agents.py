"""Les agents LLM : prompts, schémas de réponse et contrôles métier.

Chaque agent reçoit uniquement du texte préparé par le code (données bornées à la date de décision)
et répond en JSON contraint par un schéma. La sortie est ensuite vérifiée par `check_*`.
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


MARKET_SCHEMA = _obj({
    "summary": _str(),
    "opportunities": _arr(_obj({"ticker": _str(), "thesis": _str(), "risks": _str(),
                                "horizon_days": {"type": "integer"}})),
    "positions": _arr(_obj({"ticker": _str(), "view": {"type": "string", "enum": ["conserver", "alléger", "vendre"]},
                            "why": _str()})),
    "warnings": _arr(_str()),
})
NEWS_SCHEMA = _obj({
    "summary": _str(),
    "by_company": _arr(_obj({"company": _str(), "ticker": _str("ticker yfinance, ou vide si incertain"),
                             "impact": {"type": "string", "enum": ["positif", "négatif", "neutre", "incertain"]},
                             "why": _str(), "sources": _arr(_str("identifiant [id]"))})),
    "held_alerts": _arr(_str()),
    "context_notes": _str("300 mots maximum"),
})
CHEF_SCHEMA = _obj({
    "market_view": _str(),
    "orders": _arr(_obj({"ticker": _str(), "side": {"type": "string", "enum": ["BUY", "SELL"]},
                         "qty": {"type": "integer", "minimum": 1}, "thesis": _str(),
                         "sources": _arr(_str()), "exit_plan": _str()})),
    "no_trade_reason": _str("vide si des ordres sont passés"),
})
RISK_SCHEMA = _obj({
    "reviews": _arr(_obj({"index": {"type": "integer"}, "decision": {"type": "string", "enum": ["OK", "BLOCK"]},
                          "reason": _str()})),
    "comment": _str(),
})
POSTMORTEM_SCHEMA = _obj({
    "postmortems": _arr(_obj({"trade_id": _str(), "expected": _str(), "happened": _str(), "lesson": _str()})),
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

def check_chef(cfg: dict):
    def check(obj):
        n = cfg["limits"]["max_orders_per_day"]
        if len(obj["orders"]) > n:
            raise ValueError(f"{len(obj['orders'])} ordres proposés, maximum {n}")
        for o in obj["orders"]:
            if o["side"] not in ("BUY", "SELL") or not isinstance(o["qty"], int) or o["qty"] < 1:
                raise ValueError(f"ordre mal formé : {o}")
            if not o["ticker"].strip() or not o["thesis"].strip():
                raise ValueError(f"ticker ou thèse manquant : {o}")
    return check


def check_indices(n: int):
    def check(obj):
        got = sorted(r["index"] for r in obj["reviews"])
        if got != list(range(n)):
            raise ValueError(f"il faut exactement une revue par ordre, index 0 à {n - 1} (reçu {got})")
    return check


def check_trade_ids(ids: list[str]):
    def check(obj):
        got = sorted(p["trade_id"] for p in obj["postmortems"])
        if got != sorted(ids):
            raise ValueError(f"il faut un post-mortem par trade_id {sorted(ids)} (reçu {got})")
    return check


# --- Agents ------------------------------------------------------------------

def market_analyst(cfg, *, horizon, portfolio, breadth, screener, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Portefeuille", portfolio), ("Largeur du marché", breadth),
                      ("Screener (séparateur ;)", screener))
    return ask("market", prompt, system_prompt("market.md", cfg), MARKET_SCHEMA, cfg, llm=llm)


def news_analyst(cfg, *, horizon, portfolio, notes, news, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Positions détenues", portfolio),
                      ("Tes notes de contexte précédentes", notes), ("Actualités depuis le dernier run", news))
    return ask("news", prompt, system_prompt("news.md", cfg), NEWS_SCHEMA, cfg, llm=llm)


def chef(cfg, *, horizon, portfolio, performance, market, news, screener, journal, lessons, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Portefeuille", portfolio), ("Performance", performance),
                      ("Synthèse de l'analyste marché", market), ("Synthèse de l'analyste actualité", news),
                      ("Screener (séparateur ;)", screener), ("Ton journal récent", journal),
                      ("Leçons des positions clôturées", lessons))
    return ask("chef", prompt, system_prompt("chef.md", cfg), CHEF_SCHEMA, cfg, check=check_chef(cfg), llm=llm)


def risk_manager(cfg, *, n_orders, horizon, portfolio, orders, refused, market, news, lessons, llm=claude_cli):
    prompt = sections(("Horizon", horizon), ("Portefeuille", portfolio), ("Ordres à examiner", orders),
                      ("Déjà refusés par les contrôles automatiques", refused),
                      ("Synthèse marché", market), ("Synthèse actualité", news), ("Leçons", lessons))
    return ask("risk", prompt, system_prompt("risk.md", cfg), RISK_SCHEMA, cfg,
               check=check_indices(n_orders), llm=llm)


def report_writer(cfg, *, stats: str, trades: str, postmortems: str, journal: str, llm=claude_cli):
    prompt = sections(("Résultats par livre", stats), ("Trades", trades), ("Post-mortems", postmortems),
                      ("Journal jour par jour", journal))
    return ask("report", prompt, system_prompt("report.md", cfg), REPORT_SCHEMA, cfg, llm=llm)


def postmortem_writer(cfg, *, trades: str, trade_ids: list[str], llm=claude_cli):
    return ask("postmortem", sections(("Positions clôturées", trades)), system_prompt("postmortem.md", cfg),
               POSTMORTEM_SCHEMA, cfg, check=check_trade_ids(trade_ids), llm=llm)
