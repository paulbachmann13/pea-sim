"""Orchestration du run quotidien (18h, Europe/Paris).

Jour de séance :
  1. démarrage de la simulation si besoin (dépôt de 1 000 € dans chaque livre)
  2. mise à jour des données de marché
  3. pour chaque séance depuis le dernier run : dividendes, puis exécution des ordres en attente
     à l'ouverture (agents, livre fantôme low-cost, et au 1er jour : benchmark + portefeuille aléatoire)
  4. valorisation de tous les livres à la clôture
  5. post-mortems des positions clôturées
  6. décision du soir ; le dernier jour de chaque mois de simulation, bilan mensuel (la simulation ne s'arrête jamais)
  7. export du dashboard + publication
Week-end / férié : veille (actualités, notes de contexte), aucun ordre.

Livres comptables (même base, isolés) :
  agents          les ordres de l'équipe, grille de frais active
  agents_lowcost  les mêmes ordres, grille de comparaison
  benchmark       ETF Monde acheté au 1er jour et conservé
  random          N titres liquides tirés au sort au 1er jour et conservés
  random_large    idem avec 20 titres (repère statistique plus fiable)
"""
import json
import logging
import os
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from . import market_calendar as mc
from .broker import buy_max, execute_orders
from .config import ROOT, fee_grid
from .data import PriceStore
from .decide import horizon, run_decision, run_postmortems, run_veille
from .dossier import fetch_history
from .knowledge import Knowledge
from .export import export_agents, export_dashboard, export_usage
from .journal import Journal, write_json
from .ledger import Ledger
from .llm import BudgetSkip, claude_cli
from .models import BUY, Order
from .money import D, cents
from .news import NewsStore, fetch_news, select_items
from .profiles import fetch_info
from .publish import publish
from .refresh import refresh_market_data
from .remote_settings import apply_remote_settings, fetch_remote_settings
from .report import build_report
from .screener import screen, select
from .supabase_sync import sync_docs
from .universe import build_instruments, load_universe, prefilter

log = logging.getLogger(__name__)
BOOKS = ["agents", "agents_lowcost", "benchmark", "random", "random_large"]
RANDOM_BOOKS = ("random", "random_large")


@dataclass
class RunResult:
    mode: str                     # trading | veille | skip
    title: str
    message: str
    warnings: list[str] = field(default_factory=list)
    report: int | None = None     # n° du bilan mensuel publié par ce run


@dataclass
class Env:
    cfg: dict
    root: Path
    journal: Journal
    ledgers: dict[str, Ledger]
    kb: Knowledge

    @property
    def agents(self) -> Ledger:
        return self.ledgers["agents"]


def open_env(cfg: dict, root: Path) -> Env:
    db = root / cfg["simulation"]["ledger_db"]
    db.parent.mkdir(parents=True, exist_ok=True)
    return Env(cfg, root, Journal(root / cfg["simulation"]["journal_dir"]), {b: Ledger(db, b) for b in BOOKS},
               Knowledge(root / cfg["knowledge"]["dir"]))


@contextmanager
def run_lock(path: Path, max_age_s: int = 7200):
    """Empêche deux runs simultanés (ex. relance manuelle pendant le run planifié)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        raise RuntimeError(f"un run est déjà en cours (verrou {path})")
    path.write_text(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def report_done(env: Env, month: int) -> bool:
    p = env.journal.root / f"rapport-mois-{month}.json"
    return p.exists() and bool(json.loads(p.read_text(encoding="utf-8")).get("analysis"))


def run(now: datetime, cfg: dict, root: Path = ROOT, *, force: bool = False, llm=claude_cli,
        refresh=refresh_market_data, fetch=fetch_news, fetch_hist=fetch_history,
        fetch_settings=fetch_remote_settings, fetch_profile=fetch_info) -> RunResult:
    applied = apply_remote_settings(cfg, root, fetch_settings)      # mode / pause choisis depuis la PWA
    if applied:
        log.info(applied)
    today = now.date().isoformat()
    env = open_env(cfg, root)
    with run_lock(root / "data" / "run.lock"):
        if mc.is_trading_day(today):
            result = trading_run(env, now, today, force, llm, refresh, fetch, fetch_hist, fetch_profile)
        else:
            result = veille_run(env, now, today, force, llm, fetch, fetch_hist, fetch_profile)
        if result.mode != "skip":
            try:
                publish(cfg, root, f"run {today} ({result.mode})")
            except Exception as e:
                result.warnings.append(f"publication du dashboard : {e}")
            record_status(cfg, root, now, result.mode, result.title, result.warnings)
        return result


def record_status(cfg: dict, root: Path, now: datetime, mode: str, title: str, warnings: list[str],
                  error: str | None = None) -> None:
    """État du dernier run, envoyé au dashboard (bandeau rouge si problème). Indépendant de Claude :
    il reste lisible quand l'alerte par mail ne peut pas partir (ex. quota Claude épuisé)."""
    out = root / "docs" / "data"
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "status.json", {"run_at": now.isoformat(), "mode": mode, "title": title,
                                     "ok": not warnings and not error, "error": error,
                                     "warnings": [w[:300] for w in warnings]})
    try:
        export_agents(cfg, root, warnings)
        export_usage(cfg, root)
    except Exception:
        log.exception("tableau des agents / consommation non exportés")
    try:
        sync_docs(cfg, out, root / "data" / "supabase_sync.json")
    except Exception:
        log.exception("état du run non synchronisé vers Supabase")


