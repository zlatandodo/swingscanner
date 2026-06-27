"""
Pullback Scanner — Streamlit front-end.

Loads the precomputed scan results (data/latest_results.json, produced by
swing_scanner.py) and renders an interactive dashboard: filter by grade,
setup type, score, market cap / volume thresholds, sector and ticker; inspect
each name on a live TradingView chart (SMA50 / SMA200 / EMA21).

Run locally:   streamlit run streamlit_app.py
Deploy:        push to GitHub -> share.streamlit.io -> point at streamlit_app.py
"""
from __future__ import annotations

import json
import math
import os

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data", "latest_results.json")

st.set_page_config(page_title="Pullback Scanner", page_icon="📈", layout="wide")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_data(path: str, mtime: float) -> dict:
    with open(path) as fh:
        return json.load(fh)


if not os.path.exists(DATA_PATH):
    st.title("📈 Pullback Scanner")
    st.error(
        "No data found at `data/latest_results.json`.\n\n"
        "Generate it first by running the scanner:\n\n"
        "```bash\npython swing_scanner.py --fast      # quick (S&P 500)\n"
        "python swing_scanner.py              # full universe\n```"
    )
    st.stop()

payload = load_data(DATA_PATH, os.path.getmtime(DATA_PATH))
meta = payload.get("meta", {})
results = payload.get("results", [])
df = pd.json_normalize(results)
if df.empty:
    st.warning("The results file is empty — re-run the scanner.")
    st.stop()

# tags is a list column; precompute a joined string for display
df["setup_str"] = df["tags"].apply(
    lambda t: ", ".join(t) if isinstance(t, list) else "")
df["mcap_b"] = df["mcap"].apply(
    lambda v: (v / 1e9) if isinstance(v, (int, float)) and v else None)
df["vol_m"] = df["avg_vol_1m"].apply(
    lambda v: (v / 1e6) if isinstance(v, (int, float)) and v else None)

ALL_TAGS = sorted({t for row in df["tags"] if isinstance(row, list) for t in row})
ALL_SECTORS = sorted(s for s in df["sector"].dropna().unique() if s and s != "—")
ALL_GRADES = ["A+", "A", "B+", "B"]


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title(f"📈 Pullback Scanner — {meta.get('date', '')}")
st.caption(f"{meta.get('universe_label', '')} · Yahoo Finance via yfinance")

bull = meta.get("bull", True)
regime = meta.get("regime_txt", "")
(st.success if bull else st.error)(f"**Market regime:** {regime}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Scanned", f"{meta.get('total_scanned', 0):,}")
c2.metric("Passed Filter", f"{meta.get('passed_filter', 0):,}")
c3.metric("A+ Setups", int((df["grade"] == "A+").sum()))
c4.metric("Scan Time", meta.get("scan_time", "—"))


# ---------------------------------------------------------------------------
# Criteria / setup legend
# ---------------------------------------------------------------------------
with st.expander("ℹ️  How scoring works & what each setup means"):
    st.markdown(
        """
**Score 0–100** = sum of 6 independent blocks (below 40 is dropped):

| Block | Max | Measures |
|---|---|---|
| **A — Trend Template** | 30 | Price > 50/150/200 SMA, MAs stacked (50>150>200), 200-SMA rising, within 25% of 52-wk high |
| **B — Weinstein Stage 2** | 15 | Weekly: price > 30-wk SMA, 30-wk SMA rising, base breakout |
| **C — Pullback Quality** | 20 | Pullback to 20/50 EMA + bounce, low pullback volume, healthy 5–20% depth |
| **D — VCP** | 15 | Volatility contraction (ATR shrinking), multiple tightening swings, volume dry-up |
| **E — Relative Strength** | 10 | Outperforms SPY over 3 months + rising RS line |
| **F — Fundamentals** | 10 | EPS YoY > 20%, revenue > 15%, bullish analyst consensus (neutral 5/10 if missing) |

**Grades:** ≥85 → A+ · ≥70 → A · ≥55 → B+ · ≥40 → B

**Setup tags** (added only when that whole block is fully met):
- **Stage2** — perfect trend template (block A full)
- **Pullback** — healthy pullback-to-MA (block C full)
- **VCP** — Minervini volatility contraction (block D full)
- **RS Leader** — relative-strength leader (block E full)
- **High EPS** — strong earnings growth
- **Low Vol Dry** — volume dried up (supply exhaustion)
        """
    )


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")
f_grades = st.sidebar.multiselect("Grade", ALL_GRADES, default=ALL_GRADES)
f_setups = st.sidebar.multiselect(
    "Setup type", ALL_TAGS,
    help="Show only names that match the selected setups.")
