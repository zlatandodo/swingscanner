#!/usr/bin/env python3
"""
swing_scanner.py — A+ swing-trade setup scanner for S&P 500 + Russell 2000.

Self-contained: uses yfinance for ALL data (prices + fundamentals), caches to
SQLite, scores every name 0-100 across 6 transparent blocks (Trend Template,
Weinstein Stage 2, Pullback Quality, VCP, Relative Strength, Fundamentals),
computes ATR-based risk/position sizing, and emits a standalone dark-theme
interactive HTML report.

Usage:
    python swing_scanner.py [--fast] [--universe sp500|russell|both]
                            [--min-score 55] [--account 100000]
                            [--risk-pct 0.75] [--output ./reports]

See README.md for the first-run checklist.
"""
from __future__ import annotations

import argparse
import io
import math
import os
import sqlite3
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Optional dependencies — degrade gracefully
# ----------------------------------------------------------------------------
try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    sys.exit("FATAL: yfinance is required.  pip install yfinance")

try:
    from tqdm import tqdm
except ImportError:  # tqdm is optional — provide a lightweight shim
    def tqdm(iterable=None, total=None, desc=None, **kwargs):
        if iterable is None:
            iterable = range(total or 0)
        label = f"{desc}: " if desc else ""
        seq = list(iterable)
        n = len(seq)
        for i, item in enumerate(seq, 1):
            if n and (i % max(1, n // 40) == 0 or i == n):
                pct = 100.0 * i / n
                sys.stderr.write(f"\r{label}{i}/{n} ({pct:4.0f}%)")
                sys.stderr.flush()
            yield item
        sys.stderr.write("\n")

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DB = os.path.join(HERE, "swing_cache.db")
RUSSELL_CSV = os.path.join(HERE, "russell2000.csv")
FAILED_LOG = os.path.join(HERE, "failed_tickers.log")

MIN_PRICE = 10.0
MIN_AVG_VOL = 300_000
BENCHMARK = "SPY"

GRADE_COLORS = {
    "A+": "#1a7f37",   # dark green
    "A":  "#238636",   # green
    "B+": "#9e6a03",   # amber
    "B":  "#57606a",   # gray
}

# ============================================================================
# MODULE 1 — UNIVERSE LOADER
# ============================================================================

# A representative hardcoded Russell-2000 slice used as a last-resort fallback
# when neither the bundled CSV nor the iShares download is available.
RUSSELL_FALLBACK = [
    "AA","AAOI","ABCB","ABG","ABM","ACAD","ACIW","ACLS","ACT","ADMA","AEIS","AGYS",
    "AGO","AIN","AIR","AIT","AKR","AL","ALEX","ALG","ALKS","ALRM","AMBA","AMC","AMN",
    "AMR","AMRC","AMWD","ANDE","ANF","AORT","AOSL","APAM","APLE","APOG","ARCB","ARI",
    "ARLO","ARWR","ASB","ASGN","ASO","ATEN","ATGE","ATKR","AVA","AVAV","AVNS","AWR",
    "AX","AXL","AZZ","BANF","BANR","BCC","BCO","BCPC","BDC","BFH","BGS","BHE","BJRI",
    "BKE","BKU","BL","BLMN","BMI","BOOT","BOX","BRC","BRKL","BSIG","BTU","BXMT","CABO",
    "CAKE","CAL","CALM","CARG","CARS","CASH","CATY","CBRL","CBT","CBU","CCOI","CCS",
    "CDE","CENT","CENX","CERS","CEVA","CHEF","CHX","CIEN","CLB","CLDX","CLF","CLW",
    "CMC","CMP","CMRE","CNK","CNMD","CNS","CNXN","COHU","COLB","COLL","COOP","CORT",
    "CPK","CPRX","CRC","CRK","CRS","CRSR","CRVL","CSGS","CSWI","CTRE","CTRN","CTS",
    "CUBI","CVCO","CVI","CVLT","CWST","CWT","CXW","DAN","DBI","DCOM","DDD","DEA","DEN",
    "DFIN","DGII","DIN","DIOD","DK","DNOW","DOCN","DORM","DXC","DXPE","DY","ECPG","EFC",
    "EGBN","EIG","ELF","ENS","ENV","ENVA","EPAC","EPC","ESE","ETSY","EVRI","EXLS","EXPO",
    "EXTR","EZPW","FBK","FBNC","FCF","FCPT","FELE","FFBC","FHB","FIBK","FIZZ","FL","FLGT",
    "FORM","FOXF","FRME","FSS","FUL","FULT","FWRD","GBX","GDEN","GEF","GEO","GFF","GIII",
    "GKOS","GMS","GNW","GO","GOGO","GPI","GPRE","GSHD","GT","GTLS","GVA","HAYW","HBI",
    "HCC","HCI","HELE","HFWA","HGV","HI","HLIT","HLX","HMN","HOPE","HRMY","HSII","HSTM",
    "HUBG","HWKN","ICUI","IDCC","IIPR","INDB","INSP","INVA","IOSP","IPAR","IRDM","IRWD",
    "ITGR","ITRI","JACK","JBLU","JBSS","JBT","JJSF","JOE","JXN","KAI","KALU","KFY","KLG",
    "KMT","KN","KOP","KSS","KTB","KW","KWR","LBRT","LCII","LGND","LKFN","LMAT","LNN",
    "LOB","LPG","LQDT","LRN","LTC","LXP","LZB","MARA","MATW","MATX","MBIN","MC","MCRI",
    "MCY","MD","MDC","MDP","MGEE","MGPI","MGY","MHO","MLI","MMI","MMSI","MNRO","MODG",
    "MOG.A","MPW","MQ","MRTN","MSGS","MTRN","MTX","MUR","MYGN","MYRG","NARI","NATL","NAVI",
    "NBHC","NBR","NEO","NEOG","NGVT","NHC","NMIH","NOG","NPK","NPO","NSIT","NTB","NTCT",
    "NVEE","NWBI","NWL","NWN","NX","OFG","OII","OIS","OMCL","OMI","ONB","OSIS","OTTR",
    "OXM","PAHC","PARR","PATK","PBH","PCRX","PDCO","PDFS","PEB","PENN","PFBC","PFS","PGNY",
    "PI","PINC","PIPR","PJT","PLAB","PLMR","PLXS","PLUS","PMT","POWL","PPBI","PRA","PRDO",
    "PRFT","PRG","PRGS","PRIM","PRK","PRLB","PSMT","PTEN","PTGX","PUMP","PZZA","QDEL",
    "RAMP","RC","RCKT","RCKY","RDN","RDNT","REZI","RGR","RHP","RNST","ROCK","ROG","RUN",
    "RUSHA","RWT","RXO","SABR","SAFT","SAH","SANM","SBCF","SBH","SBSI","SCL","SCSC","SCVL",
    "SDGR","SEDG","SEE","SFBS","SFNC","SGH","SHAK","SHEN","SHOO","SIG","SITM","SKT","SKYW",
    "SLG","SM","SMP","SMPL","SNCY","SNDR","SNEX","SONO","SPNT","SPSC","SPTN","SPXC","SR",
    "SRCE","SSB","SSTK","STAA","STBA","STC","STEP","STRA","STRL","SUPN","SVC","SWX","SXC",
    "SXT","TALO","TBBK","TDS","TDW","TGNA","TGTX","THRM","THS","TILE","TMP","TNC","TNDM",
    "TPB","TPH","TRIP","TRMK","TRN","TRST","TRUP","TTGT","TTMI","TWI","TWO","UCTT","UE",
    "UFCS","UFPI","UFPT","UHT","UMBF","UNF","UNFI","UPBD","URBN","USLM","USNA","USPH",
    "UTL","VBTX","VCEL","VCYT","VECO","VERV","VFC","VIAV","VICR","VIR","VIRT","VRE","VRRM",
    "VRTS","VSAT","VSCO","VSTO","VTLE","WABC","WAFD","WD","WDFC","WERN","WGO","WHD","WINA",
    "WIRE","WKC","WLY","WMK","WNC","WOR","WRLD","WSFS","WSR","WT","WWW","XHR","XNCR","XPEL",
    "YELP","YOU","ZD","ZEUS","ZWS",
]


def clean_symbol(sym: str) -> str:
    """Normalize a ticker for yfinance (BRK.B -> BRK-B, strip whitespace)."""
    if not isinstance(sym, str):
        return ""
    sym = sym.strip().upper()
    # yfinance uses '-' for share classes, not '.'
    sym = sym.replace(".", "-")
    return sym


def _wiki_symbols(url: str) -> list[str]:
    """Scrape the first table with a Symbol/Ticker column from a wiki page.

    Fetches via requests (certifi) — urllib/pd.read_html(url) can hit
    SSL CERTIFICATE_VERIFY_FAILED on macOS Python builds.
    """
    import requests
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                        timeout=30).text
    tables = pd.read_html(io.StringIO(html))
    for t in tables:
        cols = {str(c).strip().lower(): c for c in t.columns}
        key = cols.get("symbol") or cols.get("ticker")
        if key is not None:
            return [clean_symbol(s) for s in t[key].astype(str).tolist()]
    return []


def load_sp500() -> list[str]:
    """Scrape current S&P 500 constituents from Wikipedia."""
    try:
        syms = _wiki_symbols(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        syms = [s for s in syms if s]
        print(f"  S&P 500: {len(syms)} tickers from Wikipedia")
        return syms
    except Exception as e:  # noqa: BLE001
        print(f"  ! S&P 500 scrape failed ({e}); using empty list")
        return []


def _download_iwm_holdings() -> list[str]:
    """Attempt to download IWM (iShares Russell 2000) holdings as CSV."""
    import requests

    url = (
        "https://www.ishares.com/us/products/239710/"
        "ishares-russell-2000-etf/1467271812596.ajax"
        "?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.text
    # iShares increasingly serves an HTML challenge page instead of the CSV.
    if text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        raise ValueError("iShares returned HTML, not CSV (bot-blocked)")
    # The CSV has a preamble; the holdings table starts at the "Ticker" header.
    start = text.find("Ticker")
    if start == -1:
        raise ValueError("could not locate holdings header in IWM CSV")
    df = pd.read_csv(io.StringIO(text[start:]))
    col = next((c for c in df.columns if c.strip().lower() == "ticker"), None)
    if col is None:
        raise ValueError("no Ticker column in IWM CSV")
    syms = []
    for s in df[col].tolist():
        cs = clean_symbol(str(s))
        # filter out cash/derivative placeholder rows
        if cs and cs.isalpha() and len(cs) <= 5:
            syms.append(cs)
    if len(syms) < 100:
        raise ValueError(f"IWM holdings parse yielded only {len(syms)} symbols")
    return syms


def load_russell2000() -> list[str]:
    """Load Russell 2000 from bundled CSV, else iShares download, else fallback."""
    # 1) bundled CSV
    if os.path.exists(RUSSELL_CSV):
        try:
            df = pd.read_csv(RUSSELL_CSV)
            col = df.columns[0]
            for c in df.columns:
                if c.strip().lower() in ("ticker", "symbol"):
                    col = c
                    break
            syms = [clean_symbol(s) for s in df[col].tolist()]
            syms = [s for s in syms if s and s.replace("-", "").isalnum()]
            if syms:
                print(f"  Russell 2000: {len(syms)} tickers from {RUSSELL_CSV}")
                return syms
        except Exception as e:  # noqa: BLE001
            print(f"  ! Failed to read {RUSSELL_CSV} ({e})")

    # 2) try live download from iShares, persist for next time
    try:
        syms = _download_iwm_holdings()
        pd.DataFrame({"Ticker": syms}).to_csv(RUSSELL_CSV, index=False)
        print(f"  Small/Mid-cap: {len(syms)} tickers from iShares IWM "
              f"(saved to {RUSSELL_CSV})")
        return syms
    except Exception as e:  # noqa: BLE001
        print(f"  ! iShares IWM unavailable ({e})")

    # 3) reliable proxy: S&P MidCap 400 + SmallCap 600 from Wikipedia
    try:
        syms = []
        for url in (
            "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
            "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
        ):
            syms += _wiki_symbols(url)
        syms = [s for s in syms if s]
        if len(syms) > 200:
            pd.DataFrame({"Ticker": syms}).to_csv(RUSSELL_CSV, index=False)
            print(f"  Small/Mid-cap: {len(syms)} tickers from S&P 400+600 "
                  f"(Wikipedia, saved to {RUSSELL_CSV})")
            return syms
    except Exception as e:  # noqa: BLE001
        print(f"  ! S&P 400/600 scrape failed ({e})")

    # 4) hardcoded representative fallback
    syms = [clean_symbol(s) for s in RUSSELL_FALLBACK]
    print(f"  Small/Mid-cap: {len(syms)} tickers from hardcoded fallback")
    return syms


def _clean_listed(sym: str) -> str | None:
    """Keep common stock / class-share tickers; drop warrants, units, prefs."""
    sym = str(sym).strip().upper()
    if not sym or "$" in sym or " " in sym:
        return None
    if "." in sym:
        base, _, suf = sym.partition(".")
        if len(suf) != 1 or suf not in "ABC":  # class A/B/C only
            return None
        sym = base + "-" + suf  # yfinance form (BRK.A -> BRK-A)
    if len(sym) > 6 or not all(c.isalnum() or c == "-" for c in sym):
        return None
    return sym


_NONCOMMON = (
    "warrant", "right", " unit", "units", "preferred", "depositary",
    "depository", "% note", "subordinated", "debenture", "convertible note",
    "when issued", "when-issued", "tender", "test stock",
)


def _fetch_nasdaqtrader(url, sym_col, etf_col, test_col) -> list[str]:
    """Parse a nasdaqtrader.com pipe-delimited symbol-directory file,
    keeping only common stock (no ETFs, warrants, units, rights, prefs)."""
    import requests
    txt = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                       timeout=30).text
    df = pd.read_csv(io.StringIO(txt), sep="|")
    df = df[df[test_col].isin(["N", "Y"])]            # drop footer row
    df = df[df[test_col] == "N"]                      # no test issues
    if etf_col in df.columns:
        df = df[df[etf_col] == "N"]                   # no ETFs
    name_col = "Security Name"
    out = []
    for _, r in df.iterrows():
        name = str(r.get(name_col, "")).lower()
        if any(k in name for k in _NONCOMMON):
            continue
        cs = _clean_listed(r[sym_col])
        if cs:
            out.append(cs)
    return out


def load_nasdaq() -> list[str]:
    """All NASDAQ-listed common stocks (nasdaqtrader.com)."""
    try:
        syms = _fetch_nasdaqtrader(
            "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
            "Symbol", "ETF", "Test Issue")
        print(f"  NASDAQ: {len(syms)} common stocks from nasdaqtrader")
        return syms
    except Exception as e:  # noqa: BLE001
        print(f"  ! NASDAQ load failed ({e})")
        return []


def load_other_listed() -> list[str]:
    """NYSE / NYSE American / etc. common stocks (nasdaqtrader.com)."""
    try:
        syms = _fetch_nasdaqtrader(
            "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
            "ACT Symbol", "ETF", "Test Issue")
        print(f"  NYSE/AMEX: {len(syms)} common stocks from nasdaqtrader")
        return syms
    except Exception as e:  # noqa: BLE001
        print(f"  ! otherlisted load failed ({e})")
        return []


def build_universe(which: str) -> list[str]:
    """Assemble, dedup, and clean the requested universe.

    which: sp500 | russell | nasdaq | both | all
      - both = S&P 500 + MidCap 400 + SmallCap 600 (~1,500)
      - all  = every US-listed common stock (NASDAQ + NYSE/AMEX, ~6,000 raw)
               plus the S&P lists, deduped — liquidity filter trims to ~2,000+
    """
    syms: list[str] = []
    if which in ("sp500", "both", "all"):
        syms += load_sp500()
    if which in ("russell", "both", "all"):
        syms += load_russell2000()
    if which in ("nasdaq", "all"):
        syms += load_nasdaq()
    if which == "all":
        syms += load_other_listed()
    # dedup preserving order
    seen, out = set(), []
    for s in syms:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _fast_info_check(ticker: str) -> tuple[str, bool, float, float, float]:
    """Return (ticker, passes, price, avg_volume, market_cap) via fast_info."""
    try:
        fi = yf.Ticker(ticker).fast_info
        price = float(fi.get("last_price") or fi.get("lastPrice") or 0) \
            if hasattr(fi, "get") else float(fi.last_price or 0)
    except Exception:  # noqa: BLE001
        try:
            fi = yf.Ticker(ticker).fast_info
            price = float(fi.last_price or 0)
        except Exception:  # noqa: BLE001
            return ticker, False, 0.0, 0.0, 0.0
    # volume proxy
    vol = 0.0
    for attr in ("three_month_average_volume", "ten_day_average_volume",
                 "last_volume"):
        try:
            v = getattr(fi, attr, None)
            if v:
                vol = float(v)
                break
        except Exception:  # noqa: BLE001
            continue
    mcap = 0.0
    try:
        mc = getattr(fi, "market_cap", None)
        if mc:
            mcap = float(mc)
    except Exception:  # noqa: BLE001
        mcap = 0.0
    passes = price > MIN_PRICE and vol > MIN_AVG_VOL
    return ticker, passes, price, vol, mcap


def prefilter_universe(tickers: list[str], workers: int = 20
                       ) -> tuple[list[str], dict[str, dict]]:
    """Threaded fast_info pre-filter: price > $10 and avg volume > 300K.

    Returns (kept_tickers, {ticker: {price, vol, mcap}}).
    """
    kept: list[str] = []
    meta: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fast_info_check, t): t for t in tickers}
        for fut in tqdm(as_completed(futs), total=len(futs),
                        desc="Pre-filter"):
            try:
                tkr, passes, price, vol, mcap = fut.result()
                meta[tkr] = {"price": price, "vol": vol, "mcap": mcap}
                if passes:
                    kept.append(tkr)
            except Exception:  # noqa: BLE001
                continue
    print(f"  Pre-filter: {len(kept)}/{len(tickers)} passed "
          f"(price>${MIN_PRICE:.0f}, vol>{MIN_AVG_VOL:,})")
    return kept, meta