# --- Jour de séance ---------------------------------------------------------------

def trading_run(env: Env, now, today, force, llm, refresh, fetch, fetch_hist=fetch_history,
                fetch_profile=None) -> RunResult:
    cfg, j = env.cfg, env.journal
    if j.is_complete(today) and not force:
        return RunResult("skip", "PEA sim", f"Run du {today} déjà effectué.")
    warnings: list[str] = []

    if env.agents.start_date() is None:
        for led in env.ledgers.values():
            led.deposit(today, cfg["capital_initial"])
        log.info("simulation démarrée le %s", today)

    pending = j.load_pending()
    bench = cfg["benchmark"]["ticker"]
    held = {t for led in env.ledgers.values() for t in led.state().positions}
    held |= {o["ticker"] for o in (pending or {}).get("orders", [])} | {bench}
    store, universe, instruments, issues = refresh(cfg, today, held, root=env.root)
    missing = sorted({i.ticker for i in issues if i.blocking and i.ticker in held})
    if missing:
        warnings.append(f"cours manquants aujourd'hui : {', '.join(missing)}")

    executions = process_sessions(env, store, instruments, pending, today, warnings)
    executions = executions or fills_as_executions(env.agents, today)       # run repris après une panne
    snaps = {b: led.snapshot(today, store.marks(list(led.state().positions), today))
             for b, led in env.ledgers.items()}

    pms, pm_changes = [], []
    try:
        pms, pm_changes = run_postmortems(as_of=today, cfg=cfg, ledger=env.agents, store=store, journal=j,
                                          kb=env.kb, llm=llm)
    except BudgetSkip as e:
        log.info("post-mortems reportés (budget) : %s", e.reason)     # retentés au prochain run
    except Exception as e:
        warnings.append(f"post-mortems : {e}")

    h = horizon(env.agents, today, cfg)
    news_items, watch = collect_news(env, store, universe, instruments, now, today, fetch, warnings)
    entry = run_decision(as_of=today, run_at=now, cfg=cfg, store=store, instruments=instruments,
                         ledger=env.agents, journal=j, news_items=news_items, kb=env.kb, watch=watch,
                         bench_summary=snaps["benchmark"], executions=executions, llm=llm,
                         fetch_hist=fetch_hist, profile_fetch=fetch_profile,
                         extra_tickers=sorted({t for led in env.ledgers.values() for t in led.state().positions}))
    if pms or pm_changes:
        entry["learning"]["postmortems"] = [p["trade_id"] for p in pms]
        entry["learning"]["playbook_changes"] = pm_changes + entry["learning"]["playbook_changes"]
    warnings += entry["errors"]

    # Bilan mensuel : le dernier jour de chaque période de `milestone_days` (rattrapé si le run avait été manqué)
    due = [k for k in range(1, h["month"]) if not report_done(env, k)]
    if today == h["milestone"] and not report_done(env, h["month"]):
        due.append(h["month"])
    report = None
    for k in due:
        try:
            build_report(cfg, env.ledgers, store, j, today, llm=llm, month=k)
            report = k
        except BudgetSkip as e:
            log.info("bilan du mois %s reporté (budget) : %s", k, e.reason)
        except Exception as e:
            warnings.append(f"bilan du mois {k} : {e}")
    if report:
        entry["milestone"] = report
    if pms or pm_changes or report:
        j.write_day(entry)
    mode = "trading"

    export_dashboard(cfg, env.root, env.ledgers, j, store, today, universe)
    sync_dashboard(env, warnings)
    return RunResult(mode, *daily_message(env, h, snaps, executions, entry, mode), warnings, report)


