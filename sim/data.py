"""Prix : source de vérité unique = la base SQLite locale.

Alimentation :
  - yfinance en source principale (téléchargement groupé, dividendes) ;
  - historique officiel Euronext en secours (barre manquante) et en contrôle croisé (titres détenus).
Les barres incohérentes sont rejetées et journalisées, jamais stockées.

Toute lecture est bornée par `as_of` : un agent qui décide le jour J ne peut pas voir J+1.
Les prix stockés sont BRUTS (non ajustés) : l'exécution et la compta se font au vrai cours,
les dividendes étant crédités à part. adj_close n'est conservé que pour l'analyse.
"""
import csv
import io
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import requests

from .money import D

log = logging.getLogger(__name__)

COLS = ["open", "high", "low", "close", "adj_close", "volume"]
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0"}
EURONEXT_HISTORY_URL = "https://live.euronext.com/en/ajax/AwlHistoricalPrice/getFullDownloadAjax/{isin}-{mic}"


@dataclass(frozen=True)
class Issue:
    ticker: str
    date: str
    kind: str        # missing | invalid | jump | cross_check | source_error
    detail: str
    blocking: bool   # True = la barre est inutilisable (titre non négociable ce jour)


# --- Contrôle de cohérence (pur, testable) -----------------------------------

def check_bars(ticker: str, df: pd.DataFrame, expected_dates: list[str], max_jump_pct) -> list[Issue]:
    """`df` : index = dates AAAA-MM-JJ triées, colonnes COLS."""
    issues = []
    for d in expected_dates:
        if d not in df.index:
            issues.append(Issue(ticker, d, "missing", "pas de barre pour cette séance", True))

    for d, r in df.iterrows():
        ohlc = r[["open", "high", "low", "close"]]
        if ohlc.isna().any():
            issues.append(Issue(ticker, d, "invalid", "valeur OHLC manquante", True))
        elif (ohlc <= 0).any():
            issues.append(Issue(ticker, d, "invalid", "prix nul ou négatif", True))
        elif r.low > r.high or not (r.low * 0.999 <= r.open <= r.high * 1.001) \
                or not (r.low * 0.999 <= r.close <= r.high * 1.001):
            issues.append(Issue(ticker, d, "invalid",
                                f"OHLC incohérent (O={r.open} H={r.high} L={r.low} C={r.close})", True))
        elif pd.isna(r.volume) or r.volume < 0:
            issues.append(Issue(ticker, d, "invalid", "volume manquant ou négatif", True))

    bad = {i.date for i in issues if i.kind == "invalid"}
    closes = df.loc[~df.index.isin(bad), "close"]
    for d, pct in (closes.pct_change().abs() * 100).items():
        if pct > float(max_jump_pct):
            issues.append(Issue(ticker, d, "jump", f"variation de {pct:.1f} % en une séance", False))
    return issues


