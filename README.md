# Pullback Scanner

A self-contained Python pullback/swing scanner that finds **A+ setups** across the
**S&P 500 + S&P MidCap 400 + S&P SmallCap 600** (~1,500 liquid names) using only
`yfinance` for data. It produces both a standalone **HTML report** and a JSON feed
for an interactive **Streamlit app** you can deploy from GitHub.

> **Two ways to view results**
> - `pullback_report_YYYY-MM-DD.html` — standalone file, opens in any browser, no server.
> - `streamlit_app.py` — interactive dashboard (filter by grade, **setup type**,
>   **market-cap / volume thresholds**, sector, score) with live TradingView charts.
>   Deployable free on Streamlit Community Cloud.

## What it does

For every liquid name it computes a transparent **0–100 score** across six blocks:

| Block | Max | What it measures |
|-------|-----|------------------|
| **A — Trend Template** | 30 | Minervini Stage-2 MA structure (price vs 50/150/200 SMA, MA stack, 200 slope, distance to 52-wk high) |
| **B — Weinstein Stage 2** | 15 | Weekly 30-wk SMA, rising slope, base breakout zone |
| **C — Pullback Quality** | 20 | Pullback to 20/50 EMA + bounce, low pullback volume, healthy 5–20% depth |
| **D — VCP Signature** | 15 | ATR contraction, multiple tightening swings, volume dry-up |
| **E — Relative Strength** | 10 | Outperforming SPY over 3 months + rising RS line |
| **F — Fundamentals** | 10 | EPS growth >20%, revenue growth >15%, bullish analyst consensus (neutral 5 pts if data missing) |

**Grades:** 85+ → `A+`, 70+ → `A`, 55+ → `B+`, 40+ → `B`, below 40 skipped.

Each graded name also gets ATR-based **risk management**: structural stop (1.5×ATR),
two targets (2×/4×ATR), R:R ratio, and position size for your account at the chosen
risk-per-trade %.

The report includes a market-regime badge (**bull** when SPY > its 10-month EMA — the
condition behind ~90% of successful breakouts), live filter/sort/search, and a
click-to-expand TradingView mini-chart per row.

## First-run checklist

1. **Install dependencies**
   ```bash
   pip install yfinance pandas numpy tqdm pandas_ta
   ```
   > `tqdm` and `pandas_ta` are *optional* — the scanner falls back to a built-in
   > progress shim and a pure-pandas ATR implementation if they're absent.

2. **Quick test (S&P 500 only, ~5 min)**
   ```bash
   python swing_scanner.py --fast
   ```

3. **Full run (all US-listed common stocks, ~2,000+ after filtering)**
   ```bash
   python swing_scanner.py            # --universe all is the default
   ```

4. **Open the report** — it opens automatically in your browser. Files land in:
   ```
   ./reports/pullback_report_YYYY-MM-DD.html
   ```

## CLI

```bash
python swing_scanner.py [--fast]
                        [--universe sp500|russell|nasdaq|both|all]
                        [--min-score 55]
                        [--account 100000]
                        [--risk-pct 0.75]
                        [--output ./reports]
```

| Flag | Default | Notes |
|------|---------|-------|
| `--fast` | off | S&P 500 only, skips fundamentals, reuses cache <4h old. Good for intraday refresh. |
| `--universe` | `all` | `all` = every US-listed common stock (NASDAQ + NYSE/AMEX, ~6,000 raw → ~2,000+ liquid). `nasdaq` = NASDAQ only. `both` = S&P 500+400+600. Also `sp500`, `russell`. |
| `--min-score` | `40` | Minimum score to include in the report. |
| `--account` | `100000` | Account size for position sizing. |
| `--risk-pct` | `0.75` | Risk per trade as % of account. |
| `--output` | `./reports` | Output directory for the HTML report. |

## How the universe is built

- **`all` (default)** — every US-listed **common stock** from nasdaqtrader.com
  (`nasdaqlisted.txt` + `otherlisted.txt`): NASDAQ + NYSE + NYSE American, ETFs /
  warrants / units / rights / preferreds removed by security-name. ~6,000 raw,
  trimmed to ~2,000+ by the liquidity filter. `nasdaq` = NASDAQ file only.