setup_mode = st.sidebar.radio(
    "Setup match", ["All selected", "Any selected"], horizontal=True,
    disabled=not f_setups)
f_min_score = st.sidebar.slider("Min score", 40, 100, 40, 1)
st.sidebar.markdown("**Market cap & volume**")
f_mcap_min = st.sidebar.number_input(
    "Market cap ≥ ($ billions)", min_value=0.0, value=0.0, step=0.5,
    help="e.g. 2 = only ≥ $2B")
f_mcap_max = st.sidebar.number_input(
    "Market cap ≤ ($ billions, 0 = no limit)", min_value=0.0, value=0.0,
    step=0.5)
f_vol_min = st.sidebar.number_input(
    "Avg volume ≥ (millions of shares)", min_value=0.0, value=0.0, step=0.1)
f_sectors = st.sidebar.multiselect("Sector", ALL_SECTORS)
f_search = st.sidebar.text_input("Search ticker / company")

# ---- apply filters ----
m = df["grade"].isin(f_grades) & (df["score"] >= f_min_score)

if f_setups:
    if setup_mode == "All selected":
        m &= df["tags"].apply(
            lambda t: isinstance(t, list) and all(s in t for s in f_setups))
    else:
        m &= df["tags"].apply(
            lambda t: isinstance(t, list) and any(s in t for s in f_setups))

if f_mcap_min > 0:
    m &= df["mcap_b"].fillna(-1) >= f_mcap_min
if f_mcap_max > 0:
    m &= df["mcap_b"].fillna(1e12) <= f_mcap_max
if f_vol_min > 0:
    m &= df["vol_m"].fillna(-1) >= f_vol_min
if f_sectors:
    m &= df["sector"].isin(f_sectors)
if f_search:
    q = f_search.lower().strip()
    m &= (df["ticker"].str.lower().str.contains(q)
          | df["company"].str.lower().str.contains(q))

fdf = df[m].sort_values("score", ascending=False).reset_index(drop=True)
st.markdown(f"### {len(fdf)} setups match  ·  of {len(df)} graded")


# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------
def _tv_url(t):
    return f"https://www.tradingview.com/chart/?symbol={str(t).replace('-', '.')}"


view = pd.DataFrame({
    "Ticker": fdf["ticker"].apply(_tv_url),   # rendered as a clickable link
    "Company": fdf["company"],
    "Sector": fdf["sector"],
    "Score": fdf["score"],
    "Grade": fdf["grade"],
    "Price": fdf["price"],
    "MktCap $B": fdf["mcap_b"],
    "AvgVol M": fdf["vol_m"],
    "vs200 %": fdf["vs200"],
    "vs50 %": fdf["vs50"],
    "Pullbk %": fdf["pullback_pct"],
    "ATR": fdf["atr"],
    "VCP": fdf["vcp_score"],
    "RS↑": fdf["rs_outperf"],
    "EPS %": fdf.get("eps_gr"),
    "Rev %": fdf.get("rev_gr"),
    "Entry": fdf["risk.entry"],
    "Stop": fdf["risk.stop"],
    "Stop %": fdf["risk.stop_pct"],
    "T1": fdf["risk.t1"],
    "T2": fdf["risk.t2"],
    "R:R": fdf["risk.rr"],
    "Pos %": fdf["risk.pos_pct"],
    "Setup": fdf["setup_str"],
})

st.dataframe(
    view,
    use_container_width=True,
    hide_index=True,
    height=460,
    column_config={
        "Ticker": st.column_config.LinkColumn(
            "Ticker", display_text=r"symbol=(.+)$",
            help="Click to open the chart on TradingView"),
        "Score": st.column_config.NumberColumn(format="%d"),
        "Price": st.column_config.NumberColumn(format="$%.2f"),
        "MktCap $B": st.column_config.NumberColumn(format="%.1f"),
        "AvgVol M": st.column_config.NumberColumn(format="%.1f"),
        "vs200 %": st.column_config.NumberColumn(format="%+.1f"),
        "vs50 %": st.column_config.NumberColumn(format="%+.1f"),
        "Pullbk %": st.column_config.NumberColumn(format="%.1f"),
        "ATR": st.column_config.NumberColumn(format="%.2f"),
        "VCP": st.column_config.NumberColumn(format="%d"),
        "EPS %": st.column_config.NumberColumn(format="%+.0f"),
        "Rev %": st.column_config.NumberColumn(format="%+.0f"),
        "Entry": st.column_config.NumberColumn(format="$%.2f"),
        "Stop": st.column_config.NumberColumn(format="$%.2f"),
        "Stop %": st.column_config.NumberColumn(format="%.1f"),
        "T1": st.column_config.NumberColumn(format="$%.2f"),
        "T2": st.column_config.NumberColumn(format="$%.2f"),
        "R:R": st.column_config.NumberColumn(format="%.1f"),
        "Pos %": st.column_config.NumberColumn(format="%.1f"),
    },
)

