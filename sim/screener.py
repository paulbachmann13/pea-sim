"""Screener déterministe : indicateurs techniques sur l'univers PEA liquide (pas de LLM)."""
import math

import pandas as pd

from .models import PortfolioState
from .money import D
from .validator import max_buy_qty, max_qty_concentration

MIN_BARS = 21


def indicators(df: pd.DataFrame) -> dict | None:
    """Indicateurs à la dernière barre de `df` (déjà borné par as_of)."""
    if len(df) < MIN_BARS:
        return None
    c, v = df["close"], df["volume"]

    def ret(n):
        return round(float(c.iloc[-1] / c.iloc[-1 - n] - 1) * 100, 2) if len(c) > n else None

    delta = c.diff().tail(14)
    gain, loss = delta.clip(lower=0).mean(), -delta.clip(upper=0).mean()
    prev_vol = v.iloc[-21:-1].mean()
    return {
        "close": round(float(c.iloc[-1]), 4),
        "ret_1d": ret(1), "ret_5d": ret(5), "ret_20d": ret(20), "ret_60d": ret(60),
        "vol_20d": round(float(c.pct_change().tail(20).std() * math.sqrt(252) * 100), 1),
        "dist_ma20": round(float(c.iloc[-1] / c.tail(20).mean() - 1) * 100, 2),
        "dist_ma50": round(float(c.iloc[-1] / c.tail(50).mean() - 1) * 100, 2) if len(c) >= 50 else None,
        "vol_ratio": round(float(v.iloc[-1] / prev_vol), 2) if prev_vol > 0 else None,
        "rsi14": 100.0 if loss == 0 else round(float(100 - 100 / (1 + gain / loss)), 1),
        "adv_meur": round(float((c * v).tail(20).mean()) / 1e6, 2),
    }


def screen(store, instruments: dict, as_of: str, cfg: dict, held=()) -> dict[str, dict]:
    """Indicateurs pour les titres liquides cotés le jour `as_of` (+ titres détenus, toujours)."""
    threshold = D(cfg["liquidity"]["min_avg_daily_volume_eur"])
    rows = {}
    for t, inst in instruments.items():
        liquid = inst.kind == "etf" or (inst.avg_daily_volume_eur or 0) >= threshold
        if not (liquid or t in held):
            continue
        df = store.bars(t, as_of, lookback=130)
        if not len(df) or (df.index[-1] != as_of and t not in held):
            continue
        ind = indicators(df)
        if ind:
            rows[t] = {"ticker": t, "kind": inst.kind, "stale": df.index[-1] != as_of, **ind}
    return rows


def breadth(rows: dict[str, dict]) -> dict:
    eq = [r for r in rows.values() if r["kind"] == "equity" and r["ret_1d"] is not None]
    if not eq:
        return {}
    s1 = pd.Series([r["ret_1d"] for r in eq])
    s20 = pd.Series([r["ret_20d"] for r in eq if r["ret_20d"] is not None])
    return {"titres": len(eq), "pct_en_hausse": round(float((s1 > 0).mean() * 100), 1),
            "mediane_1j": round(float(s1.median()), 2), "mediane_20j": round(float(s20.median()), 2)}


def select(rows: dict[str, dict], cfg: dict, held, state: PortfolioState, marks: dict, grid: dict) -> list[dict]:
    """Choisit les lignes montrées aux agents et calcule `qmax` (quantité achetable maintenant)."""
    n = cfg["screener"]["top_n"]
    total = state.total_value(marks)
    max_price = total * D(cfg["limits"]["max_position_pct"]) / 100
    eq = [r for r in rows.values() if r["kind"] == "equity" and D(r["close"]) <= max_price
          and r["ret_20d"] is not None and not r["stale"]]

    def top(key, k, reverse=True):
        return sorted((r for r in eq if r[key] is not None), key=lambda r: r[key], reverse=reverse)[:k]

    lists = {
        "momentum20j": top("ret_20d", n),
        "hausse1j": top("ret_1d", n // 2),
        "baisse1j": top("ret_1d", n // 2, reverse=False),
        "volume": top("vol_ratio", n // 2),
        "detenu": [rows[t] for t in held if t in rows],
        "etf": [r for r in rows.values() if r["kind"] == "etf"],
    }
    picked: dict[str, dict] = {}
    for name, items in lists.items():
        for r in items:
            picked.setdefault(r["ticker"], {**r, "lists": []})["lists"].append(name)

    out = sorted(picked.values(), key=lambda r: ("detenu" not in r["lists"], -(r["ret_20d"] or 0)))
    out = out[:cfg["screener"]["max_rows"]]
    for r in out:
        price, t = D(r["close"]), r["ticker"]
        m = {**marks, t: price}
        r["qmax"] = min(max_buy_qty(state.cash, price, grid), max_qty_concentration(state, t, price, m, cfg, grid))
    return out


COLUMNS = [("ticker", "ticker"), ("kind", "type"), ("lists", "listes"), ("close", "cours"), ("ret_1d", "r1j%"),
           ("ret_5d", "r5j%"), ("ret_20d", "r20j%"), ("ret_60d", "r60j%"), ("vol_20d", "volat%"),
           ("dist_ma20", "vsMM20%"), ("dist_ma50", "vsMM50%"), ("vol_ratio", "volx"), ("rsi14", "rsi14"),
           ("adv_meur", "volM€"), ("qmax", "qmax")]


def format_table(rows: list[dict]) -> str:
    def cell(v):
        if v is None:
            return "-"
        return "+".join(v) if isinstance(v, list) else str(v)
    lines = [";".join(h for _, h in COLUMNS)]
    lines += [";".join(cell(r.get(k)) for k, _ in COLUMNS) for r in rows]
    return "\n".join(lines)