- **S&P 500** — scraped live from the Wikipedia "List of S&P 500 companies" page.
- **Small/Mid-cap (Russell 2000 proxy)** — resolved in this order:
  1. bundled `russell2000.csv` if present (a `Ticker`/`Symbol` column);
  2. else the iShares **IWM** holdings CSV (now usually bot-blocked → skipped);
  3. else **S&P MidCap 400 + SmallCap 600** scraped from Wikipedia (~1,000 names,
     a reliable liquid proxy for the Russell 2000) and saved to `russell2000.csv`;
  4. else a hardcoded ~500-name representative fallback.

Symbols are cleaned for yfinance (`BRK.B` → `BRK-B`, dots → dashes) and de-duplicated.
**Pre-filtering happens *after* the bulk price download** — the scanner downloads
18-month history for the whole universe in batches (few HTTP calls), then keeps only
**price > $10** and **20-day avg volume > 300K**. This is far more robust than issuing
~1,500 single `fast_info` calls, which Yahoo rate-limits (429).

## Caching

Daily (18 mo) and weekly (3 yr) OHLCV are cached in **`swing_cache.db`** (SQLite,
keyed by `ticker, date, interval`). The cache survives between runs and is refreshed
when older than 24h (4h in `--fast`). Failed tickers are logged to
`failed_tickers.log` and the scan continues.

## Interactive Streamlit app

The scanner writes its results to `data/latest_results.json`. The Streamlit app
reads that file (it does **not** re-scan on every page load) and gives you:

- filter by **grade**, **setup type** (Stage2 / Pullback / VCP / RS Leader / High EPS /
  Low Vol Dry), **min score**, **sector**, ticker search;
- **numeric thresholds** — “Market cap ≥ 2 (billions)”, “Market cap ≤ …”,
  “Avg volume ≥ 1 (millions)”, etc.;
- a sortable results table + **CSV download** of the filtered set;
- a live **TradingView chart** (SMA 50 / SMA 200 / EMA 21) for any selected name;
- a built-in legend explaining every scoring block and setup tag.

Run it locally:

```bash
pip install -r requirements.txt
python swing_scanner.py --fast       # generate data/latest_results.json
streamlit run streamlit_app.py
```

### Deploy free on Streamlit Community Cloud (via GitHub)

1. Push this folder to a GitHub repo (make sure `data/latest_results.json`,
   `streamlit_app.py`, and `requirements.txt` are committed — `.gitignore` keeps the
   cache/logs out but **keeps the data file**).
2. Go to **share.streamlit.io** → *New app* → pick your repo/branch →
   main file path = `streamlit_app.py` → **Deploy**.
3. To refresh the data, re-run the scanner locally and commit the updated
   `data/latest_results.json` (or use the optional GitHub Action in
   `.github/workflows/scan.yml`).

> Running the full ~1,500-ticker scan *inside* Streamlit Cloud is not recommended
> (limited RAM/CPU + Yahoo rate limits). Keep scanning local/scheduled and let the
> app serve the precomputed JSON.

## Files

```
swing_scanner.py            # the scanner (universe → data → score → risk → HTML/JSON)
streamlit_app.py            # interactive dashboard (reads data/latest_results.json)
requirements.txt            # deps for Streamlit Cloud
data/latest_results.json    # scan output consumed by the app (commit this)
russell2000.csv             # small/mid-cap universe (auto-created)
swing_cache.db              # SQLite price cache (auto-created, gitignored)
failed_tickers.log          # tickers skipped during a scan (gitignored)
reports/                    # generated HTML reports
.github/workflows/scan.yml  # optional: scheduled scan that commits fresh data
```

## Notes & limits

- Data is Yahoo Finance via `yfinance`; fields can be missing or delayed.
- Fundamentals are best-effort — missing data scores a neutral 5/10, never a penalty.
- The HTML works fully offline after generation; only the optional TradingView chart
  iframes reach the network when you expand a row.
- **For research and educational purposes only. Not investment advice.**