def filter_by_liquidity(daily: dict[str, pd.DataFrame],
                        exclude: tuple = (BENCHMARK,)) -> list[str]:
    """Pre-filter from already-downloaded history (rate-limit friendly).

    Keeps tickers with last close > $10 and 20-day avg volume > 300K.
    """
    kept = []
    for t, df in daily.items():
        if t in exclude:
            continue
        try:
            c = df["Close"].astype(float)
            v = df["Volume"].astype(float)
            if len(c) < 20:
                continue
            price = float(c.iloc[-1])
            vol20 = float(v.tail(20).mean())
            if price > MIN_PRICE and vol20 > MIN_AVG_VOL:
                kept.append(t)
        except Exception:  # noqa: BLE001
            continue
    return kept


def fetch_market_caps(tickers, workers=10, throttle=0.05) -> dict[str, float]:
    """Best-effort market-cap lookup via fast_info (survivors only)."""
    out: dict[str, float] = {}

    def _one(t):
        time.sleep(throttle)
        try:
            mc = getattr(yf.Ticker(t).fast_info, "market_cap", None)
            return t, (float(mc) if mc else None)
        except Exception:  # noqa: BLE001
            return t, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, t) for t in tickers]
        for fut in tqdm(as_completed(futs), total=len(futs), desc="Mkt cap"):
            try:
                t, mc = fut.result()
                out[t] = mc
            except Exception:  # noqa: BLE001
                continue
    return out


