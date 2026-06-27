"""
sector_strength.py — rank market sectors (and industries) by relative strength.

Composite momentum score across 1-day / 1-week / 1-month / 3-month horizons,
sourced from Finviz via finvizfinance. The leading sectors are the rotation
hotspots to then fish individual setups from.

    from sector_strength import rank_sectors
    rank_sectors()                       # 11 macro sectors, ranked
    rank_sectors(top_n=3)                # the 3 strongest
    rank_sectors(group="Industry", top_n=5)
    rank_sectors(weights={"1d":0.0,"1w":0.4,"1m":0.4,"3m":0.2})

CLI:  python sector_strength.py [--industry] [--top N]
"""
from __future__ import annotations

import time

import pandas as pd

try:
    from finvizfinance.group.performance import Performance
except ImportError:  # pragma: no cover
    Performance = None

# Default weights (sum = 1.0). Recent momentum dominates (1d+1w+1m = 80%) so we
# get the sector leading *now*, with 3m (20%) as a filter against one-day fakes.
WEIGHTS = {"1d": 0.10, "1w": 0.35, "1m": 0.35, "3m": 0.20}


def _with_retries(fn, attempts: int = 4, base_delay: float = 2.0):
    """Retry with exponential backoff on Finviz rate-limits / network errors.

    Returns the function result, or None if every attempt failed.
    """
    delay = base_delay
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i < attempts - 1:
                print(f"[retry] {str(e)[:80]} — attempt {i+1}/{attempts}, "
                      f"wait {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
    return None


def _to_decimal(series: pd.Series) -> pd.Series:
    """Coerce a Finviz performance column to a DECIMAL fraction (0.0293 = +2.93%).

    Finviz is inconsistent: some columns arrive as percent STRINGS ("-4.22%"),
    others as already-decimal floats (-0.0562). We decide per value:
      - if the raw text carried a "%", it is a percentage  -> divide by 100;
      - otherwise it is decimal, but if its magnitude looks like a percentage
        (|v| > 1.5, i.e. >150%) we still rescale it (defensive).
    This is more robust than a blanket |v|>1.5 rule, which would mis-handle a
    sub-1.5% week that arrived as the string "0.93%".
    """
    raw = series.astype(str).str.strip()
    has_pct = raw.str.contains("%", regex=False)
    num = pd.to_numeric(raw.str.replace("%", "", regex=False).str.strip(),
                        errors="coerce")
    out = num.copy()
    out[has_pct] = num[has_pct] / 100.0            # explicit percentages
    defensive = (~has_pct) & (num.abs() > 1.5)     # decimals that look like %
    out[defensive] = num[defensive] / 100.0
    return out


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Put Change + Perf Week/Month/Quart on the same decimal scale."""
    for col in ("Change", "Perf Week", "Perf Month", "Perf Quart"):
        if col in df.columns:
            df[col] = _to_decimal(df[col])
        else:
            df[col] = 0.0
    return df


def rank_sectors(weights: dict | None = None, top_n: int | None = None,
                 group: str = "Sector") -> list[dict]:
    """Rank Finviz sectors (or ``group="Industry"``) by relative strength.

        score = 1d*w1d + 1w*w1w + 1m*w1m + 3m*w3m

    Returns a list sorted by score desc:
        [{sector, perf_1d, perf_1w, perf_1m, perf_3m, score}, ...]
    Empty list if Finviz is unavailable (rate-limited) after retries.
    """
    if Performance is None:
        print("[WARN] finvizfinance not installed — pip install finvizfinance")
        return []

    w = {**WEIGHTS, **(weights or {})}

    df = _with_retries(lambda: Performance().screener_view(group=group))
    if df is None or getattr(df, "empty", True):
        print("[WARN] Finviz data unavailable (rate limit?) — empty list")
        return []

    df = _normalize(df)
    z = pd.Series(0.0, index=df.index)
    df["score"] = (
        df.get("Change", z).fillna(0) * w.get("1d", 0)
        + df.get("Perf Week", z).fillna(0) * w.get("1w", 0)
        + df.get("Perf Month", z).fillna(0) * w.get("1m", 0)
        + df.get("Perf Quart", z).fillna(0) * w.get("3m", 0)
    )

    df = df.sort_values("score", ascending=False)
    out = [{
        "sector": r["Name"],
        "perf_1d": round(float(r.get("Change", 0) or 0), 4),
        "perf_1w": round(float(r.get("Perf Week", 0) or 0), 4),
        "perf_1m": round(float(r.get("Perf Month", 0) or 0), 4),
        "perf_3m": round(float(r.get("Perf Quart", 0) or 0), 4),
        "score": round(float(r["score"]), 4),
    } for _, r in df.iterrows()]
    return out[:top_n] if top_n else out


def sanity_check(rows: list[dict]) -> bool:
    """True if every percentage is realistic (|v| < 1.0 i.e. under 100%)."""
    for r in rows:
        for k in ("perf_1d", "perf_1w", "perf_1m", "perf_3m", "score"):
            if abs(r[k]) >= 1.0:
                print(f"[SANITY FAIL] {r['sector']} {k}={r[k]} "
                      f"(looks like a normalization bug)")
                return False
    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Rank sectors by relative strength")
    ap.add_argument("--industry", action="store_true",
                    help="rank ~145 industries instead of 11 sectors")
    ap.add_argument("--top", type=int, default=None, help="show only the top N")
    args = ap.parse_args()

    grp = "Industry" if args.industry else "Sector"
    rows = rank_sectors(group=grp, top_n=args.top)
    if not rows:
        raise SystemExit("No data.")

    print(f"\n{'SCORE':>7}  {'SECTOR':24} {'1d':>7} {'1w':>7} "
          f"{'1m':>7} {'3m':>7}")
    print("-" * 64)
    for s in rows:
        print(f"{s['score']:+.3f}  {s['sector']:24} "
              f"{s['perf_1d']:+.2%} {s['perf_1w']:+.2%} "
              f"{s['perf_1m']:+.2%} {s['perf_3m']:+.2%}")

    ok = sanity_check(rows)
    print(f"\nSanity check: {'PASS' if ok else 'FAIL'}")
    if not args.top:
        print("Leaders (top 3):", [s["sector"] for s in rows[:3]])