def sync_dashboard(env: Env, warnings: list[str]) -> None:
    try:
        sync_docs(env.cfg, env.root / "docs" / "data", env.root / "data" / "supabase_sync.json")
    except Exception as e:
        warnings.append(f"synchro Supabase : {e}")


def process_sessions(env: Env, store, instruments, pending, today, warnings) -> list[dict]:
    """Rejoue chaque séance depuis le dernier run : dividendes (positions détenues à la clôture précédente),
    puis exécution des ordres en attente à l'ouverture de leur date d'exécution."""
    last = env.agents.last_snapshot_date()
    sessions = mc.sessions_between(mc.next_session(last), today) if last else [today]
    executions = []
    for d in sessions:
        credit_dividends(env, store, d)
        if pending and pending["exec_date"] == d:
            executions = execute_pending(env, store, instruments, pending, d, warnings)
            env.journal.clear_pending()
        invest_passive_books(env, store, instruments, d, warnings)
    return executions


def fills_as_executions(led: Ledger, d: str) -> list[dict]:
    """Exécutions du jour relues dans le grand livre (si un run interrompu les avait déjà enregistrées)."""
    return [{"ticker": f.ticker, "side": f.side, "qty_requested": f.qty, "filled": True, "qty": f.qty,
             "price": str(f.price), "fees": str(f.fees), "reason": ""} for f in led.fills() if f.date == d]


def investable(led: Ledger, d: str) -> Decimal:
    """Cash à investir à la séance `d` : seulement s'il y a un versement NOUVEAU (daté avant `d`, au plus tôt le jour
    du dernier achat) ; le petit reste d'un achat précédent n'est pas réinvesti tout seul."""
    fills = led.fills()
    last_fill = max((f.date for f in fills), default=None)
    fresh = any(dd < d and (last_fill is None or dd >= last_fill) for dd, _ in led.deposit_events())
    return led.state().cash - led.deposits_since(d) if fresh else Decimal("0")


def invest_passive_books(env: Env, store, instruments, d: str, warnings: list[str]) -> None:
    """Benchmark (ETF Monde) et portefeuille aléatoire investissent l'argent disponible, y compris les versements
    ultérieurs. Un versement daté de `d` n'est investissable qu'à la séance suivante : c'est la première ouverture
    où les agents (qui décident le soir) peuvent aussi l'employer, donc la comparaison reste équitable."""
    cfg = env.cfg
    minimum = D(cfg["benchmark"].get("min_cash_to_invest", 50))        # pas d'achats de miettes de cash

    bench_led, bench = env.ledgers["benchmark"], cfg["benchmark"]["ticker"]
    avail = investable(bench_led, d)
    if avail >= minimum:
        p = store.price(bench, d, "open")
        if p is None:
            warnings.append(f"benchmark {bench} sans cours d'ouverture le {d}, achat reporté")
        else:
            buy_max(bench_led, bench, p, cfg, fee_grid(cfg), d, cash=avail)
    for book in RANDOM_BOOKS:
        avail = investable(env.ledgers[book], d)
        if avail >= minimum:
            buy_random(env, store, instruments, mc.previous_session(d), d, cash=avail, book=book)


def credit_dividends(env: Env, store, d: str) -> None:
    divs = store.dividends_on(d)
    for led in env.ledgers.values():
        for t in list(led.state().positions):
            if t in divs and not led.has_event("DIVIDEND", t, d):
                led.record_dividend(d, t, divs[t])


def _open_marks(store, led: Ledger, d: str) -> dict:
    """Valorisation à l'ouverture de `d` (sinon dernière clôture connue) pour les contrôles d'exécution."""
    prev = mc.previous_session(d)
    out = {}
    for t in led.state().positions:
        p = store.price(t, d, "open") or store.last_close(t, prev)
        if p is not None:
            out[t] = p
    return out