# ============================================================================
# MODULE 2 — DATA FETCHER (+ SQLite cache)
# ============================================================================

def _init_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS prices(
               ticker TEXT, date TEXT, interval TEXT,
               open REAL, high REAL, low REAL, close REAL, volume REAL,
               PRIMARY KEY (ticker, date, interval))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fetch_meta(
               ticker TEXT, interval TEXT, last_updated TEXT,
               PRIMARY KEY (ticker, interval))"""
    )
    conn.commit()
    return conn


def _meta_fresh(conn, ticker, interval, max_age_hours) -> bool:
    row = conn.execute(
        "SELECT last_updated FROM fetch_meta WHERE ticker=? AND interval=?",
        (ticker, interval),
    ).fetchone()
    if not row or not row[0]:
        return False
    try:
        ts = datetime.fromisoformat(row[0])
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - ts
    return age < timedelta(hours=max_age_hours)


def _store_prices(conn, ticker, interval, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    rows = []
    for idx, r in df.iterrows():
        d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
        try:
            rows.append((ticker, d, interval,
                         _f(r.get("Open")), _f(r.get("High")),
                         _f(r.get("Low")), _f(r.get("Close")),
                         _f(r.get("Volume"))))
        except Exception:  # noqa: BLE001
            continue
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?)", rows
        )
    conn.execute(
        "INSERT OR REPLACE INTO fetch_meta VALUES (?,?,?)",
        (ticker, interval, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _f(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _load_prices(conn, ticker, interval) -> pd.DataFrame | None:
    rows = conn.execute(
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE ticker=? AND interval=? ORDER BY date",
        (ticker, interval),
    ).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(
        rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]
    )
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df.dropna(subset=["Close"])


def _split_group_by(data: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Extract a single ticker's OHLCV frame from a grouped yf.download result."""
    if data is None or data.empty:
        return None
    try:
        if isinstance(data.columns, pd.MultiIndex):
            if ticker in data.columns.get_level_values(0):
                sub = data[ticker].copy()
            else:
                return None
        else:
            sub = data.copy()
    except Exception:  # noqa: BLE001
        return None
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume")
            if c in sub.columns]
    sub = sub[keep].dropna(how="all")
    return sub if not sub.empty else None


