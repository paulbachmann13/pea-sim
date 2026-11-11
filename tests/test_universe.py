from sim.data import PriceStore
from sim.universe import (build_instruments, etf_entries, load_universe, parse_euronext_list,
                          prefilter, save_universe)
from sim.validator import check_pea_eligibility

SAMPLE = '﻿' + "\n".join([
    'Name;ISIN;Symbol;Market;Currency;"Open Price";"High Price";"low Price";"last Price";'
    '"last Trade MIC Time";"Time Zone";Volume;Turnover;"Closing Price";"Closing Price DateTime"',
    '"European Equities"', '"11 Nov 2026"', '"All datapoints provided as of end of last active trading day."',
    'TOTALENERGIES;FR0000120271;TTE;"Euronext Paris";EUR;78.5;79.6;78.5;79.4;" 17:35";CET;2379843;188732888;79.37;',
    'AIRBUS;NL0000235190;AIR;"Euronext Paris";EUR;1;1;1;1;" 17:35";CET;1;150000000;1;',
    '2CRSI;FR0013341781;AL2SI;"Euronext Growth Paris";EUR;17.81;18.95;17.71;18.76;" 17:35";CET;485584;8873336.41;18.76;',
    'TINY;FR0000000017;TNY;"Euronext Growth Paris";EUR;1;1;1;1;" 17:35";CET;10;10;1;',
    '"3D SYSTEMS CORP";US88554D2053;4DDD;EuroTLX;EUR;2.8;2.8;2.8;2.8;" 09:44";CET;2500;7165.00;1.692;',
    '"2020 BULKERS";BMG9156K1018;2020;"Oslo Børs";NOK;8.2;8.4;7.3;7.8;" 16:25";CET;1235126;9660986;7.895;',
    'AB INBEV;BE0974293251;ABI;"Euronext Brussels, Paris";EUR;1;1;1;1;" 17:35";CET;1;90000000;1;',
    'GLOBALFOO;US0000000001;FOO;"Euronext Paris";EUR;1;1;1;1;" 17:35";CET;1;90000000;1;',
    'NOTURNOVER;FR0000000025;NTO;"Euronext Paris";EUR;-;-;-;-;" ";CET;-;-;-;',
])


def test_parse_keeps_only_euronext_markets():
    u = parse_euronext_list(SAMPLE)
    assert set(u) == {"TTE.PA", "AIR.PA", "AL2SI.PA", "TNY.PA", "ABI.BR", "FOO.PA", "NTO.PA"}
    assert u["TTE.PA"].isin == "FR0000120271" and u["TTE.PA"].mic == "XPAR"
    assert u["AL2SI.PA"].mic == "ALXP"
    assert u["NTO.PA"].turnover == 0.0


def test_save_load_roundtrip(tmp_path):
    u = parse_euronext_list(SAMPLE)
    save_universe(tmp_path / "u.csv", u)
    assert load_universe(tmp_path / "u.csv") == u


def test_prefilter(cfg):
    u = parse_euronext_list(SAMPLE)
    # seuil 1 M€ × 0,3 = 300 k€ ; FOO.PA a un ISIN US
    assert prefilter(u, cfg) == ["ABI.BR", "AIR.PA", "AL2SI.PA", "TTE.PA"]


def test_build_instruments_and_eligibility(cfg):
    u = {**parse_euronext_list(SAMPLE), **etf_entries(cfg)}
    inst = build_instruments(u, PriceStore(":memory:"), "2026-11-11", cfg, ["TTE.PA", "FOO.PA", "WPEA.PA", "ZZZ.PA"])
    assert set(inst) == {"TTE.PA", "FOO.PA", "WPEA.PA"}
    assert inst["TTE.PA"].avg_daily_volume_eur is None          # pas d'historique en base
    assert check_pea_eligibility(inst["TTE.PA"], cfg) is None
    assert "UE/EEE" in check_pea_eligibility(inst["FOO.PA"], cfg)
    assert check_pea_eligibility(inst["WPEA.PA"], cfg) is None
    assert inst["WPEA.PA"].kind == "etf"