def execute_pending(env: Env, store, instruments, pending, d, warnings) -> list[dict]:
    cfg = env.cfg
    grid = fee_grid(cfg)
    orders = [Order(o["ticker"], o["side"], o["qty"], o.get("thesis", "")) for o in pending["orders"]]
    opens = {o.ticker: p for o in orders if (p := store.price(o.ticker, d, "open")) is not None}

    outcomes = execute_orders(env.agents, orders, opens, _open_marks(store, env.agents, d), instruments, cfg, grid, d)
    shadow = env.ledgers["agents_lowcost"]
    execute_orders(shadow, orders, opens, _open_marks(store, shadow, d), instruments, cfg,
                   fee_grid(cfg, cfg["simulation"]["compare_grid"]), d)

    return [{"ticker": o.order.ticker, "side": o.order.side, "qty_requested": o.order.qty,
             "filled": o.fill is not None, "qty": o.fill.qty if o.fill else 0,
             "price": str(o.fill.price) if o.fill else None, "fees": str(o.fill.fees) if o.fill else None,
             "reason": o.verdict.reason} for o in outcomes]


def buy_random(env: Env, store, instruments, decision_date: str, d: str, cash=None, book: str = "random") -> None:
    """Tire au sort des actions liquides (connues à la date de décision) et répartit `cash` à parts égales.
    Chaque lot (premier achat, puis chaque versement) a son propre tirage, reproductible.
    `random_large` : beaucoup plus de titres (repère statistique plus fiable), donc sans limite d'ordres par jour
    et avec la grille low-cost (1 € par ordre), sinon les frais fixes de 2,50 € écraseraient des lots de 50 €."""
    cfg, led = env.cfg, env.ledgers[book]
    large = book == "random_large"
    n = cfg["simulation"]["random_large_picks" if large else "random_picks"]
    seed = cfg["simulation"]["random_seed"] + (1000 if large else 0)
    run_cfg = {**cfg, "limits": {**cfg["limits"], "max_orders_per_day": n}} if large else cfg
    grid = fee_grid(cfg, cfg["simulation"]["compare_grid"]) if large else fee_grid(cfg)
    batch = len({f.date for f in led.fills()})
    budget = (led.state().cash if cash is None else D(cash)) / n
    threshold = D(cfg["liquidity"]["min_avg_daily_volume_eur"])
    candidates = sorted(t for t, i in instruments.items()
                        if i.kind == "equity" and (i.avg_daily_volume_eur or 0) >= threshold
                        and (c := store.price(t, decision_date)) is not None and c < budget * D("0.9")
                        and store.price(t, d, "open") is not None)
    picks = random.Random(seed + batch).sample(candidates, min(n, len(candidates)))
    orders = [Order(t, BUY, int((budget - 3) / store.price(t, decision_date))) for t in picks]
    orders = [o for o in orders if o.qty > 0]
    opens = {o.ticker: store.price(o.ticker, d, "open") for o in orders}
    execute_orders(led, orders, opens, _open_marks(store, led, d), instruments, run_cfg, grid, d)


def collect_news(env: Env, store, universe, instruments, now, today, fetch, warnings) -> list:
    cfg = env.cfg
    state = env.agents.state()
    held = sorted(state.positions)
    rows = screen(store, instruments, today, cfg, held)
    table = select(rows, cfg, held, state, store.marks(held, today), fee_grid(cfg))
    names = {t: universe[t].name for t in [*held, *(r["ticker"] for r in table if r["kind"] == "equity")]
             if t in universe}
    watch = dict(list(names.items())[:cfg["news"]["watch_max"]])
    return _news_window(env, now, fetch, watch, warnings), watch


def _news_window(env: Env, now, fetch, watch, warnings) -> list:
    cfg = env.cfg
    items, errors = fetch(cfg, watch)
    if errors and not items:
        warnings.append(f"aucune actualité récupérée ({len(errors)} flux en erreur)")
    ns = NewsStore(env.root / cfg["data"]["market_db"])
    ns.upsert(items)
    last = env.journal.last_run_at()
    since = datetime.fromisoformat(last) if last else now - timedelta(hours=cfg["news"]["lookback_hours"])
    return select_items(ns.between(since, now), cfg["news"]["max_items"])