def fetch_history(conn, tickers, interval, period, max_age_hours,
                  batch_size=120) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for all tickers (cached), returning {ticker: DataFrame}."""
    need = [t for t in tickers if not _meta_fresh(conn, t, interval,
                                                   max_age_hours)]
    fresh = [t for t in tickers if t not in need]
    print(f"  [{interval}] cached-fresh: {len(fresh)}, to-download: {len(need)}")

    failed = []
    for i in tqdm(range(0, len(need), batch_size),
                  desc=f"Download {interval}",
                  total=math.ceil(len(need) / batch_size) if need else 0):
        batch = need[i:i + batch_size]
        try:
            data = yf.download(
                batch, period=period, interval=interval,
                group_by="ticker", auto_adjust=True, threads=True,
                progress=False,
            )
        except Exception as e:  # noqa: BLE001
            _log_failed(batch, f"download {interval}: {e}")
            failed += batch
            continue
        time.sleep(0.3)  # gentle pacing between batches (avoid 429s)
        for t in batch:
            sub = _split_group_by(data, t)
            if sub is None and len(batch) == 1:
                # single-ticker download → flat (non-grouped) columns
                sub = data if data is not None and not data.empty else None
            if sub is None or sub.empty:
                failed.append(t)
                _log_failed([t], f"no {interval} data")
                continue
            _store_prices(conn, t, interval, sub)

    # load everything (fresh + newly downloaded) from cache
    out = {}
    for t in tickers:
        df = _load_prices(conn, t, interval)
        if df is not None and len(df) > 10:
            out[t] = df
    return out


def _log_failed(tickers, reason):
    try:
        with open(FAILED_LOG, "a") as fh:
            stamp = datetime.now().isoformat(timespec="seconds")
            for t in tickers:
                fh.write(f"{stamp}\t{t}\t{reason}\n")
    except Exception:  # noqa: BLE001
        pass


def fetch_fundamentals(tickers, workers=8, throttle=0.25) -> dict[str, dict]:
    """Threaded ticker.info pull for fundamental fields.

    Gentle by default (few workers + per-call throttle + one backoff retry) —
    a 20-worker burst on ~2k tickers gets the whole batch 429'd by Yahoo.
    """
    out: dict[str, dict] = {}

    def _one(t):
        for attempt in range(2):
            time.sleep(throttle)
            try:
                info = yf.Ticker(t).info or {}
                if info.get("sector") or info.get("shortName") \
                        or info.get("marketCap"):
                    return t, {
                        "shortName": info.get("shortName")
                        or info.get("longName") or t,
                        "sector": info.get("sector") or "—",
                        "marketCap": info.get("marketCap"),
                        "earningsGrowth": info.get("earningsGrowth"),
                        "revenueGrowth": info.get("revenueGrowth"),
                        "trailingEps": info.get("trailingEps"),
                        "recommendationMean": info.get("recommendationMean"),
                        "numberOfAnalystOpinions":
                            info.get("numberOfAnalystOpinions"),
                    }
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.0 + attempt)   # backoff before the single retry
        _log_failed([t], "fundamentals fetch failed")
        return t, {}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, t) for t in tickers]
        for fut in tqdm(as_completed(futs), total=len(futs),
                        desc="Fundamentals"):
            try:
                t, d = fut.result()
                out[t] = d
            except Exception:  # noqa: BLE001
                continue
    return out


# ============================================================================
# Indicator helpers
# ============================================================================

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range — simple rolling mean of True Range (pure pandas)."""
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l).abs(),
                    (h - prev_c).abs(),
                    (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def last(s: pd.Series, default=np.nan):
    s = s.dropna()
    return s.iloc[-1] if len(s) else default


# ============================================================================
# MODULE 3 — SCORING ENGINE
# ============================================================================

def score_ticker(daily: pd.DataFrame, weekly: pd.DataFrame | None,
                 spy_daily: pd.DataFrame, fund: dict,
                 skip_fundamentals: bool) -> dict | None:
    """Compute the full 0-100 score and component detail for one ticker."""
    if daily is None or len(daily) < 200:
        return None
    c = daily["Close"].astype(float)
    v = daily["Volume"].astype(float)
    price = float(c.iloc[-1])
    if not math.isfinite(price) or price <= 0:
        return None

    sma50 = sma(c, 50); sma150 = sma(c, 150); sma200 = sma(c, 200)
    ema20 = ema(c, 20); ema50 = ema(c, 50)
    s50, s150, s200 = last(sma50), last(sma150), last(sma200)
    tags = []

    # ---- BLOCK A: Trend Template (30) ----
    A = 0.0
    a_hits = 0
    if price > s200: A += 5; a_hits += 1
    if price > s150: A += 5; a_hits += 1
    if price > s50:  A += 5; a_hits += 1
    if s50 > s150 > s200: A += 5; a_hits += 1
    s200_20ago = sma200.dropna()
    slope200 = (s200_20ago.iloc[-1] - s200_20ago.iloc[-21]) \
        if len(s200_20ago) > 21 else np.nan
    if math.isfinite(slope200) and slope200 > 0: A += 5; a_hits += 1
    hi_52w = float(c.tail(252).max())
    within_25 = hi_52w > 0 and price >= hi_52w * 0.75
    if within_25: A += 5; a_hits += 1
    if a_hits >= 5:
        tags.append("Stage2")

    # ---- BLOCK B: Weinstein Stage 2 (weekly) (15) ----
    B = 0.0
    b_full = False
    if weekly is not None and len(weekly) >= 30:
        wc = weekly["Close"].astype(float)
        wprice = float(wc.iloc[-1])
        sma30w = sma(wc, 30)
        s30 = last(sma30w)
        b_hits = 0
        if math.isfinite(s30) and wprice > s30: B += 5; b_hits += 1
        s30_series = sma30w.dropna()
        slope30 = (s30_series.iloc[-1] - s30_series.iloc[-11]) \
            if len(s30_series) > 11 else np.nan
        if math.isfinite(slope30) and slope30 > 0: B += 5; b_hits += 1
        # base breakout: 12+ wk where close stayed within a 30% band, and
        # current price within 5% of base top
        base = wc.tail(16)
        if len(base) >= 12:
            bt, bb = float(base.max()), float(base.min())
            if bb > 0 and (bt - bb) / bb <= 0.30 and wprice >= bt * 0.95:
                B += 5; b_hits += 1
        b_full = b_hits == 3

    # ---- BLOCK C: Pullback Quality (20) ----
    C = 0.0
    c_full = False
    # C1: pulled back to 20/50 EMA in last 10 bars, now bouncing
    e20 = ema20.tail(10); e50 = ema50.tail(10)
    lows10 = daily["Low"].astype(float).tail(10)
    touched = bool(((lows10 <= e20 * 1.02) | (lows10 <= e50 * 1.02)).any())
    bouncing = len(c) > 3 and price > float(c.iloc[-4])
    c1 = touched and bouncing
    if c1: C += 8
    # recent swing high over last ~30 bars
    swing_hi = float(c.tail(30).max())
    pullback_pct = (swing_hi - price) / swing_hi * 100 if swing_hi > 0 else 0.0
    # C2: volume during the falling stretch below 20d avg
    vol20 = last(sma(v, 20))
    falling = c.tail(7)
    fall_mask = falling.diff() < 0
    pb_vol = float(v.tail(7)[fall_mask.values].mean()) if fall_mask.any() else np.nan
    c2 = math.isfinite(pb_vol) and math.isfinite(vol20) and pb_vol < vol20
    if c2: C += 7
    # C3: pullback depth 5-20%
    c3 = 5.0 <= pullback_pct <= 20.0
    if c3: C += 5
    c_full = c1 and c2 and c3
    if c_full:
        tags.append("Pullback")

    # ---- BLOCK D: VCP Signature (15) ----
    D = 0.0
    atr14 = atr(daily, 14)
    atr_now = last(atr14)
    atr_series = atr14.dropna()
    atr_30ago = atr_series.iloc[-31] if len(atr_series) > 31 else np.nan
    d1 = math.isfinite(atr_now) and math.isfinite(atr_30ago) \
        and atr_now < atr_30ago * 0.75
    if d1: D += 5
    # D2: >=2 successive contracting swings in last 60 bars
    d2 = _has_contractions(c.tail(60))
    if d2: D += 5
    # D3: volume dry-up
    vol10 = last(sma(v, 10)); vol30 = last(sma(v, 30))
    d3 = math.isfinite(vol10) and math.isfinite(vol30) and vol10 < vol30 * 0.75
    if d3: D += 5
    vcp_score = D
    if D == 15:
        tags.append("VCP")
    if d3:
        tags.append("Low Vol Dry")

    # ---- BLOCK E: Relative Strength (10) ----
    E = 0.0
    rs_ratio = np.nan
    rs_outperf = False
    spy_c = spy_daily["Close"].astype(float)
    if len(c) > 63 and len(spy_c) > 63:
        stock_ret = price / float(c.iloc[-64]) - 1
        spy_ret = float(spy_c.iloc[-1]) / float(spy_c.iloc[-64]) - 1
        if stock_ret > spy_ret:
            E += 5; rs_outperf = True
        # RS line rising
        aligned = pd.concat([c, spy_c], axis=1, join="inner").dropna()
        if len(aligned) > 21:
            ratio = aligned.iloc[:, 0] / aligned.iloc[:, 1]
            rs_ratio = float(ratio.iloc[-1])
            if ratio.iloc[-1] > ratio.iloc[-21]:
                E += 5
    if E == 10:
        tags.append("RS Leader")

    # ---- BLOCK F: Fundamentals (10) ----
    if skip_fundamentals or not fund:
        F = 5.0  # neutral
        eps_gr = rev_gr = None
    else:
        F, eps_gr, rev_gr, high_eps = _score_fundamentals(fund)
        if high_eps:
            tags.append("High EPS")

    total = A + B + C + D + E + F
    grade = grade_for(total)
    if grade is None:
        return None

    return {
        "score": round(total, 1),
        "grade": grade,
        "price": price,
        "sma50": s50, "sma150": s150, "sma200": s200,
        "vs200": (price / s200 - 1) * 100 if math.isfinite(s200) and s200 else 0,
        "vs50": (price / s50 - 1) * 100 if math.isfinite(s50) and s50 else 0,
        "pullback_pct": pullback_pct,
        "atr": atr_now if math.isfinite(atr_now) else 0.0,
        "avg_vol_1m": float(v.tail(21).mean()) if len(v) >= 5 else None,
        "vcp_score": vcp_score,
        "rs_ratio": rs_ratio,
        "rs_outperf": rs_outperf,
        "eps_gr": (eps_gr * 100) if isinstance(eps_gr, (int, float))
                  and eps_gr is not None else None,
        "rev_gr": (rev_gr * 100) if isinstance(rev_gr, (int, float))
                  and rev_gr is not None else None,
        "blocks": {"A": A, "B": B, "C": C, "D": D, "E": E, "F": F},
        "tags": tags,
    }


def _has_contractions(closes: pd.Series) -> bool:
    """At least 2 successive swing ranges each < 0.8 × the prior range."""
    s = closes.dropna().values
    if len(s) < 15:
        return False
    # identify local pivots with a small window
    pivots = []
    w = 3
    for i in range(w, len(s) - w):
        seg = s[i - w:i + w + 1]
        if s[i] == seg.max() or s[i] == seg.min():
            pivots.append(s[i])
    if len(pivots) < 4:
        return False
    # successive swing ranges
    ranges = [abs(pivots[i] - pivots[i - 1]) for i in range(1, len(pivots))]
    contractions = 0
    for i in range(1, len(ranges)):
        if ranges[i] < ranges[i - 1] * 0.8:
            contractions += 1
    return contractions >= 2


def _score_fundamentals(fund: dict):
    """Return (points, eps_growth, rev_growth, high_eps_flag)."""
    pts = 0.0
    eps_gr = fund.get("earningsGrowth")
    rev_gr = fund.get("revenueGrowth")
    rec = fund.get("recommendationMean")
    n_an = fund.get("numberOfAnalystOpinions")

    any_data = any(x is not None for x in (eps_gr, rev_gr, rec))
    if not any_data:
        return 5.0, None, None, False  # neutral when all missing

    high_eps = False
    # F1: EPS growth YoY > 20%
    if isinstance(eps_gr, (int, float)) and eps_gr is not None:
        if eps_gr > 0.20:
            pts += 4
            high_eps = True
    # F2: Revenue growth YoY > 15%
    if isinstance(rev_gr, (int, float)) and rev_gr is not None:
        if rev_gr > 0.15:
            pts += 3
    # F3: analyst consensus bullish
    if isinstance(rec, (int, float)) and rec is not None and \
            isinstance(n_an, (int, float)) and n_an is not None:
        if rec < 2.5 and n_an >= 3:
            pts += 3
    return pts, eps_gr, rev_gr, high_eps


def grade_for(score: float):
    if score >= 85: return "A+"
    if score >= 70: return "A"
    if score >= 55: return "B+"
    if score >= 40: return "B"
    return None


# ============================================================================
# MODULE 4 — RISK CALCULATOR
# ============================================================================

def compute_risk(entry: float, atr_val: float, account: float,
                 risk_pct: float) -> dict:
    if not math.isfinite(atr_val) or atr_val <= 0:
        atr_val = entry * 0.02  # 2% fallback
    stop = entry - 1.5 * atr_val
    risk_per_share = max(entry - stop, 1e-9)
    stop_pct = risk_per_share / entry * 100
    t1 = entry + 2.0 * atr_val
    t2 = entry + 4.0 * atr_val
    rr = (t2 - entry) / risk_per_share
    dollar_risk = account * (risk_pct / 100.0)
    shares = int(math.floor(dollar_risk / risk_per_share))
    pos_val = shares * entry
    pos_pct = pos_val / account * 100 if account else 0
    return {
        "entry": entry, "stop": stop, "stop_pct": stop_pct,
        "t1": t1, "t2": t2, "rr": rr,
        "shares": shares, "pos_val": pos_val, "pos_pct": pos_pct,
    }


# ============================================================================
# Market regime
# ============================================================================

def market_regime() -> tuple[bool, str]:
    """Bull regime if SPY > its 10-month EMA."""
    try:
        spy = yf.download(BENCHMARK, period="5y", interval="1mo",
                          auto_adjust=True, progress=False)
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.get_level_values(0)
        c = spy["Close"].astype(float).dropna()
        e10 = ema(c, 10)
        bull = float(c.iloc[-1]) > float(e10.iloc[-1])
        return bull, ("BULL REGIME ✓" if bull else "CAUTION — REDUCE SIZE")
    except Exception:  # noqa: BLE001
        return True, "REGIME UNKNOWN"


# ============================================================================
# SVG CHART RENDERING (AskLivermore-style, fully self-contained / no CDN)
# ============================================================================

UP_COL = "#1a7f37"
DN_COL = "#cf222e"


def render_thumb(df: pd.DataFrame, w: int = 240, h: int = 66,
                 bars: int = 55) -> str:
    """Small inline candlestick sparkline for the results row."""
    try:
        sub = df.tail(bars).dropna(subset=["Open", "High", "Low", "Close"])
        if len(sub) < 5:
            return ""
        o = sub["Open"].astype(float).values
        hi = sub["High"].astype(float).values
        lo = sub["Low"].astype(float).values
        cl = sub["Close"].astype(float).values
        pmin, pmax = float(lo.min()), float(hi.max())
        rng = (pmax - pmin) or 1.0
        pad = 3
        n = len(sub)
        cw = (w - 2 * pad) / n

        def y(p):
            return pad + (pmax - p) / rng * (h - 2 * pad)

        bw = max(1.0, cw * 0.6)
        parts = []
        for i in range(n):
            cx = pad + i * cw + cw / 2
            col = UP_COL if cl[i] >= o[i] else DN_COL
            parts.append(
                f'<line x1="{cx:.1f}" y1="{y(hi[i]):.1f}" x2="{cx:.1f}" '
                f'y2="{y(lo[i]):.1f}" stroke="{col}" stroke-width="0.7"/>')
            yo, yc = y(o[i]), y(cl[i])
            top = min(yo, yc)
            bh = max(0.8, abs(yc - yo))
            parts.append(
                f'<rect x="{cx-bw/2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" fill="{col}"/>')
        return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
                f'xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>')
    except Exception:  # noqa: BLE001
        return ""