view_csv = view.copy()
view_csv["Ticker"] = fdf["ticker"]   # plain symbols in the export, not URLs
csv = view_csv.to_csv(index=False).encode()
st.download_button("⬇️ Download filtered (CSV)", csv,
                   file_name="pullback_setups.csv", mime="text/csv")


# ---------------------------------------------------------------------------
# Chart gallery — filtered setups on a grid of live TradingView charts
# ---------------------------------------------------------------------------
st.markdown("### 🖼️ Chart gallery (TradingView)")
total = len(fdf)
if total == 0:
    st.info("No setups match the current filters.")
else:
    gc1, gc2 = st.columns([1, 2])
    ncols = gc1.radio("Charts per row", [3, 4], horizontal=True, index=0)
    if total > 3:
        max_n = gc2.slider("Charts to show", 3, min(60, total),
                           min(12, total), step=3,
                           help="Each chart is a live TradingView widget — "
                                "more charts load slower.")
    else:
        max_n = total
    st.caption(f"Showing {min(max_n, total)} of {total} filtered setups · "
               "live TradingView · SMA50 + SMA200 + EMA21. "
               "Sorted by score. Loading many widgets can be slow.")

    gal = fdf.head(max_n).reset_index(drop=True)
    ch = 300  # per-chart height (px)

    def _tv_cell(ticker, grade, score, setup):
        sym = str(ticker).replace("-", ".")
        cfg = {
            "autosize": True, "symbol": sym, "interval": "D",
            "timezone": "Etc/UTC", "theme": "light", "style": "1",
            "locale": "en", "hide_top_toolbar": True, "hide_legend": True,
            "hide_side_toolbar": True, "allow_symbol_change": False,
            "save_image": False, "withdateranges": False,
            "studies": [
                {"id": "MASimple@tv-basicstudies", "inputs": {"length": 50}},
                {"id": "MASimple@tv-basicstudies", "inputs": {"length": 200}},
                {"id": "MAExp@tv-basicstudies", "inputs": {"length": 21}},
            ],
        }
        url = f"https://www.tradingview.com/chart/?symbol={sym}"
        link = (f'<a href="{url}" target="_blank" rel="noopener" '
                f'style="color:#0969da;text-decoration:none">{ticker} ↗</a>')
        sub = f" · {setup}" if setup else ""
        return (
            '<div style="border:1px solid #d0d7de;border-radius:8px;'
            'overflow:hidden;background:#fff">'
            f'<div style="font:600 12px -apple-system,sans-serif;'
            f'color:#1f2328;padding:5px 8px;border-bottom:1px solid #eaeef2">'
            f'{link} · {grade} · {score:.0f}'
            f'<span style="color:#8250df;font-weight:400">{sub}</span></div>'
            f'<div class="tradingview-widget-container" '
            f'style="height:{ch}px;width:100%">'
            '<div class="tradingview-widget-container__widget" '
            'style="height:100%;width:100%"></div>'
            '<script type="text/javascript" '
            'src="https://s3.tradingview.com/external-embedding/'
            'embed-widget-advanced-chart.js" async>'
            f'{json.dumps(cfg)}</script></div></div>')

    cells = "".join(
        _tv_cell(r["ticker"], r["grade"], r["score"], r["setup_str"])
        for _, r in gal.iterrows())
    grid = (f'<div style="display:grid;'
            f'grid-template-columns:repeat({ncols},1fr);gap:10px">{cells}</div>')
    rows = math.ceil(len(gal) / ncols)
    components.html(grid, height=rows * (ch + 40) + 20, scrolling=True)


st.caption("Tip: click any ticker (table or gallery) to open it on TradingView.")
st.caption("For research and educational purposes only. Not investment advice.")
