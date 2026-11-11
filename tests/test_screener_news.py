from datetime import datetime, timezone

import pandas as pd
import pytest

from sim import market_calendar as mc
from sim.data import PriceStore
from sim.models import Instrument, PortfolioState, Position
from sim.money import D
from sim.news import NewsItem, NewsStore, format_news, parse_feed, select_items
from sim.screener import format_table, indicators, screen, select

DAYS = mc.sessions_between("2026-08-03", "2026-11-11")
AS_OF = DAYS[-1]


def series(start=10.0, step=0.1, n=len(DAYS), volume=100_000, last_volume=None):
    closes = [start + step * i for i in range(n)]
    df = pd.DataFrame({"open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
                       "close": closes, "adj_close": closes, "volume": [volume] * n}, index=DAYS[-n:])
    if last_volume:
        df.iloc[-1, df.columns.get_loc("volume")] = last_volume
    return df


def test_indicators_uptrend():
    ind = indicators(series(last_volume=300_000))
    assert ind["rsi14"] == 100.0
    c = ind["close"]
    assert ind["ret_1d"] == round((c / (c - 0.1) - 1) * 100, 2)
    assert ind["vol_ratio"] == 3.0
    assert ind["dist_ma20"] > 0
    assert indicators(series(n=10)) is None


@pytest.fixture
def store():
    s = PriceStore(":memory:")
    s.upsert_bars("UP.PA", series(volume=200_000), "t")            # ~2-3 M€/j
    s.upsert_bars("DOWN.PA", series(30, -0.05, volume=100_000), "t")
    s.upsert_bars("ILLQ.PA", series(volume=100), "t")
    s.upsert_bars("STALE.PA", series(volume=200_000).iloc[:-1], "t")
    s.upsert_bars("EXP.PA", series(900, 1, volume=10_000), "t")    # trop cher pour 1 000 €
    return s


def instruments(store):
    out = {}
    for t in ["UP.PA", "DOWN.PA", "ILLQ.PA", "STALE.PA", "EXP.PA"]:
        out[t] = Instrument(t, "FR0000000001", "EUR", "equity", store.avg_daily_volume_eur(t, AS_OF, 20))
    return out


def test_screen_and_select(store, cfg, grid):
    rows = screen(store, instruments(store), AS_OF, cfg)
    assert set(rows) == {"UP.PA", "DOWN.PA", "EXP.PA"}           # illiquide et non coté ce jour exclus
    st = PortfolioState(cash=D(1000), deposits=D(1000))
    table = select(rows, cfg, [], st, {}, grid)
    tickers = [r["ticker"] for r in table]
    assert "EXP.PA" not in tickers                                 # prix > 40 % du portefeuille
    up = next(r for r in table if r["ticker"] == "UP.PA")
    assert "momentum20j" in up["lists"] and up["qmax"] > 0
    assert format_table(table).splitlines()[0].startswith("ticker;type;listes;cours")


def test_held_title_always_shown(store, cfg, grid):
    rows = screen(store, instruments(store), AS_OF, cfg, held=["STALE.PA"])
    assert rows["STALE.PA"]["stale"]
    st = PortfolioState(cash=D(500), deposits=D(1000), positions={"STALE.PA": Position(10, D(200))})
    table = select(rows, cfg, ["STALE.PA"], st, {"STALE.PA": D(20)}, grid)
    assert table[0]["ticker"] == "STALE.PA" and "detenu" in table[0]["lists"]


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>TotalEnergies &amp; le p&#233;trole</title><link>https://a/1</link>
<pubDate>Wed, 11 Nov 2026 14:24:00 GMT</pubDate><description>&lt;b&gt;R&#233;sum&#233;&lt;/b&gt; ici</description></item>
<item><title>Sans date</title><link>https://a/2</link></item>
<item><title></title><link>https://a/3</link></item>
</channel></rss>"""


def test_parse_feed():
    fetched = datetime(2026, 11, 11, 17, 0, tzinfo=timezone.utc)
    items = parse_feed(RSS, "test", "TTE.PA", fetched_at=fetched)
    assert [i.title for i in items] == ["TotalEnergies & le pétrole", "Sans date"]
    assert items[0].published == "2026-11-11T14:24:00+00:00" and items[0].summary == "Résumé ici"
    assert items[1].published == fetched.isoformat()              # sans date → heure de collecte, jamais futur


def item(i, published, ticker="", title=None):
    return NewsItem(str(i), "s", ticker, title or f"titre {i}", "", published, "")


def test_news_store_excludes_future():
    ns = NewsStore(":memory:")
    ns.upsert([item(1, "2026-11-11T10:00:00+00:00"), item(2, "2026-11-11T18:00:00+00:00"),
               item(3, "2026-11-09T10:00:00+00:00")])
    got = ns.between(datetime(2026, 11, 10, tzinfo=timezone.utc), datetime(2026, 11, 11, 17, tzinfo=timezone.utc))
    assert [i.id for i in got] == ["1"]


def test_select_items_dedupes_and_balances():
    items = [item(i, f"2026-11-11T{10 + i % 8:02d}:00:00+00:00") for i in range(20)]
    items += [item(100 + i, "2026-11-10T10:00:00+00:00", "TTE.PA") for i in range(10)]
    items += [item(200, "2026-11-11T12:00:00+00:00", title="Titre 1 !")]   # doublon de « titre 1 »
    picked = select_items(items, 10)
    assert len(picked) == 10
    assert sum(1 for i in picked if i.ticker) == 5
    everything = {i.id for i in select_items(items, 100)}
    assert len({"1", "200"} & everything) == 1                       # doublon éliminé
    assert format_news(picked).startswith("[")