def daily_message(env: Env, h, snaps, executions, entry, mode) -> tuple[str, str]:
    a = snaps["agents"]
    total_sessions = len(mc.sessions_between(h["start"], h["end"]))
    title = f"PEA sim J{h['session_no']}/{total_sessions} : {cents(a['total'])} € ({a['return_pct']:+.2f} %)"
    if entry.get("milestone"):
        title = f"📊 Bilan du mois {entry['milestone']} · " + title
    labels = {"benchmark": "ETF Monde", "random": "Aléatoire", "random_large": "Aléatoire large",
              "agents_lowcost": "Agents low-cost"}
    lines = [" | ".join(f"{labels[b]} {cents(snaps[b]['total'])} € ({snaps[b]['return_pct']:+.2f} %)"
                        for b in labels)]
    if executions:
        lines.append("Exécutés : " + ", ".join(
            f"{e['side']} {e['qty']} {e['ticker']} à {e['price']} €" if e["filled"] else f"{e['side']} {e['ticker']} refusé"
            for e in executions))
    orders = entry.get("final_orders", [])
    lines.append("Pour demain : " + (", ".join(f"{o['side']} {o['qty']} {o['ticker']}" for o in orders)
                                     or "aucun ordre"))
    if entry.get("milestone"):
        lines.append(f"Le bilan du mois {entry['milestone']} est disponible dans l'onglet Rapport.")
    return title, "\n".join(lines)


# --- Veille --------------------------------------------------------------------

def weekend_watch(env: Env, store, universe, last_session: str, held) -> dict[str, str]:
    """Titres suivis le week-end : les positions et les meilleurs candidats du screener de la dernière séance."""
    cfg = env.cfg
    tickers = sorted(set(prefilter(universe, cfg)) | set(held))
    instruments = build_instruments(universe, store, last_session, cfg, tickers)
    state = env.agents.state()
    rows = screen(store, instruments, last_session, cfg, held)
    table = select(rows, cfg, held, state, store.marks(held, last_session), fee_grid(cfg))
    names = {t: universe[t].name for t in [*held, *(r["ticker"] for r in table if r["kind"] == "equity")]
             if t in universe}
    return dict(list(names.items())[:cfg["weekend"]["watch_max"]])


def veille_kind(today: str) -> str:
    """Samedi : recherche approfondie ; dimanche : plan de la semaine ; autre jour sans cotation (férié) : léger."""
    return {5: "research", 6: "plan"}.get(date.fromisoformat(today).weekday(), "light")


def veille_run(env: Env, now, today, force, llm, fetch, fetch_hist=fetch_history, fetch_profile=None) -> RunResult:
    cfg, j = env.cfg, env.journal
    if j.is_complete(today, "veille") and not force:
        return RunResult("skip", "PEA sim", f"Veille du {today} déjà effectuée.")
    warnings: list[str] = []
    store = PriceStore(env.root / cfg["data"]["market_db"])
    upath = env.root / cfg["data"]["universe_path"]
    universe = load_universe(upath) if upath.exists() else {}
    held = sorted(env.agents.state().positions)
    kind = veille_kind(today)
    last = j.recent("9999", 1)
    watch = {**((last[0].get("watch") or {}) if last else {}), **{t: universe[t].name for t in held if t in universe}}
    if kind != "light" and env.agents.last_snapshot_date():
        try:
            watch = {**watch, **weekend_watch(env, store, universe, env.agents.last_snapshot_date(), held)}
        except Exception as e:                      # la veille continue avec la liste habituelle
            warnings.append(f"liste de titres du week-end : {e}")
    news_items = _news_window(env, now, fetch, watch, warnings)
    entry = run_veille(today=today, run_at=now, cfg=cfg, ledger=env.agents, store=store, journal=j,
                       news_items=news_items, kb=env.kb, watch=watch, kind=kind, llm=llm, fetch_hist=fetch_hist,
                       profile_fetch=fetch_profile)
    warnings += entry["errors"]
    if env.agents.last_snapshot_date():
        export_dashboard(cfg, env.root, env.ledgers, j, store, env.agents.last_snapshot_date(), universe)
        sync_dashboard(env, warnings)
    summary = (entry.get("news_analysis") or {}).get("summary", "")
    return RunResult("veille", f"PEA sim : veille du {today}", summary[:500], warnings)