def render_full(df: pd.DataFrame, risk: dict, w: int = 960, h: int = 430,
                bars: int = 130) -> str:
    """Large candlestick chart: MA50/MA200, volume panel, risk lines."""
    try:
        closes = df["Close"].astype(float)
        ma50f = sma(closes, 50)
        ma200f = sma(closes, 200)
        sub = df.tail(bars).dropna(subset=["Open", "High", "Low", "Close"])
        if len(sub) < 10:
            return ""
        o = sub["Open"].astype(float)
        hi = sub["High"].astype(float)
        lo = sub["Low"].astype(float)
        cl = sub["Close"].astype(float)
        vol = sub["Volume"].astype(float).fillna(0)
        ma50 = ma50f.reindex(sub.index)
        ma200 = ma200f.reindex(sub.index)

        prices = [float(hi.max()), float(lo.min())]
        for k in ("entry", "stop", "t1", "t2"):
            v = risk.get(k)
            if v is not None and math.isfinite(v):
                prices.append(float(v))
        pmax, pmin = max(prices), min(prices)
        span = (pmax - pmin) or 1.0
        pmax += span * 0.04
        pmin -= span * 0.04

        padL, padR, padT = 8, 66, 12
        x0, x1 = padL, w - padR
        py0, py1 = padT, int(h * 0.70)
        vy0, vy1 = int(h * 0.77), h - 22
        n = len(sub)
        cw = (x1 - x0) / n

        def yP(p):
            return py0 + (pmax - p) / (pmax - pmin) * (py1 - py0)

        parts = [f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>']
        # price grid + axis labels
        for frac in (0, .25, .5, .75, 1):
            yy = py0 + frac * (py1 - py0)
            pp = pmax - frac * (pmax - pmin)
            parts.append(
                f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" '
                f'stroke="#eaeef2" stroke-width="1"/>')
            parts.append(
                f'<text x="{x1+5}" y="{yy+3:.1f}" font-size="10" '
                f'fill="#57606a">{pp:.2f}</text>')

        bw = max(1.4, cw * 0.62)
        ov, hv, lv, cv = o.values, hi.values, lo.values, cl.values
        for i in range(n):
            cx = x0 + i * cw + cw / 2
            col = UP_COL if cv[i] >= ov[i] else DN_COL
            parts.append(
                f'<line x1="{cx:.1f}" y1="{yP(hv[i]):.1f}" x2="{cx:.1f}" '
                f'y2="{yP(lv[i]):.1f}" stroke="{col}" stroke-width="1"/>')
            yo, yc = yP(ov[i]), yP(cv[i])
            top = min(yo, yc)
            bh = max(1, abs(yc - yo))
            parts.append(
                f'<rect x="{cx-bw/2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" fill="{col}"/>')

        def poly(series, color):
            pts = []
            vals = series.values
            for i in range(n):
                vv = vals[i]
                if vv is None or (isinstance(vv, float) and not math.isfinite(vv)):
                    continue
                cx = x0 + i * cw + cw / 2
                pts.append(f"{cx:.1f},{yP(float(vv)):.1f}")
            if len(pts) > 1:
                parts.append(
                    f'<polyline points="{" ".join(pts)}" fill="none" '
                    f'stroke="{color}" stroke-width="1.5"/>')
        poly(ma50, "#0969da")
        poly(ma200, "#bc4c00")

        def hline(p, color, label):
            if p is None or not math.isfinite(p) or p > pmax or p < pmin:
                return
            yy = yP(p)
            parts.append(
                f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" '
                f'stroke="{color}" stroke-width="1" stroke-dasharray="5,3"/>')
            parts.append(
                f'<text x="{x0+4}" y="{yy-3:.1f}" font-size="9.5" '
                f'fill="{color}" font-weight="600">{label} {p:.2f}</text>')
        hline(risk.get("t2"), "#1a7f37", "T2")
        hline(risk.get("t1"), "#238636", "T1")
        hline(risk.get("entry"), "#57606a", "Entry")
        hline(risk.get("stop"), "#cf222e", "Stop")

        vmax = float(vol.max()) or 1.0
        vv = vol.values
        for i in range(n):
            cx = x0 + i * cw + cw / 2
            col = "#a7d3b0" if cv[i] >= ov[i] else "#f1b0ac"
            bh = (vv[i] / vmax) * (vy1 - vy0)
            parts.append(
                f'<rect x="{cx-bw/2:.1f}" y="{vy1-bh:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" fill="{col}"/>')

        parts.append(f'<text x="{x0}" y="{py0+10}" font-size="10.5" '
                     f'fill="#0969da" font-weight="600">— MA50</text>')
        parts.append(f'<text x="{x0+52}" y="{py0+10}" font-size="10.5" '
                     f'fill="#bc4c00" font-weight="600">— MA200</text>')
        try:
            parts.append(f'<text x="{x0}" y="{h-7}" font-size="9" '
                         f'fill="#57606a">{sub.index[0].strftime("%Y-%m-%d")}</text>')
            parts.append(f'<text x="{x1-58}" y="{h-7}" font-size="9" '
                         f'fill="#57606a">{sub.index[-1].strftime("%Y-%m-%d")}</text>')
        except Exception:  # noqa: BLE001
            pass
        return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
                f'preserveAspectRatio="xMidYMid meet" '
                f'xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>')
    except Exception:  # noqa: BLE001
        return ""


