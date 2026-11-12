"""Actualités : flux RSS gratuits, stockés en base, filtrés par date de publication (pas de look-ahead)."""
import hashlib
import html
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import requests

from .data import HTTP_HEADERS

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY, source TEXT, ticker TEXT, title TEXT, link TEXT,
    published TEXT, summary TEXT
);
"""
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class NewsItem:
    id: str
    source: str
    ticker: str          # "" pour une actualité générale
    title: str
    link: str
    published: str       # ISO 8601 UTC
    summary: str


def _clean(text: str, limit: int) -> str:
    text = html.unescape(TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_feed(content: bytes, source: str, ticker: str = "", fetched_at: datetime | None = None) -> list[NewsItem]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    items = []
    for e in feedparser.parse(content).entries:
        title = _clean(e.get("title", ""), 200)
        if not title:
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime(*ts[:6], tzinfo=timezone.utc) if ts else fetched_at
        link = e.get("link", "")
        items.append(NewsItem(hashlib.sha1((link or title).encode()).hexdigest()[:8], source, ticker, title,
                              link, published.isoformat(), _clean(e.get("summary", ""), 240)))
    return items


def fetch_news(cfg: dict, watch: dict[str, str]) -> tuple[list[NewsItem], list[str]]:
    """watch : ticker -> nom de société. Renvoie (items, erreurs) ; un flux en panne n'arrête pas le run."""
    ncfg = cfg["news"]
    jobs = [(name, url, "") for name, url in ncfg["general_feeds"].items()]
    for ticker, name in watch.items():
        for src, tpl in ncfg["ticker_feeds"].items():
            jobs.append((src, tpl.format(ticker=quote(ticker), name=quote(name)), ticker))

    items, errors = [], []
    for source, url, ticker in jobs:
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=20)
            r.raise_for_status()
            items += parse_feed(r.content, source, ticker)
        except Exception as e:
            errors.append(f"{source} {ticker}: {e}")
    return items, errors


class NewsStore:
    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)

    def upsert(self, items: list[NewsItem]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?)",
                            [tuple(i.__dict__.values()) for i in items])
        self.db.commit()

    def between(self, since: datetime, until: datetime) -> list[NewsItem]:
        rows = self.db.execute("SELECT * FROM news WHERE published > ? AND published <= ? ORDER BY published DESC",
                               (since.astimezone(timezone.utc).isoformat(), until.astimezone(timezone.utc).isoformat()))
        return [NewsItem(*r) for r in rows]


def select_items(items: list[NewsItem], max_items: int) -> list[NewsItem]:
    """Dédoublonne par titre et garde les plus récents, en réservant de la place aux news par titre."""
    seen, general, by_ticker = set(), [], []
    for i in sorted(items, key=lambda i: i.published, reverse=True):
        key = re.sub(r"\W+", "", i.title.lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        (by_ticker if i.ticker else general).append(i)
    half = max_items // 2
    picked = by_ticker[:half] + general[:max_items - min(half, len(by_ticker))]
    return sorted(picked, key=lambda i: i.published, reverse=True)[:max_items]


def price_reactions(items: list[NewsItem], store, as_of: str) -> dict[str, float]:
    """Variation du cours du titre lié entre la dernière clôture AVANT publication et la clôture `as_of`."""
    out = {}
    for i in items:
        if not i.ticker:
            continue
        before = store.bars(i.ticker, i.published[:10], lookback=2)
        before = before[before.index < i.published[:10]] if len(before) else before
        now = store.last_close(i.ticker, as_of)
        if len(before) and now is not None:
            out[i.id] = round((float(now) / float(before["close"].iloc[-1]) - 1) * 100, 2)
    return out


def format_news(items: list[NewsItem], reactions: dict[str, float] | None = None) -> str:
    reactions = reactions or {}
    lines = []
    for i in items:
        tag = f" ({i.ticker})" if i.ticker else ""
        summary = f" — {i.summary}" if i.summary and i.summary not in i.title else ""
        react = f" [cours depuis publication : {reactions[i.id]:+.2f} %]" if i.id in reactions else ""
        lines.append(f"[{i.id}] {i.published[:16]} {i.source}{tag} : {i.title}{summary}{react}")
    return "\n".join(lines) or "(aucune actualité)"