# --- Stockage ------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    ticker TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL, source TEXT,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS dividends (
    ticker TEXT NOT NULL, ex_date TEXT NOT NULL, amount REAL,
    PRIMARY KEY (ticker, ex_date)
);
CREATE TABLE IF NOT EXISTS data_issues (
    run_date TEXT, ticker TEXT, date TEXT, kind TEXT, detail TEXT, blocking INTEGER
);
"""


class PriceStore:
    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)

    def upsert_bars(self, ticker: str, df: pd.DataFrame, source: str) -> None:
        rows = [(ticker, d, *(None if pd.isna(r[c]) else float(r[c]) for c in COLS), source)
                for d, r in df.iterrows()]
        self.db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()

    def upsert_dividends(self, ticker: str, divs: dict[str, float]) -> None:
        self.db.executemany("INSERT OR REPLACE INTO dividends VALUES (?,?,?)",
                            [(ticker, d, a) for d, a in divs.items()])
        self.db.commit()

    def log_issues(self, run_date: str, issues: list[Issue]) -> None:
        self.db.executemany("INSERT INTO data_issues VALUES (?,?,?,?,?,?)",
                            [(run_date, i.ticker, i.date, i.kind, i.detail, int(i.blocking)) for i in issues])
        self.db.commit()

    # Lectures — toujours bornées par as_of
    def bars(self, ticker: str, as_of: str, lookback: int | None = None) -> pd.DataFrame:
        q = "SELECT date, open, high, low, close, adj_close, volume FROM bars WHERE ticker=? AND date<=? ORDER BY date"
        df = pd.read_sql_query(q, self.db, params=(ticker, as_of), index_col="date")
        return df.tail(lookback) if lookback else df

    def bar(self, ticker: str, d: str) -> dict | None:
        df = self.bars(ticker, d, lookback=1)
        return df.iloc[0].to_dict() if len(df) and df.index[0] == d else None

    def price(self, ticker: str, d: str, field: str = "close") -> Decimal | None:
        b = self.bar(ticker, d)
        return D(b[field]) if b else None

    def last_close(self, ticker: str, as_of: str) -> Decimal | None:
        df = self.bars(ticker, as_of, lookback=1)
        return D(df["close"].iloc[0]) if len(df) else None

    def marks(self, tickers, as_of: str) -> dict[str, Decimal]:
        """Dernier cours de clôture connu à `as_of`, pour valoriser le portefeuille."""
        out = {t: self.last_close(t, as_of) for t in tickers}
        return {t: p for t, p in out.items() if p is not None}

    def avg_daily_volume_eur(self, ticker: str, as_of: str, window: int) -> Decimal | None:
        df = self.bars(ticker, as_of, lookback=window)
        if len(df) < max(5, window // 2):
            return None
        return D(round(float((df["close"] * df["volume"]).mean()), 2))

    def dividends_on(self, d: str) -> dict[str, Decimal]:
        rows = self.db.execute("SELECT ticker, amount FROM dividends WHERE ex_date=?", (d,))
        return {t: D(a) for t, a in rows}


# --- Sources -------------------------------------------------------------------

def fetch_yfinance(tickers: list[str], start: str, end: str) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    """Barres brutes [start, end] et dividendes (date ex) par ticker."""
    import yfinance as yf

    end_excl = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    raw = yf.download(tickers, start=start, end=end_excl, auto_adjust=False, actions=True,
                      group_by="ticker", progress=False, threads=True)
    bars, divs = {}, {}
    for t in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if t not in raw.columns.get_level_values(0):
                continue
            sub = raw[t]
        else:
            sub = raw
        sub = sub.dropna(how="all", subset=["Open", "High", "Low", "Close"])
        if sub.empty:
            continue
        sub.index = pd.to_datetime(sub.index).strftime("%Y-%m-%d")
        df = sub.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                                 "Adj Close": "adj_close", "Volume": "volume"})[COLS]
        # yfinance renvoie des float32 bruités (7.176000118) : on arrondit à 4 décimales
        df = df.round({c: 4 for c in COLS if c != "volume"})
        bars[t] = df
        if "Dividends" in sub:
            d = sub["Dividends"]
            divs[t] = {k: float(v) for k, v in d[d > 0].items()}
    return bars, divs


def parse_euronext_history(text: str) -> pd.DataFrame:
    lines = text.lstrip("﻿").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Date;"))
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=";"))
    df = pd.DataFrame({
        "date": [pd.to_datetime(r["Date"], dayfirst=True).strftime("%Y-%m-%d") for r in rows],
        "open": [float(r["Open"]) for r in rows],
        "high": [float(r["High"]) for r in rows],
        "low": [float(r["Low"]) for r in rows],
        "close": [float(r["Close"]) for r in rows],
        "volume": [float(r["Number of Shares"]) for r in rows],
    }).set_index("date").sort_index()
    df["adj_close"] = df["close"]
    return df[COLS]


def fetch_euronext(isin: str, mic: str, start: str, end: str) -> pd.DataFrame:
    params = {"format": "csv", "decimal_separator": ".", "date_form": "d/m/Y", "op": "", "adjusted": "N",
              "base100": "", "startdate": start, "enddate": end}
    r = requests.get(EURONEXT_HISTORY_URL.format(isin=isin, mic=mic), params=params,
                     headers=HTTP_HEADERS, timeout=30)
    r.raise_for_status()
    return parse_euronext_history(r.content.decode("utf-8-sig"))


# --- Mise à jour ----------------------------------------------------------------

def update_prices(store: PriceStore, tickers: list[str], refs: dict[str, tuple[str, str]],
                  start: str, end: str, expected_dates: list[str], cfg: dict,
                  cross_check: set[str] = frozenset(), run_date: str | None = None,
                  fetch_yf=fetch_yfinance, fetch_enx=fetch_euronext) -> list[Issue]:
    """Met à jour la base pour `tickers` sur [start, end].

    refs : ticker -> (isin, mic) pour la source Euronext.
    expected_dates : séances qui doivent impérativement être présentes (en général la dernière).
    cross_check : titres à comparer systématiquement avec Euronext (les titres détenus).
    """
    dcfg = cfg["data"]
    issues: list[Issue] = []
    try:
        yf_bars, yf_divs = fetch_yf(tickers, start, end)
    except Exception as e:                       # yfinance indisponible : tout passera par Euronext
        log.exception("yfinance a échoué")
        issues.append(Issue("*", end, "source_error", f"yfinance : {e}", False))
        yf_bars, yf_divs = {}, {}

    for t in tickers:
        df = yf_bars.get(t, pd.DataFrame(columns=COLS))
        t_issues = check_bars(t, df, expected_dates, dcfg["max_daily_jump_pct"])
        bad = {i.date for i in t_issues if i.kind == "invalid"}
        good = df.loc[~df.index.isin(bad)]
        missing = {i.date for i in t_issues if i.blocking}

        if (missing or t in cross_check) and t in refs:
            try:
                enx = fetch_enx(*refs[t], start, end)
            except Exception as e:
                enx = None
                t_issues.append(Issue(t, end, "source_error", f"Euronext : {e}", False))
            if enx is not None and not enx.empty:
                enx_issues = check_bars(t, enx, [], dcfg["max_daily_jump_pct"])
                enx_bad = {i.date for i in enx_issues if i.kind == "invalid"}
                patch = enx.loc[enx.index.isin(missing) & ~enx.index.isin(enx_bad)]
                if len(patch):
                    store.upsert_bars(t, patch, "euronext")
                    t_issues = [i for i in t_issues if not (i.blocking and i.date in patch.index)]
                    t_issues += [Issue(t, d, "missing", "comblé par la source Euronext", False) for d in patch.index]
                t_issues += _cross_check(t, good, enx, dcfg["cross_check_tolerance_pct"])

        if len(good):
            store.upsert_bars(t, good, "yfinance")
        if yf_divs.get(t):
            store.upsert_dividends(t, yf_divs[t])
        issues += t_issues

    store.log_issues(run_date or end, issues)
    return issues


def _cross_check(ticker: str, a: pd.DataFrame, b: pd.DataFrame, tol_pct) -> list[Issue]:
    common = a.index.intersection(b.index)
    if not len(common):
        return []
    d = common[-1]
    ca, cb = a.at[d, "close"], b.at[d, "close"]
    gap = abs(ca - cb) / cb * 100
    if gap > float(tol_pct):
        return [Issue(ticker, d, "cross_check",
                      f"clôture yfinance {ca} vs Euronext {cb} (écart {gap:.2f} %)", False)]
    return []