def render_gallery(df: pd.DataFrame, risk: dict, label: str = "",
                   w: int = 440, h: int = 240, bars: int = 45) -> str:
    """Compact candlestick mini-chart (SMA50/SMA200/EMA21 + entry/stop) for the
    gallery grid. Kept small so hundreds embed cheaply in the JSON feed."""
    try:
        closes = df["Close"].astype(float)
        ma50f = sma(closes, 50)
        ma200f = sma(closes, 200)
        ema21f = ema(closes, 21)
        sub = df.tail(bars).dropna(subset=["Open", "High", "Low", "Close"])
        if len(sub) < 10:
            return ""
        o = sub["Open"].astype(float).values
        hi = sub["High"].astype(float).values
        lo = sub["Low"].astype(float).values
        cl = sub["Close"].astype(float).values
        ma50 = ma50f.reindex(sub.index).values
        ma200 = ma200f.reindex(sub.index).values
        ema21 = ema21f.reindex(sub.index).values

        prices = [float(hi.max()), float(lo.min())]
        for k in ("entry", "stop"):
            v = risk.get(k)
            if v is not None and math.isfinite(v):
                prices.append(float(v))
        pmax, pmin = max(prices), min(prices)
        span = (pmax - pmin) or 1.0
        pmax += span * 0.05
        pmin -= span * 0.05

        padT, padB, x0 = 18, 6, 4
        x1 = w - 4
        py0, py1 = padT, h - padB
        n = len(sub)
        cw = (x1 - x0) / n

        def yP(p):
            return py0 + (pmax - p) / (pmax - pmin) * (py1 - py0)

        parts = [f'<rect width="{w}" height="{h}" fill="#fff"/>']
        bw = max(1.0, cw * 0.6)
        up_w, dn_w = [], []   # wick path segments
        up_b, dn_b = [], []   # body rects
        for i in range(n):
            cx = round(x0 + i * cw + cw / 2)
            yo, yc = round(yP(o[i])), round(yP(cl[i]))
            yh, yl = round(yP(hi[i])), round(yP(lo[i]))
            up = cl[i] >= o[i]
            (up_w if up else dn_w).append(f"M{cx} {yh}V{yl}")
            top = min(yo, yc)
            (up_b if up else dn_b).append(
                f'<rect x="{cx-bw/2:.0f}" y="{top}" width="{bw:.1f}" '
                f'height="{max(1, abs(yc-yo))}"/>')
        parts.append(f'<path d="{"".join(up_w)}" stroke="{UP_COL}" '
                     f'stroke-width="0.8" fill="none"/>')
        parts.append(f'<g fill="{UP_COL}">{"".join(up_b)}</g>')
        parts.append(f'<path d="{"".join(dn_w)}" stroke="{DN_COL}" '
                     f'stroke-width="0.8" fill="none"/>')
        parts.append(f'<g fill="{DN_COL}">{"".join(dn_b)}</g>')

        def poly(vals, color):
            pts = []
            for i in range(n):
                vv = vals[i]
                if vv is None or (isinstance(vv, float) and not math.isfinite(vv)):
                    continue
                cx = round(x0 + i * cw + cw / 2)
                pts.append(f"{cx},{round(yP(float(vv)))}")
            if len(pts) > 1:
                parts.append(f'<polyline points="{" ".join(pts)}" fill="none" '
                             f'stroke="{color}" stroke-width="1.3"/>')
        poly(ema21, "#8250df")   # EMA21 purple
        poly(ma50, "#0969da")    # SMA50 blue
        poly(ma200, "#bc4c00")   # SMA200 orange

        def hline(p, color):
            if p is None or not math.isfinite(p) or p > pmax or p < pmin:
                return
            yy = yP(p)
            parts.append(
                f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" '
                f'stroke="{color}" stroke-width="0.9" stroke-dasharray="4,3"/>')
        hline(risk.get("entry"), "#57606a")
        hline(risk.get("stop"), "#cf222e")

        if label:
            parts.append(f'<text x="{x0+2}" y="13" font-size="12" '
                         f'font-weight="700" fill="#1f2328">{label}</text>')
        parts.append(f'<text x="{x1-2}" y="13" font-size="10" '
                     f'text-anchor="end" fill="#57606a">'
                     f'{float(cl[-1]):.2f}</text>')
        return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
                f'preserveAspectRatio="xMidYMid meet" '
                f'xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>')
    except Exception:  # noqa: BLE001
        return ""


def fmt_mcap(v):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v) \
            or v <= 0:
        return "—"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def fmt_vol(v):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v) \
            or v <= 0:
        return "—"
    if v >= 1e6:
        return f"{v/1e6:.1f}M"
    if v >= 1e3:
        return f"{v/1e3:.0f}K"
    return f"{v:.0f}"


# ============================================================================
# MODULE 5 — HTML REPORT GENERATOR
# ============================================================================

def _fmt(v, spec="{:.2f}", dash="—"):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return dash
    try:
        return spec.format(v)
    except Exception:  # noqa: BLE001
        return dash


def generate_html(results: list[dict], meta: dict, out_path: str) -> str:
    bull = meta["bull"]
    regime_txt = meta["regime_txt"]
    regime_cls = "bull" if bull else "bear"

    rows_html = []
    for i, r in enumerate(results):
        gc = GRADE_COLORS.get(r["grade"], "#57606a")
        tags = "".join(f'<span class="tag">{t}</span>' for t in r["tags"])
        thumb = r.get("thumb") or ""
        mcap = r.get("mcap")
        avgvol = r.get("avg_vol_1m")
        # TradingView uses '.' for share classes (yfinance uses '-')
        tv_sym = r["ticker"].replace("-", ".")
        tv = f"https://www.tradingview.com/chart/?symbol={tv_sym}"
        rows_html.append(f"""
<tr class="row" data-grade="{r['grade']}" data-score="{r['score']}"
    data-rs="{_fmt(r.get('rs_ratio'), '{:.4f}', '0')}"
    data-sector="{r['sector']}" data-ticker="{r['ticker']}"
    data-company="{r['company'].lower()}"
    data-mcap="{mcap if isinstance(mcap,(int,float)) and math.isfinite(mcap) else 0}"
    data-avgvol="{avgvol if isinstance(avgvol,(int,float)) and math.isfinite(avgvol) else 0}"
    style="border-left:4px solid {gc}" onclick="toggleChart(this,'{i}')">
  <td class="tk">{r['ticker']}</td>
  <td class="co">{r['company']}</td>
  <td>{r['sector']}</td>
  <td class="thumb">{thumb}</td>
  <td class="num"><b>{_fmt(r['score'], '{:.0f}')}</b></td>
  <td><span class="grade" style="background:{gc}">{r['grade']}</span></td>
  <td class="num">{_fmt(r['price'], '${:.2f}')}</td>
  <td class="num">{fmt_mcap(mcap)}</td>
  <td class="num">{fmt_vol(avgvol)}</td>
  <td class="num {pmcls(r['vs200'])}">{_fmt(r['vs200'], '{:+.1f}%')}</td>
  <td class="num {pmcls(r['vs50'])}">{_fmt(r['vs50'], '{:+.1f}%')}</td>
  <td class="num">{_fmt(r['pullback_pct'], '{:.1f}%')}</td>
  <td class="num">{_fmt(r['atr'], '{:.2f}')}</td>
  <td class="num">{_fmt(r['vcp_score'], '{:.0f}')}/15</td>
  <td class="num {pmcls(1 if r['rs_outperf'] else -1)}">{'&#9650;' if r['rs_outperf'] else '&#9655;'}</td>
  <td class="num">{_fmt(r.get('eps_gr'), '{:+.0f}%')}</td>
  <td class="num">{_fmt(r.get('rev_gr'), '{:+.0f}%')}</td>
  <td class="num">{_fmt(r['risk']['entry'], '${:.2f}')}</td>
  <td class="num">{_fmt(r['risk']['stop'], '${:.2f}')}</td>
  <td class="num neg">{_fmt(r['risk']['stop_pct'], '{:.1f}%')}</td>
  <td class="num">{_fmt(r['risk']['t1'], '${:.2f}')}</td>
  <td class="num">{_fmt(r['risk']['t2'], '${:.2f}')}</td>
  <td class="num">{_fmt(r['risk']['rr'], '{:.1f}')}</td>
  <td class="num">{_fmt(r['risk']['pos_pct'], '{:.1f}%')}</td>
  <td class="tags">{tags}</td>
</tr>
<tr class="chartrow" id="chart-{i}"><td colspan="25">
  <div class="chartbox">
    <div class="charttitle">{r['ticker']} &middot; {r['company']} &middot;
      Entry ${_fmt(r['risk']['entry'],'{:.2f}')} &middot;
      Stop ${_fmt(r['risk']['stop'],'{:.2f}')} ({_fmt(r['risk']['stop_pct'],'{:.1f}')}%) &middot;
      T1 ${_fmt(r['risk']['t1'],'{:.2f}')} &middot; T2 ${_fmt(r['risk']['t2'],'{:.2f}')} &middot;
      R:R {_fmt(r['risk']['rr'],'{:.1f}')}</div>
    <div class="chartsvg" data-symbol="{tv_sym}"></div>
    <div class="tvlink"><a href="{tv}" target="_blank" rel="noopener"
      onclick="event.stopPropagation()">Open {r['ticker']} on TradingView &#8599;</a></div>
  </div>
</td></tr>""")

    rows = "\n".join(rows_html)
    n_aplus = sum(1 for r in results if r["grade"] == "A+")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pullback Scanner &mdash; {meta['date']}</title>
