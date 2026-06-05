"""T0 ingest del universo amplio — tests offline de la regla pre-registrada.

Spec: docs/superpowers/specs/2026-06-05-programa-t0-ingest-universo.md.
La regla del universo es una función pura (build_universe); la red se mockea.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from tools.program_ingest import download as dl
from tools.program_ingest.constants import WINDOW_END, WINDOW_START
from tools.program_ingest.universe import (
    build_universe,
    classify_symbol,
    expected_months,
    months_in_window,
    parse_listing_page,
)

# ---------------------------------------------------------------------------
# Filtros de forma/exclusión (spec §Regla 2-3)
# ---------------------------------------------------------------------------

def test_classify_keeps_plain_usdt():
    assert classify_symbol("BTCUSDT") is None
    assert classify_symbol("PENDLEUSDT") is None


def test_classify_rejects_non_usdt():
    assert classify_symbol("BTCBUSD") == "not_usdt"
    assert classify_symbol("ETHBTC") == "not_usdt"


def test_classify_rejects_leveraged():
    assert classify_symbol("ETHUPUSDT") == "leveraged"
    assert classify_symbol("BTCDOWNUSDT") == "leveraged"
    assert classify_symbol("EOSBULLUSDT") == "leveraged"
    assert classify_symbol("EOSBEARUSDT") == "leveraged"


def test_classify_rejects_declared_bases():
    assert classify_symbol("USDCUSDT") == "excluded_base"
    assert classify_symbol("EURUSDT") == "excluded_base"
    assert classify_symbol("BUSDUSDT") == "excluded_base"


# ---------------------------------------------------------------------------
# Ventana y cobertura
# ---------------------------------------------------------------------------

def test_months_in_window_clips_both_ends():
    months = ["2020-11", "2020-12", "2021-01", "2024-06", "2026-05", "2026-06"]
    assert months_in_window(months) == ["2021-01", "2024-06", "2026-05"]


def test_expected_months_inclusive():
    assert expected_months("2021-01", "2021-01") == 1
    assert expected_months("2021-01", "2021-12") == 12
    assert expected_months("2021-01", "2026-05") == 65


# ---------------------------------------------------------------------------
# La regla completa (spec §Regla 4-6)
# ---------------------------------------------------------------------------

def _full(first: str, last: str) -> list[str]:
    """All months first..last inclusive."""
    fy, fm = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    res, y, m = [], fy, fm
    while (y, m) <= (ly, lm):
        res.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return res


def test_build_universe_panel_vs_listed_later():
    uni = build_universe({
        "BTCUSDT": _full("2020-01", "2026-05"),     # panel (existed at start)
        "PENDLEUSDT": _full("2023-07", "2026-05"),  # listed later — NOT panel
        "ETHUPUSDT": _full("2020-01", "2026-05"),   # leveraged — excluded
        "USDCUSDT": _full("2020-01", "2026-05"),    # stablecoin — excluded
    })
    assert "BTCUSDT" in uni["panel"]
    assert uni["listed_later"] == ["PENDLEUSDT"]
    assert uni["excluded"]["leveraged"] == ["ETHUPUSDT"]
    assert uni["excluded"]["excluded_base"] == ["USDCUSDT"]
    assert uni["counts"]["panel"] == 1


def test_build_universe_keeps_delisted_with_last_month():
    """Spec §Regla 5: los delistados mid-window SE QUEDAN — anti-survivorship."""
    uni = build_universe({"OLDUSDT": _full("2021-01", "2023-04")})
    row = uni["panel"]["OLDUSDT"]
    assert row["delisted_in_window"] is True
    assert row["last_month"] == "2023-04"
    assert row["coverage_ok"] is True          # full while listed


def test_build_universe_coverage_reported_not_excluding():
    """Spec §Regla 6: cobertura baja se MARCA, el símbolo no se excluye."""
    months = [m for m in _full("2021-01", "2026-05") if not m.startswith("2023")]
    uni = build_universe({"GAPUSDT": months})
    assert "GAPUSDT" in uni["panel"]           # still in the panel
    assert uni["panel"]["GAPUSDT"]["coverage_ok"] is False


def test_build_universe_no_data_in_window():
    uni = build_universe({"DEADUSDT": ["2019-05", "2019-06"]})
    assert uni["excluded"]["no_data_in_window"] == ["DEADUSDT"]


# ---------------------------------------------------------------------------
# Parser del listing S3
# ---------------------------------------------------------------------------

_NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def test_parse_listing_page_common_prefixes_and_marker():
    xml = f"""<?xml version="1.0"?>
    <ListBucketResult {_NS}>
      <Prefix>data/spot/monthly/klines/</Prefix>
      <IsTruncated>true</IsTruncated>
      <NextMarker>data/spot/monthly/klines/ETHUSDT/</NextMarker>
      <CommonPrefixes><Prefix>data/spot/monthly/klines/BTCUSDT/</Prefix></CommonPrefixes>
      <CommonPrefixes><Prefix>data/spot/monthly/klines/ETHUSDT/</Prefix></CommonPrefixes>
    </ListBucketResult>"""
    prefixes, keys, marker = parse_listing_page(xml)
    assert prefixes == ["data/spot/monthly/klines/BTCUSDT/",
                        "data/spot/monthly/klines/ETHUSDT/"]
    assert keys == []
    assert marker == "data/spot/monthly/klines/ETHUSDT/"


def test_parse_listing_page_keys_not_truncated():
    xml = f"""<?xml version="1.0"?>
    <ListBucketResult {_NS}>
      <Prefix>data/spot/monthly/klines/BTCUSDT/1h/</Prefix>
      <IsTruncated>false</IsTruncated>
      <Contents><Key>data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2021-01.zip</Key></Contents>
      <Contents><Key>data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2021-01.zip.CHECKSUM</Key></Contents>
    </ListBucketResult>"""
    prefixes, keys, marker = parse_listing_page(xml)
    assert prefixes == []
    assert len(keys) == 2                      # month regex filters CHECKSUM later
    assert marker is None


def test_parse_listing_page_truncated_without_nextmarker_falls_back():
    xml = f"""<?xml version="1.0"?>
    <ListBucketResult {_NS}>
      <Prefix>p/</Prefix>
      <IsTruncated>true</IsTruncated>
      <Contents><Key>p/a.zip</Key></Contents>
    </ListBucketResult>"""
    _, keys, marker = parse_listing_page(xml)
    assert marker == "p/a.zip"


# ---------------------------------------------------------------------------
# Normalización de timestamps (spot 2025+ = microsegundos)
# ---------------------------------------------------------------------------

def test_normalize_open_time_ms_passthrough():
    assert dl.normalize_open_time(1609459200000) == 1609459200000


def test_normalize_open_time_us_to_ms():
    assert dl.normalize_open_time(1735689600000000) == 1735689600000


def test_parse_kline_rows_mixed_eras():
    rows = [
        ["1609459200000", "29000", "29100", "28900", "29050", "12.5",
         "1609462799999", "x", "1", "y", "z", "0"],
        ["1735689600000000", "93000", "93100", "92900", "93050", "3.2",
         "1735693199999999", "x", "1", "y", "z", "0"],
    ]
    out = dl.parse_kline_rows(rows)
    assert out[0] == (1609459200000, 29000.0, 29100.0, 28900.0, 29050.0, 12.5)
    assert out[1][0] == 1735689600000          # µs normalized to ms


# ---------------------------------------------------------------------------
# Inserción idempotente (red mockeada)
# ---------------------------------------------------------------------------

def test_download_symbol_inserts_and_reports_gaps(tmp_path, monkeypatch):
    db = str(tmp_path / "program.db")
    dl.init_db(db)
    fixture = {
        "2021-01": ([], [["1609459200000", "29000", "29100", "28900", "29050",
                          "12.5", "0", "0", "0", "0", "0", "0"]]),
        "2021-02": None,                       # 404 → gap
    }

    def fake_fetch(url, **kw):
        for mo, res in fixture.items():
            if mo in url:
                return res
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(dl, "_fetch_zip_csv", fake_fetch)
    per_month = dl.download_symbol("BTCUSDT", ["2021-01", "2021-02"], db)
    assert per_month == {"2021-01": 1, "2021-02": 0}
    # resume: second run touches no network (fetch would raise on any call)
    monkeypatch.setattr(dl, "_fetch_zip_csv",
                        lambda url, **kw: (_ for _ in ()).throw(AssertionError(url)))
    per_month2 = dl.download_symbol("BTCUSDT", ["2021-01", "2021-02"], db)
    assert per_month2 == {"2021-01": 1, "2021-02": 0}   # served from ingest_log
    with closing(sqlite3.connect(db)) as con:
        n = con.execute("SELECT COUNT(*) FROM spot_klines").fetchone()[0]
    assert n == 1


def test_backfill_ingest_log_reconstructs_months(tmp_path):
    db = str(tmp_path / "program.db")
    dl.init_db(db)
    with closing(sqlite3.connect(db)) as con:
        # two bars in 2021-01, one in 2021-02 (UTC month boundaries)
        con.execute("INSERT INTO spot_klines VALUES('BTCUSDT',1609459200000,1,1,1,1,1)")
        con.execute("INSERT INTO spot_klines VALUES('BTCUSDT',1609462800000,1,1,1,1,1)")
        con.execute("INSERT INTO spot_klines VALUES('BTCUSDT',1612137600000,1,1,1,1,1)")
        con.commit()
    assert dl.backfill_ingest_log(db) == 2
    with closing(sqlite3.connect(db)) as con:
        rows = dict(con.execute(
            "SELECT month, rows FROM ingest_log WHERE symbol='BTCUSDT'").fetchall())
    assert rows == {"2021-01": 2, "2021-02": 1}


def test_window_constants_match_spec():
    assert WINDOW_START == "2021-01"
    assert WINDOW_END == "2026-05"