<style>
:root{{--bg:#ffffff;--card:#ffffff;--bd:#d0d7de;--fg:#1f2328;--mut:#656d76;
--soft:#f6f8fa;--grn:#1a7f37;--red:#cf222e;--amb:#9a6700;--blue:#0969da;}}
*{{box-sizing:border-box}}
html,body{{background:#ffffff}}
body{{margin:0;color:var(--fg);
font:13px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}}
.wrap{{max-width:1850px;margin:0 auto;padding:20px}}
h1{{font-size:22px;margin:0 0 4px}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:16px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:12px;margin-bottom:16px}}
.metric{{background:var(--soft);border:1px solid var(--bd);border-radius:8px;
padding:14px 16px}}
.metric .v{{font-size:26px;font-weight:700}}
.metric .l{{color:var(--mut);font-size:12px;text-transform:uppercase;
letter-spacing:.04em}}
.regime{{display:inline-block;padding:8px 16px;border-radius:8px;font-weight:700;
margin-bottom:16px}}
.regime.bull{{background:#dafbe1;color:#1a7f37;border:1px solid #1a7f37}}
.regime.bear{{background:#ffebe9;color:#cf222e;border:1px solid #cf222e}}
.bar{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}}
.btn{{background:#fff;border:1px solid var(--bd);color:var(--fg);
padding:7px 14px;border-radius:6px;cursor:pointer;font-size:13px}}
.btn.active{{background:var(--grn);border-color:var(--grn);color:#fff}}
label.fld{{color:var(--mut);font-size:12px;display:flex;align-items:center;gap:5px}}
select,input{{background:#fff;border:1px solid var(--bd);color:var(--fg);
padding:7px 10px;border-radius:6px;font-size:13px}}
input{{min-width:200px}}
.tablewrap{{overflow-x:auto;border:1px solid var(--bd);border-radius:8px}}
table{{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}}
th{{position:sticky;top:0;background:var(--soft);color:var(--mut);text-align:right;
padding:9px 8px;border-bottom:1px solid var(--bd);font-weight:600;
text-transform:uppercase;font-size:10.5px;letter-spacing:.03em;cursor:default}}
th:nth-child(1),th:nth-child(2),th:nth-child(3),th:nth-child(4),
th:last-child{{text-align:left}}
td{{padding:7px 8px;border-bottom:1px solid #eaeef2}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.thumb{{padding:2px 6px;line-height:0}}
td.thumb svg{{display:block;border:1px solid #eaeef2;border-radius:4px;
background:#fff}}
.row{{cursor:pointer}}
.row:hover{{background:var(--soft)}}
.tk{{font-weight:700;color:var(--blue)}}
.co{{max-width:170px;overflow:hidden;text-overflow:ellipsis}}
.grade{{color:#fff;padding:2px 8px;border-radius:10px;font-weight:700;
font-size:11px}}
.pos{{color:var(--grn)}}.neg{{color:var(--red)}}
.tag{{display:inline-block;background:var(--soft);border:1px solid var(--bd);
color:var(--mut);border-radius:4px;padding:1px 6px;margin:1px;font-size:10px}}
.chartrow{{display:none}}
.chartrow.open{{display:table-row}}
.chartbox{{background:#fff;padding:10px 6px}}
.charttitle{{font-size:12px;color:var(--fg);font-weight:600;margin-bottom:6px}}
.chartsvg{{width:100%;height:760px}}
.chartsvg iframe,.chartsvg .tradingview-widget-container{{width:100%;
height:100%;border:1px solid var(--bd);border-radius:6px}}
.tvlink{{margin-top:6px}}
.tvlink a{{color:var(--blue);font-size:12px;text-decoration:none}}
.tvlink a:hover{{text-decoration:underline}}
footer{{color:var(--mut);font-size:12px;margin-top:20px;padding-top:14px;
border-top:1px solid var(--bd)}}
.disc{{color:#8c959f;font-size:11px;margin-top:6px}}
.count{{color:var(--mut);font-size:12px;margin-left:auto}}
</style></head>
<body><div class="wrap">
<h1>Pullback Scanner &mdash; {meta['date']}</h1>
<div class="sub">A+ setup scan across {meta['universe_label']} &middot; Yahoo Finance via yfinance</div>
<div class="regime {regime_cls}">{regime_txt}</div>
<div class="metrics">
  <div class="metric"><div class="v">{meta['total_scanned']:,}</div><div class="l">Total Scanned</div></div>
  <div class="metric"><div class="v">{meta['passed_filter']:,}</div><div class="l">Passed Filter</div></div>
  <div class="metric"><div class="v">{n_aplus}</div><div class="l">A+ Setups</div></div>
  <div class="metric"><div class="v">{meta['scan_time']}</div><div class="l">Scan Time</div></div>
</div>
<div class="bar">
  <button class="btn active" id="g-ALL" onclick="setGrade(this,'ALL')">ALL ({len(results)})</button>
  <button class="btn" id="g-A+" onclick="setGrade(this,'A+')">A+ ({n_aplus})</button>
  <button class="btn" id="g-A" onclick="setGrade(this,'A')">A</button>
  <button class="btn" id="g-B+" onclick="setGrade(this,'B+')">B+</button>
  <label class="fld">Mkt Cap
    <select id="mcap" onchange="applyFilters()">
      <option value="all">All</option>
      <option value="200-">Mega &ge; $200B</option>
      <option value="10-200">Large $10&ndash;200B</option>
      <option value="2-10">Mid $2&ndash;10B</option>
      <option value="0.3-2">Small $300M&ndash;2B</option>
      <option value="0-0.3">Micro &lt; $300M</option>
    </select>
  </label>
  <label class="fld">Avg Vol 1m
    <select id="avol" onchange="applyFilters()">
      <option value="0">All</option>
      <option value="2000000">&ge; 2M</option>
      <option value="1000000">&ge; 1M</option>
      <option value="500000">&ge; 500K</option>
      <option value="300000">&ge; 300K</option>
    </select>
  </label>
  <label class="fld">Sort
    <select id="sort" onchange="sortBy(this.value)">
      <option value="score">Score &darr;</option>
      <option value="rs">RS &darr;</option>
      <option value="mcap">Mkt Cap &darr;</option>
      <option value="avgvol">Avg Vol &darr;</option>
      <option value="grade">Grade</option>
      <option value="sector">Sector</option>
    </select>
  </label>
  <input id="search" placeholder="Search ticker / company&hellip;" oninput="applyFilters()">
  <span class="count" id="count"></span>
</div>
<div class="tablewrap"><table id="tbl"><thead><tr>
<th>Ticker</th><th>Company</th><th>Sector</th><th>Chart</th><th>Score</th>
<th>Grade</th><th>Price</th><th>Mkt Cap</th><th>Avg Vol</th>
<th>vs200MA</th><th>vs50MA</th><th>Pullbk</th><th>ATR</th>
<th>VCP</th><th>RS</th><th>EPS%</th><th>Rev%</th>
<th>Entry</th><th>Stop</th><th>Stop%</th><th>T1</th><th>T2</th><th>R:R</th>
<th>Pos%</th><th>Setup Tags</th>
</tr></thead><tbody>
{rows}
</tbody></table></div>
<footer>
Generated {generated} &middot; Data: Yahoo Finance via yfinance &middot;
{meta['total_scanned']:,} scanned / {len(results)} graded
<div class="disc">For research and educational purposes only. Not investment advice.
Always do your own due diligence and manage risk.</div>
</footer>
</div>
<script>
var fGrade='ALL';
// Advanced Chart base config — SMA50, SMA200, EMA21 overlaid
var TV_CFG={{"autosize":true,"interval":"D","timezone":"Etc/UTC",
"theme":"light","style":"1","locale":"en","allow_symbol_change":true,
"hide_side_toolbar":false,"withdateranges":true,"details":false,
"save_image":true,"studies":[
  {{"id":"MASimple@tv-basicstudies","inputs":{{"length":50}}}},
  {{"id":"MASimple@tv-basicstudies","inputs":{{"length":200}}}},
  {{"id":"MAExp@tv-basicstudies","inputs":{{"length":21}}}}
]}};
function setGrade(btn,g){{
  document.querySelectorAll('.bar .btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active'); fGrade=g; applyFilters();
}}
function closeAllCharts(){{
  document.querySelectorAll('.chartrow.open').forEach(c=>{{
    c.classList.remove('open');
    var b=c.querySelector('.chartsvg'); if(b) b.innerHTML='';
  }});
}}
function applyFilters(){{
  var mc=document.getElementById('mcap').value;
  var av=+document.getElementById('avol').value;
  var q=document.getElementById('search').value.toLowerCase().trim();
  var lo=0,hi=Infinity;
  if(mc!=='all'){{var p=mc.split('-');lo=(+p[0])*1e9;hi=(p[1]==='')?Infinity:(+p[1])*1e9;}}
  var shown=0;
  document.querySelectorAll('#tbl tbody .row').forEach(r=>{{
    var g=r.dataset.grade, m=+r.dataset.mcap, v=+r.dataset.avgvol;
    var ok=(fGrade==='ALL'||g===fGrade);
    if(ok&&mc!=='all') ok=(m>=lo&&m<hi);
    if(ok&&av) ok=(v>=av);
    if(ok&&q) ok=(r.dataset.ticker.toLowerCase().includes(q)||r.dataset.company.includes(q));
    r.style.display=ok?'':'none';
    var cr=r.nextElementSibling;
    if(cr&&cr.classList.contains('chartrow')&&!ok&&cr.classList.contains('open')){{
      cr.classList.remove('open');
      var b=cr.querySelector('.chartsvg'); if(b) b.innerHTML='';
    }}
    if(ok) shown++;
  }});
  document.getElementById('count').textContent=shown+' shown';
}}
function sortBy(key){{
  var tb=document.querySelector('#tbl tbody');
  var rows=[...tb.querySelectorAll('.row')];
  closeAllCharts();
  rows.sort((a,b)=>{{
    if(key==='score') return b.dataset.score-a.dataset.score;
    if(key==='rs') return b.dataset.rs-a.dataset.rs;
    if(key==='mcap') return b.dataset.mcap-a.dataset.mcap;
    if(key==='avgvol') return b.dataset.avgvol-a.dataset.avgvol;
    if(key==='grade') return a.dataset.grade.localeCompare(b.dataset.grade);
    if(key==='sector') return a.dataset.sector.localeCompare(b.dataset.sector);
    return 0;
  }});
  rows.forEach(r=>{{
    var cr=r.nextElementSibling;
    tb.appendChild(r);
    if(cr&&cr.classList.contains('chartrow')) tb.appendChild(cr);
  }});
}}
function toggleChart(row,idx){{
  var cr=document.getElementById('chart-'+idx);
  if(!cr) return;
  var box=cr.querySelector('.chartsvg');
  if(cr.classList.contains('open')){{
    cr.classList.remove('open'); box.innerHTML='';   // unload widget
  }} else {{
    var cont=document.createElement('div');
    cont.className='tradingview-widget-container';
    var inner=document.createElement('div');
    inner.className='tradingview-widget-container__widget';
    inner.style.height='100%'; inner.style.width='100%';
    cont.appendChild(inner);
    var s=document.createElement('script');
    s.type='text/javascript'; s.async=true;
    s.src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    var cfg=Object.assign({{}},TV_CFG); cfg.symbol=box.dataset.symbol;
    s.text=JSON.stringify(cfg);
    cont.appendChild(s);
    box.appendChild(cont);
    cr.classList.add('open');
  }}
}}
applyFilters();
</script>
</body></html>"""

    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path


def pmcls(v):
    try:
        return "pos" if float(v) >= 0 else "neg"
    except (TypeError, ValueError):
        return ""


# ============================================================================
# ORCHESTRATION
# ============================================================================

def _sanitize(o):
    """Recursively convert numpy types / NaN / inf into JSON-safe values."""
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(x) for x in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    return o


def export_json(results, meta, path):
    """Write the scan results to a JSON file consumed by the Streamlit app."""
    import json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean = [{k: v for k, v in r.items() if k != "thumb"} for r in results]
    payload = {"meta": _sanitize(meta), "results": _sanitize(clean)}
    with open(path, "w") as fh:
        json.dump(payload, fh)
    return path


def main():
    ap = argparse.ArgumentParser(description="Pullback Scanner — A+ swing setups")
    ap.add_argument("--fast", action="store_true",
                    help="S&P 500 only, skip fundamentals, 4h cache")
    ap.add_argument("--universe",
                    choices=["sp500", "russell", "nasdaq", "both", "all"],
                    default="all",
                    help="all = every US-listed common stock (~2,000+ after "
                         "liquidity filter); both = S&P 500+400+600")
    ap.add_argument("--min-score", type=float, default=40.0)
    ap.add_argument("--account", type=float, default=100_000.0)
    ap.add_argument("--risk-pct", type=float, default=0.75)
    ap.add_argument("--output", default="./reports")
    args = ap.parse_args()

    t0 = time.time()
    universe_choice = "sp500" if args.fast else args.universe
    skip_fund = args.fast
    max_age = 4 if args.fast else 24  # hours

    print("=" * 60)
    print(f"PULLBACK SCANNER  ·  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"universe={universe_choice}  fast={args.fast}  "
          f"min_score={args.min_score}  account=${args.account:,.0f}  "
          f"risk={args.risk_pct}%")
    print("=" * 60)

    # ---- Module 1: universe ----
    print("\n[1/5] Loading universe …")
    raw = build_universe(universe_choice)
    print(f"  Raw universe: {len(raw)} unique tickers")
    if BENCHMARK not in raw:
        raw.append(BENCHMARK)
    total_scanned = len(raw) - 1  # exclude benchmark from the headline count

    # ---- Module 2: data ----
    # Bulk-download history for the FULL universe, then pre-filter from the
    # downloaded data. This is far more rate-limit friendly than issuing one
    # fast_info request per ticker (Yahoo 429s on ~1.5k single calls).
    print("\n[2/5] Fetching price history …")
    conn = _init_cache()
    daily_all = fetch_history(conn, raw, "1d", "18mo", max_age)
    kept = filter_by_liquidity(daily_all)
    print(f"  Liquidity filter: {len(kept)}/{total_scanned} passed "
          f"(price>${MIN_PRICE:.0f}, avg20d vol>{MIN_AVG_VOL:,})")
    if BENCHMARK not in kept:
        kept.append(BENCHMARK)
    # weekly only for the survivors
    weekly = fetch_history(conn, kept, "1wk", "3y", max_age)
    daily = {t: daily_all[t] for t in kept if t in daily_all}
    if BENCHMARK not in daily:
        print(f"  ! Benchmark {BENCHMARK} unavailable — RS disabled")
    spy_daily = daily.get(BENCHMARK)

    cand = [t for t in kept if t in daily and t != BENCHMARK]
    fundamentals = {}
    pf_meta: dict[str, dict] = {}
    if not skip_fund:
        print("\n  Fetching fundamentals …")
        fundamentals = fetch_fundamentals(cand)
    else:
        # fast mode: light market-cap lookup on survivors only (for filters)
        print("\n  Fetching market caps …")
        mcaps = fetch_market_caps(cand)
        pf_meta = {t: {"mcap": mc} for t, mc in mcaps.items()}

    # ---- Module 3 + 4: score & risk ----
    print("\n[3-4/5] Scoring & risk …")
    results = []
    if spy_daily is None:
        spy_daily = pd.DataFrame({"Close": []})
    for t in tqdm([x for x in kept if x != BENCHMARK], desc="Scoring"):
        d = daily.get(t)
        if d is None:
            continue
        try:
            sc = score_ticker(d, weekly.get(t), spy_daily,
                              fundamentals.get(t, {}), skip_fund)
        except Exception as e:  # noqa: BLE001
            _log_failed([t], f"scoring error: {e}")
            continue
        if sc is None or sc["score"] < args.min_score:
            continue
        fund = fundamentals.get(t, {})
        sc["ticker"] = t
        sc["company"] = fund.get("shortName", t) or t
        sc["sector"] = fund.get("sector", "—") or "—"
        sc["mcap"] = fund.get("marketCap") or pf_meta.get(t, {}).get("mcap")
        sc["risk"] = compute_risk(sc["price"], sc["atr"],
                                  args.account, args.risk_pct)
        sc["thumb"] = render_thumb(d)
        results.append(sc)

    results.sort(key=lambda r: r["score"], reverse=True)

    # ---- Module 5: report ----
    print("\n[5/5] Building HTML report …")
    bull, regime_txt = market_regime()
    os.makedirs(args.output, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = os.path.join(args.output, f"pullback_report_{date_str}.html")
    scan_time = f"{(time.time() - t0)/60:.1f}m"
    meta = {
        "date": date_str,
        "universe_label": {
            "sp500": "S&P 500",
            "russell": "Small/Mid-cap (Russell 2000 proxy)",
            "nasdaq": "NASDAQ-listed",
            "both": "S&P 500 + MidCap 400 + SmallCap 600",
            "all": "All US-listed (NASDAQ + NYSE/AMEX)",
        }.get(universe_choice, universe_choice),
        "total_scanned": total_scanned,
        "passed_filter": len(kept) - (1 if BENCHMARK in kept else 0),
        "scan_time": scan_time,
        "bull": bull, "regime_txt": regime_txt,
    }
    generate_html(results, meta, out_path)

    # ---- export JSON for the Streamlit app ----
    export_json(results, meta, os.path.join(args.output, "latest_results.json"))
    export_json(results, meta, os.path.join(HERE, "data", "latest_results.json"))
    print(f"  Data: {os.path.join(HERE, 'data', 'latest_results.json')}")

    # ---- terminal summary ----
    print("\n" + "=" * 60)
    print(f"DONE in {scan_time}  ·  {len(results)} graded setups")
    print("=" * 60)
    grades = {}
    for r in results:
        grades[r["grade"]] = grades.get(r["grade"], 0) + 1
    for g in ("A+", "A", "B+", "B"):
        if grades.get(g):
            print(f"  {g:>3}: {grades[g]}")
    print(f"\n  {'TICKER':<8}{'SCORE':>6}  {'GR':<3}{'PRICE':>9}"
          f"{'ENTRY':>9}{'STOP':>9}{'R:R':>6}  TAGS")
    print("  " + "-" * 70)
    for r in results[:25]:
        print(f"  {r['ticker']:<8}{r['score']:>6.0f}  {r['grade']:<3}"
              f"${r['price']:>8.2f}${r['risk']['entry']:>8.2f}"
              f"${r['risk']['stop']:>8.2f}{r['risk']['rr']:>6.1f}  "
              f"{','.join(r['tags'])}")
    if len(results) > 25:
        print(f"  … and {len(results) - 25} more in the HTML report")

    print(f"\n  Report: {out_path}")
    conn.close()
    try:
        webbrowser.open(f"file://{os.path.abspath(out_path)}")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
