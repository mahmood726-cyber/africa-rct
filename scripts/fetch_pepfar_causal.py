#!/usr/bin/env python
"""
fetch_pepfar_causal.py -- PEPFAR Causal Inference: Did HIV Funding Crowd Out Everything Else?

Applies quasi-experimental methods to estimate the causal effect of PEPFAR
(launched 2003) on non-HIV clinical trial starts in recipient countries.

Methods:
  1. Interrupted Time Series (ITS) with control series
  2. Difference-in-Differences (DiD) estimator
  3. Synthetic Control Method (for Uganda)

Queries ClinicalTrials.gov API v2 for year-by-year counts (2000-2025)
across 10 PEPFAR-focus + 5 non-PEPFAR control countries.

Key reference: Bendavid & Bhatt (2009) PEPFAR & health systems, JAMA.

Outputs:
  - data/pepfar_causal_data.json   (cached API results, 24h TTL)
  - pepfar-causal.html             (dark-theme interactive dashboard)
"""

import json
import math
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Encoding safety (Windows cp1252)
# ---------------------------------------------------------------------------
import io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

# PEPFAR-focus countries (received >$100M/yr from PEPFAR)
PEPFAR_COUNTRIES = [
    "Uganda", "Kenya", "Tanzania", "Mozambique", "South Africa",
    "Nigeria", "Zambia", "Malawi", "Zimbabwe", "Ethiopia",
]

# Non-PEPFAR comparators (similar income, less HIV funding)
NON_PEPFAR_COUNTRIES = [
    "Ghana", "Senegal", "Burkina Faso", "DRC", "Cameroon",
]

ALL_COUNTRIES = PEPFAR_COUNTRIES + NON_PEPFAR_COUNTRIES

YEARS = list(range(2000, 2026))  # 2000-2025 inclusive

# PEPFAR intervention year
PEPFAR_YEAR = 2003  # PEPFAR announced Jan 2003, first funds flowed mid-2003
PEPFAR_EFFECT_YEAR = 2004  # first full year of PEPFAR implementation

CACHE_FILE = Path(__file__).resolve().parent / "data" / "pepfar_causal_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "pepfar-causal.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# Location name overrides for API
LOCATION_MAP = {
    "DRC": "Congo",
    "Tanzania": "Tanzania",
}

# References
REFERENCES = [
    {"id": "bendavid2009", "desc": "Bendavid & Bhatt (2009). Does PEPFAR crowd out other health aid?", "url": "https://doi.org/10.1001/jama.2009.1385"},
    {"id": "pmid39972388", "desc": "PEPFAR infrastructure sustainability analysis", "url": "https://pubmed.ncbi.nlm.nih.gov/39972388/"},
    {"id": "pmid37643290", "desc": "PopART trial long-term outcomes", "url": "https://pubmed.ncbi.nlm.nih.gov/37643290/"},
    {"id": "abadie2010", "desc": "Abadie, Diamond & Hainmueller (2010). Synthetic Control Methods.", "url": "https://doi.org/10.1198/jasa.2009.ap08746"},
    {"id": "bernal2017", "desc": "Bernal, Cummins & Gasparrini (2017). ITS design for policy evaluation.", "url": "https://doi.org/10.1093/ije/dyw098"},
]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(params, retries=MAX_RETRIES):
    """Make a GET request to ClinicalTrials.gov API v2 with retries."""
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"  [retry {attempt + 1}/{retries}] {exc}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def get_trial_count_by_year(location, year, condition=None):
    """Return count of interventional trials for a location+year, optionally filtered by condition."""
    locn = LOCATION_MAP.get(location, location)

    filters = [
        "AREA[StudyType]INTERVENTIONAL",
        f"AREA[StartDate]RANGE[{year}-01-01,{year}-12-31]",
    ]

    params = {
        "format": "json",
        "query.locn": locn,
        "filter.advanced": " AND ".join(filters),
        "pageSize": 1,
        "countTotal": "true",
    }

    if condition is not None:
        params["query.cond"] = condition

    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def load_cache():
    """Load cached data if fresh enough."""
    if CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(raw.get("timestamp", "2000-01-01"))
            if datetime.now() - ts < timedelta(hours=CACHE_TTL_HOURS):
                print(f"Using cached data from {ts.isoformat()}")
                return raw
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def fetch_all_data():
    """Fetch year-by-year trial counts for all countries.

    For each country x year:
      - Total interventional trials
    For PEPFAR countries additionally:
      - HIV trials (condition = HIV)
      - Non-HIV = total - HIV
    """
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "countries": {},
    }

    # Calculate total API calls:
    # Non-PEPFAR: 5 countries x 26 years x 1 query = 130
    # PEPFAR: 10 countries x 26 years x 2 queries (total + HIV) = 520
    # Grand total: 650
    n_pepfar_calls = len(PEPFAR_COUNTRIES) * len(YEARS) * 2
    n_control_calls = len(NON_PEPFAR_COUNTRIES) * len(YEARS) * 1
    total_calls = n_pepfar_calls + n_control_calls
    call_num = 0

    for country in ALL_COUNTRIES:
        is_pepfar = country in PEPFAR_COUNTRIES
        group = "pepfar" if is_pepfar else "non_pepfar"

        country_data = {
            "group": group,
            "total_by_year": {},
            "hiv_by_year": {},
            "nonhiv_by_year": {},
        }

        for year in YEARS:
            # --- Total interventional trials ---
            call_num += 1
            print(f"  [{call_num}/{total_calls}] {country} / {year} / Total...")
            total_count = get_trial_count_by_year(country, year)
            time.sleep(RATE_LIMIT)
            country_data["total_by_year"][str(year)] = total_count

            if is_pepfar:
                # --- HIV trials ---
                call_num += 1
                print(f"  [{call_num}/{total_calls}] {country} / {year} / HIV...")
                hiv_count = get_trial_count_by_year(country, year, condition="HIV")
                time.sleep(RATE_LIMIT)
                country_data["hiv_by_year"][str(year)] = hiv_count
                country_data["nonhiv_by_year"][str(year)] = max(0, total_count - hiv_count)

        data["countries"][country] = country_data

    # --- Save cache ---
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Cached to {CACHE_FILE}")

    return data


# ---------------------------------------------------------------------------
# Statistical helpers (pure Python OLS)
# ---------------------------------------------------------------------------

def ols_simple(x_vals, y_vals):
    """Simple OLS regression: y = a + b*x. Returns (intercept, slope, r_squared)."""
    n = len(x_vals)
    if n < 2:
        return (0.0, 0.0, 0.0)
    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n
    ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
    ss_xx = sum((x - x_mean) ** 2 for x in x_vals)
    ss_yy = sum((y - y_mean) ** 2 for y in y_vals)
    if ss_xx == 0:
        return (y_mean, 0.0, 0.0)
    slope = ss_xy / ss_xx
    intercept = y_mean - slope * x_mean
    r_sq = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0
    return (intercept, slope, r_sq)


def ols_its(years, values, intervention_year):
    """Interrupted Time Series segmented regression.

    Model: Y_t = b0 + b1*time + b2*level_change + b3*slope_change + error

    Where:
      time = t (centered on intervention)
      level_change = 1 if t >= intervention_year, else 0
      slope_change = (t - intervention_year) if t >= intervention_year, else 0

    Returns dict with coefficients and interpretation.
    Uses manual OLS: (X'X)^{-1} X'Y
    """
    n = len(years)
    if n < 4:
        return {"b0": 0, "b1": 0, "b2": 0, "b3": 0, "r_sq": 0, "se": [0, 0, 0, 0]}

    # Build design matrix
    # X = [1, time, D, D*(time - T0)]
    T0 = intervention_year
    X = []
    Y = list(values)
    for i, yr in enumerate(years):
        t = yr - years[0]  # time index starting at 0
        D = 1.0 if yr >= T0 else 0.0
        slope_chg = float(yr - T0) if yr >= T0 else 0.0
        X.append([1.0, float(t), D, slope_chg])

    # X'X
    p = 4
    XtX = [[0.0] * p for _ in range(p)]
    XtY = [0.0] * p
    for i in range(n):
        for j in range(p):
            XtY[j] += X[i][j] * Y[i]
            for k in range(p):
                XtX[j][k] += X[i][j] * X[i][k]

    # Solve via Gauss-Jordan elimination
    aug = [XtX[i][:] + [XtY[i]] for i in range(p)]
    for col in range(p):
        # Partial pivoting
        max_row = col
        for row in range(col + 1, p):
            if abs(aug[row][col]) > abs(aug[max_row][col]):
                max_row = row
        aug[col], aug[max_row] = aug[max_row], aug[col]

        pivot = aug[col][col]
        if abs(pivot) < 1e-12:
            continue
        for j in range(col, p + 1):
            aug[col][j] /= pivot
        for row in range(p):
            if row != col:
                factor = aug[row][col]
                for j in range(col, p + 1):
                    aug[row][j] -= factor * aug[col][j]

    beta = [aug[i][p] for i in range(p)]

    # Residuals and R-squared
    y_mean = sum(Y) / n
    ss_res = 0.0
    ss_tot = 0.0
    for i in range(n):
        y_hat = sum(beta[j] * X[i][j] for j in range(p))
        ss_res += (Y[i] - y_hat) ** 2
        ss_tot += (Y[i] - y_mean) ** 2

    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Standard errors of coefficients
    mse = ss_res / max(1, n - p)
    # Invert X'X for variance-covariance
    # Re-build X'X inverse via augmented identity
    aug_inv = [XtX[i][:] + [1.0 if j == i else 0.0 for j in range(p)] for i in range(p)]
    for col in range(p):
        max_row = col
        for row in range(col + 1, p):
            if abs(aug_inv[row][col]) > abs(aug_inv[max_row][col]):
                max_row = row
        aug_inv[col], aug_inv[max_row] = aug_inv[max_row], aug_inv[col]
        pivot = aug_inv[col][col]
        if abs(pivot) < 1e-12:
            continue
        for j in range(2 * p):
            aug_inv[col][j] /= pivot
        for row in range(p):
            if row != col:
                factor = aug_inv[row][col]
                for j in range(2 * p):
                    aug_inv[row][j] -= factor * aug_inv[col][j]

    se = []
    for i in range(p):
        var_i = mse * aug_inv[i][p + i]
        se.append(math.sqrt(max(0, var_i)))

    return {
        "b0": beta[0],  # baseline intercept
        "b1": beta[1],  # pre-intervention slope (trend)
        "b2": beta[2],  # level change at intervention
        "b3": beta[3],  # slope change after intervention
        "r_sq": r_sq,
        "se": se,
        "mse": mse,
        "n": n,
    }


def did_estimate(pepfar_pre, pepfar_post, control_pre, control_post):
    """Difference-in-Differences estimator.

    DiD = (Treat_post - Treat_pre) - (Control_post - Control_pre)
    """
    treat_diff = pepfar_post - pepfar_pre
    control_diff = control_post - control_pre
    did = treat_diff - control_diff
    return {
        "pepfar_pre": pepfar_pre,
        "pepfar_post": pepfar_post,
        "control_pre": control_pre,
        "control_post": control_post,
        "pepfar_change": treat_diff,
        "control_change": control_diff,
        "did_estimate": did,
    }


def synthetic_control(target_series, donor_series_dict, pre_years, post_years):
    """Construct a synthetic control for the target from donor countries.

    Uses constrained least squares over the pre-period to find non-negative
    weights that sum to 1 (simplex constraint). Simple iterative approach.

    Args:
        target_series: dict year->value for target country
        donor_series_dict: {country_name: {year: value}}
        pre_years: list of years in pre-period
        post_years: list of years in post-period

    Returns:
        dict with weights, synthetic series, gap series, and summary stats.
    """
    donors = list(donor_series_dict.keys())
    n_donors = len(donors)
    if n_donors == 0:
        return None

    # Extract pre-period vectors
    target_pre = [target_series.get(str(y), 0) for y in pre_years]
    donor_pre = {}
    for d in donors:
        donor_pre[d] = [donor_series_dict[d].get(str(y), 0) for y in pre_years]

    # Simple optimization: grid search + refinement for weights
    # For small number of donors, use iterative coordinate descent
    n_pre = len(pre_years)

    # Initialize equal weights
    weights = {d: 1.0 / n_donors for d in donors}

    # Coordinate descent (100 iterations)
    for iteration in range(200):
        for d in donors:
            best_w = weights[d]
            best_loss = float("inf")

            # Try different values for this weight
            remaining = 1.0 - sum(weights[dd] for dd in donors if dd != d)
            remaining = max(0, remaining)
            for w_try in [remaining * f / 20 for f in range(21)]:
                # Temporarily set weight
                old_w = weights[d]
                weights[d] = w_try
                # Normalize to sum to 1
                w_sum = sum(weights.values())
                if w_sum == 0:
                    weights[d] = old_w
                    continue

                # Compute loss
                loss = 0.0
                for t in range(n_pre):
                    synth_t = sum(weights[dd] / w_sum * donor_pre[dd][t] for dd in donors)
                    loss += (target_pre[t] - synth_t) ** 2

                if loss < best_loss:
                    best_loss = loss
                    best_w = w_try

                weights[d] = old_w

            weights[d] = best_w

        # Normalize
        w_sum = sum(weights.values())
        if w_sum > 0:
            weights = {d: w / w_sum for d, w in weights.items()}

    # Compute synthetic series for all years
    all_years = sorted(set(pre_years + post_years))
    synthetic = {}
    gap = {}
    for y in all_years:
        synth_val = sum(
            weights[d] * donor_series_dict[d].get(str(y), 0) for d in donors
        )
        actual_val = target_series.get(str(y), 0)
        synthetic[str(y)] = round(synth_val, 2)
        gap[str(y)] = round(actual_val - synth_val, 2)

    # Pre-period fit (RMSPE)
    pre_errors = [(target_series.get(str(y), 0) - synthetic[str(y)]) ** 2 for y in pre_years]
    rmspe_pre = math.sqrt(sum(pre_errors) / max(1, len(pre_errors)))

    # Post-period gap
    post_gaps = [gap[str(y)] for y in post_years if str(y) in gap]
    avg_gap_post = sum(post_gaps) / max(1, len(post_gaps)) if post_gaps else 0

    # Filter to meaningful weights (>0.01)
    sig_weights = {d: round(w, 4) for d, w in weights.items() if w > 0.01}

    return {
        "target": "Uganda",
        "weights": sig_weights,
        "all_weights": {d: round(w, 4) for d, w in weights.items()},
        "synthetic_series": synthetic,
        "actual_series": {str(y): target_series.get(str(y), 0) for y in all_years},
        "gap_series": gap,
        "rmspe_pre": round(rmspe_pre, 3),
        "avg_gap_post": round(avg_gap_post, 2),
        "pre_years": pre_years,
        "post_years": post_years,
    }


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def compute_analysis(data):
    """Run all three causal inference methods on the fetched data."""
    countries = data["countries"]

    # =====================================================================
    # 1. Aggregate series: PEPFAR total trials, PEPFAR non-HIV, Control total
    # =====================================================================
    pepfar_total_by_year = defaultdict(int)
    pepfar_hiv_by_year = defaultdict(int)
    pepfar_nonhiv_by_year = defaultdict(int)
    control_total_by_year = defaultdict(int)

    for cname, cdata in countries.items():
        for y in YEARS:
            ys = str(y)
            total = cdata["total_by_year"].get(ys, 0)
            if cdata["group"] == "pepfar":
                pepfar_total_by_year[y] += total
                pepfar_hiv_by_year[y] += cdata.get("hiv_by_year", {}).get(ys, 0)
                pepfar_nonhiv_by_year[y] += cdata.get("nonhiv_by_year", {}).get(ys, 0)
            else:
                control_total_by_year[y] += total

    # Per-country total series for synthetic control donors
    country_total_series = {}
    for cname, cdata in countries.items():
        country_total_series[cname] = cdata["total_by_year"]

    # =====================================================================
    # 2. Interrupted Time Series (ITS)
    # =====================================================================
    # ITS on PEPFAR non-HIV trials
    its_pepfar_nonhiv = ols_its(
        YEARS,
        [pepfar_nonhiv_by_year[y] for y in YEARS],
        PEPFAR_EFFECT_YEAR,
    )

    # ITS on PEPFAR total trials (for comparison)
    its_pepfar_total = ols_its(
        YEARS,
        [pepfar_total_by_year[y] for y in YEARS],
        PEPFAR_EFFECT_YEAR,
    )

    # ITS on control total trials (should show no intervention effect)
    its_control = ols_its(
        YEARS,
        [control_total_by_year[y] for y in YEARS],
        PEPFAR_EFFECT_YEAR,
    )

    # =====================================================================
    # 3. Difference-in-Differences (DiD)
    # =====================================================================
    pre_years_did = list(range(2000, 2004))   # 2000-2003
    post_years_did = list(range(2004, 2011))  # 2004-2010

    # DiD on total trials per country (average per year)
    pepfar_pre_avg = sum(pepfar_total_by_year[y] for y in pre_years_did) / len(pre_years_did)
    pepfar_post_avg = sum(pepfar_total_by_year[y] for y in post_years_did) / len(post_years_did)
    control_pre_avg = sum(control_total_by_year[y] for y in pre_years_did) / len(pre_years_did)
    control_post_avg = sum(control_total_by_year[y] for y in post_years_did) / len(post_years_did)

    did_total = did_estimate(pepfar_pre_avg, pepfar_post_avg, control_pre_avg, control_post_avg)

    # DiD on PEPFAR non-HIV trials vs control total (normalized)
    pepfar_nonhiv_pre = sum(pepfar_nonhiv_by_year[y] for y in pre_years_did) / len(pre_years_did)
    pepfar_nonhiv_post = sum(pepfar_nonhiv_by_year[y] for y in post_years_did) / len(post_years_did)

    did_nonhiv = did_estimate(pepfar_nonhiv_pre, pepfar_nonhiv_post, control_pre_avg, control_post_avg)

    # Sensitivity: different post-period windows
    sensitivity_windows = [
        ("2004-2008", list(range(2004, 2009))),
        ("2004-2010", list(range(2004, 2011))),
        ("2004-2015", list(range(2004, 2016))),
        ("2004-2020", list(range(2004, 2021))),
    ]
    sensitivity_results = []
    for label, post_yrs in sensitivity_windows:
        p_pre = sum(pepfar_total_by_year[y] for y in pre_years_did) / len(pre_years_did)
        p_post = sum(pepfar_total_by_year[y] for y in post_yrs) / len(post_yrs)
        c_pre = sum(control_total_by_year[y] for y in pre_years_did) / len(pre_years_did)
        c_post = sum(control_total_by_year[y] for y in post_yrs) / len(post_yrs)
        d = did_estimate(p_pre, p_post, c_pre, c_post)
        sensitivity_results.append({
            "window": label,
            "did": round(d["did_estimate"], 2),
            "pepfar_change": round(d["pepfar_change"], 2),
            "control_change": round(d["control_change"], 2),
        })

    # =====================================================================
    # 4. Synthetic Control (Uganda)
    # =====================================================================
    uganda_total = countries.get("Uganda", {}).get("total_by_year", {})
    uganda_nonhiv = countries.get("Uganda", {}).get("nonhiv_by_year", {})

    # Donors: all non-PEPFAR countries
    donors_total = {}
    for cname in NON_PEPFAR_COUNTRIES:
        if cname in countries:
            donors_total[cname] = countries[cname]["total_by_year"]

    pre_sc = list(range(2000, 2004))
    post_sc = list(range(2004, 2026))

    sc_result_total = synthetic_control(uganda_total, donors_total, pre_sc, post_sc)

    # Also do SC for Uganda non-HIV trials vs control total
    sc_result_nonhiv = None
    if uganda_nonhiv:
        sc_result_nonhiv = synthetic_control(uganda_nonhiv, donors_total, pre_sc, post_sc)

    # =====================================================================
    # 5. Country-level summaries
    # =====================================================================
    country_summaries = {}
    for cname, cdata in countries.items():
        pre_total = sum(cdata["total_by_year"].get(str(y), 0) for y in pre_years_did)
        post_total = sum(cdata["total_by_year"].get(str(y), 0) for y in post_years_did)
        pre_avg = pre_total / len(pre_years_did) if pre_years_did else 0
        post_avg = post_total / len(post_years_did) if post_years_did else 0
        growth = post_avg - pre_avg

        cs = {
            "group": cdata["group"],
            "pre_avg": round(pre_avg, 1),
            "post_avg": round(post_avg, 1),
            "growth": round(growth, 1),
            "growth_pct": round(100 * growth / pre_avg, 1) if pre_avg > 0 else 0,
        }
        if cdata["group"] == "pepfar":
            pre_hiv = sum(cdata.get("hiv_by_year", {}).get(str(y), 0) for y in pre_years_did)
            post_hiv = sum(cdata.get("hiv_by_year", {}).get(str(y), 0) for y in post_years_did)
            pre_nonhiv = sum(cdata.get("nonhiv_by_year", {}).get(str(y), 0) for y in pre_years_did)
            post_nonhiv = sum(cdata.get("nonhiv_by_year", {}).get(str(y), 0) for y in post_years_did)
            cs["pre_hiv_avg"] = round(pre_hiv / len(pre_years_did), 1) if pre_years_did else 0
            cs["post_hiv_avg"] = round(post_hiv / len(post_years_did), 1) if post_years_did else 0
            cs["pre_nonhiv_avg"] = round(pre_nonhiv / len(pre_years_did), 1) if pre_years_did else 0
            cs["post_nonhiv_avg"] = round(post_nonhiv / len(post_years_did), 1) if post_years_did else 0
        country_summaries[cname] = cs

    return {
        "series": {
            "pepfar_total": {str(y): pepfar_total_by_year[y] for y in YEARS},
            "pepfar_hiv": {str(y): pepfar_hiv_by_year[y] for y in YEARS},
            "pepfar_nonhiv": {str(y): pepfar_nonhiv_by_year[y] for y in YEARS},
            "control_total": {str(y): control_total_by_year[y] for y in YEARS},
        },
        "its": {
            "pepfar_nonhiv": its_pepfar_nonhiv,
            "pepfar_total": its_pepfar_total,
            "control": its_control,
        },
        "did": {
            "total": did_total,
            "nonhiv": did_nonhiv,
        },
        "sensitivity": sensitivity_results,
        "synthetic_control_total": sc_result_total,
        "synthetic_control_nonhiv": sc_result_nonhiv,
        "country_summaries": country_summaries,
    }


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def sign_str(val):
    """Return a signed string representation."""
    if val >= 0:
        return f"+{val:.1f}"
    return f"{val:.1f}"


def generate_html(data, analysis):
    """Generate the full HTML dashboard."""
    series = analysis["series"]
    its = analysis["its"]
    did = analysis["did"]
    sensitivity = analysis["sensitivity"]
    sc_total = analysis["synthetic_control_total"]
    sc_nonhiv = analysis["synthetic_control_nonhiv"]
    cs = analysis["country_summaries"]

    # --- Chart.js data ---
    years_json = json.dumps(YEARS)
    pepfar_total_json = json.dumps([series["pepfar_total"].get(str(y), 0) for y in YEARS])
    pepfar_hiv_json = json.dumps([series["pepfar_hiv"].get(str(y), 0) for y in YEARS])
    pepfar_nonhiv_json = json.dumps([series["pepfar_nonhiv"].get(str(y), 0) for y in YEARS])
    control_total_json = json.dumps([series["control_total"].get(str(y), 0) for y in YEARS])

    # ITS fitted lines for PEPFAR non-HIV
    its_nh = its["pepfar_nonhiv"]
    its_fitted_nonhiv = []
    for yr in YEARS:
        t = yr - YEARS[0]
        D = 1.0 if yr >= PEPFAR_EFFECT_YEAR else 0.0
        slope_chg = float(yr - PEPFAR_EFFECT_YEAR) if yr >= PEPFAR_EFFECT_YEAR else 0.0
        y_hat = its_nh["b0"] + its_nh["b1"] * t + its_nh["b2"] * D + its_nh["b3"] * slope_chg
        its_fitted_nonhiv.append(round(max(0, y_hat), 1))
    its_fitted_nonhiv_json = json.dumps(its_fitted_nonhiv)

    # ITS counterfactual (pre-trend extrapolated)
    its_counterfactual = []
    for yr in YEARS:
        t = yr - YEARS[0]
        y_cf = its_nh["b0"] + its_nh["b1"] * t  # just pre-trend
        its_counterfactual.append(round(max(0, y_cf), 1))
    its_counterfactual_json = json.dumps(its_counterfactual)

    # Synthetic control series
    sc = sc_total or {}
    sc_actual_json = json.dumps([sc.get("actual_series", {}).get(str(y), 0) for y in YEARS] if sc else [0] * len(YEARS))
    sc_synth_json = json.dumps([sc.get("synthetic_series", {}).get(str(y), 0) for y in YEARS] if sc else [0] * len(YEARS))
    sc_gap_json = json.dumps([sc.get("gap_series", {}).get(str(y), 0) for y in YEARS] if sc else [0] * len(YEARS))

    # Weights display
    sc_weights_str = ""
    if sc and sc.get("weights"):
        sc_weights_str = ", ".join(f"{c}: {w:.1%}" for c, w in sorted(sc["weights"].items(), key=lambda x: -x[1]))

    # DiD table data
    did_t = did["total"]
    did_nh = did["nonhiv"]

    # Country summary rows
    pepfar_rows = ""
    control_rows = ""
    for cname in PEPFAR_COUNTRIES:
        if cname not in cs:
            continue
        c = cs[cname]
        color = "#22c55e" if c["growth"] > 0 else "#ef4444"
        pepfar_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(cname)}</td>'
            f'<td style="padding:10px;text-align:right;">{c["pre_avg"]:.1f}</td>'
            f'<td style="padding:10px;text-align:right;">{c["post_avg"]:.1f}</td>'
            f'<td style="padding:10px;text-align:right;color:{color};font-weight:bold;">'
            f'{sign_str(c["growth"])}</td>'
            f'<td style="padding:10px;text-align:right;">{c.get("pre_hiv_avg", 0):.1f}</td>'
            f'<td style="padding:10px;text-align:right;">{c.get("post_hiv_avg", 0):.1f}</td>'
            f'<td style="padding:10px;text-align:right;">{c.get("pre_nonhiv_avg", 0):.1f}</td>'
            f'<td style="padding:10px;text-align:right;">{c.get("post_nonhiv_avg", 0):.1f}</td>'
            f'</tr>\n'
        )
    for cname in NON_PEPFAR_COUNTRIES:
        if cname not in cs:
            continue
        c = cs[cname]
        color = "#22c55e" if c["growth"] > 0 else "#ef4444"
        control_rows += (
            f'<tr>'
            f'<td style="padding:10px;">{escape_html(cname)}</td>'
            f'<td style="padding:10px;text-align:right;">{c["pre_avg"]:.1f}</td>'
            f'<td style="padding:10px;text-align:right;">{c["post_avg"]:.1f}</td>'
            f'<td style="padding:10px;text-align:right;color:{color};font-weight:bold;">'
            f'{sign_str(c["growth"])}</td>'
            f'</tr>\n'
        )

    # Sensitivity rows
    sens_rows = ""
    for s in sensitivity:
        d_color = "#ef4444" if s["did"] < 0 else "#22c55e"
        sens_rows += (
            f'<tr>'
            f'<td style="padding:10px;">2000-2003</td>'
            f'<td style="padding:10px;">{s["window"]}</td>'
            f'<td style="padding:10px;text-align:right;">{sign_str(s["pepfar_change"])}</td>'
            f'<td style="padding:10px;text-align:right;">{sign_str(s["control_change"])}</td>'
            f'<td style="padding:10px;text-align:right;color:{d_color};font-weight:bold;">'
            f'{sign_str(s["did"])}</td>'
            f'</tr>\n'
        )

    # Reference rows
    ref_rows = ""
    for ref in REFERENCES:
        ref_rows += (
            f'<li style="margin-bottom:0.5rem;">'
            f'<a href="{escape_html(ref["url"])}" '
            f'target="_blank" style="color:#3b82f6;">{escape_html(ref["id"])}</a> '
            f'&mdash; {escape_html(ref["desc"])}</li>\n'
        )

    # ITS interpretation
    level_change = its_nh["b2"]
    slope_change = its_nh["b3"]
    level_dir = "increase" if level_change >= 0 else "decrease"
    slope_dir = "acceleration" if slope_change >= 0 else "deceleration"
    level_se = its_nh["se"][2] if len(its_nh["se"]) > 2 else 0
    slope_se = its_nh["se"][3] if len(its_nh["se"]) > 3 else 0

    # DiD interpretation
    did_val = did_t["did_estimate"]
    did_dir = "higher" if did_val >= 0 else "lower"
    crowd_verdict = "no evidence of crowding out" if did_val >= 0 else "suggestive of crowding out"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PEPFAR Causal Inference: Did HIV Funding Crowd Out Everything Else?</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3"></script>
<style>
:root {{
  --bg: #0a0e17;
  --surface: #111827;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #3b82f6;
  --danger: #ef4444;
  --warning: #f59e0b;
  --success: #22c55e;
  --purple: #7c3aed;
  --teal: #14b8a6;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  line-height: 1.6;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 2rem; }}
h1 {{
  font-size: 2.4rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #ef4444, #3b82f6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
h2 {{
  font-size: 1.5rem;
  margin: 2.5rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--border);
  color: var(--accent);
}}
h3 {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
.subtitle {{ color: var(--muted); font-size: 1rem; margin-bottom: 2rem; }}
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1.5rem;
  margin-bottom: 2rem;
}}
.summary-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  text-align: center;
}}
.summary-card .value {{
  font-size: 2.5rem;
  font-weight: 800;
  margin: 0.5rem 0;
}}
.summary-card .label {{
  color: var(--muted);
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}
.danger {{ color: var(--danger); }}
.warning {{ color: var(--warning); }}
.success {{ color: var(--success); }}
.purple {{ color: var(--purple); }}
.teal {{ color: var(--teal); }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 1.5rem;
}}
th {{
  background: #1a2332;
  padding: 10px 8px;
  text-align: left;
  font-size: 0.85rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}}
td {{
  border-bottom: 1px solid var(--border);
  padding: 8px;
  font-size: 0.9rem;
}}
tr:hover {{ background: rgba(59, 130, 246, 0.05); }}
.chart-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}}
.insight-box {{
  background: var(--surface);
  border-left: 4px solid var(--danger);
  border-radius: 0 8px 8px 0;
  padding: 1.25rem 1.5rem;
  margin: 1.5rem 0;
  font-size: 0.95rem;
  line-height: 1.7;
}}
.insight-box.success-box {{ border-left-color: var(--success); }}
.insight-box.warning-box {{ border-left-color: var(--warning); }}
.insight-box.purple-box {{ border-left-color: var(--purple); }}
.insight-box.teal-box {{ border-left-color: var(--teal); }}
.method-box {{
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: 8px;
  padding: 1rem 1.5rem;
  margin: 1rem 0;
  font-size: 0.9rem;
}}
.two-col {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}}
@media (max-width: 900px) {{
  .two-col {{ grid-template-columns: 1fr; }}
}}
.footer {{
  margin-top: 3rem;
  padding-top: 2rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.85rem;
}}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.coeff {{ font-family: 'Consolas', monospace; background: rgba(255,255,255,0.05); padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>
<div class="container">

<!-- ============================================================ -->
<!-- HEADER -->
<!-- ============================================================ -->

<h1>PEPFAR Causal Inference</h1>
<p class="subtitle">
  Did HIV funding crowd out everything else? Quasi-experimental evidence from
  ClinicalTrials.gov (2000&ndash;2025) across 15 African countries using ITS,
  Difference-in-Differences, and Synthetic Control methods.
</p>

<div class="summary-grid">
  <div class="summary-card">
    <div class="label">ITS Level Change</div>
    <div class="value {'danger' if level_change < 0 else 'success'}">{sign_str(level_change)}</div>
    <div class="label">non-HIV trials at PEPFAR onset</div>
  </div>
  <div class="summary-card">
    <div class="label">ITS Slope Change</div>
    <div class="value {'danger' if slope_change < 0 else 'success'}">{sign_str(slope_change)}</div>
    <div class="label">annual trend shift post-PEPFAR</div>
  </div>
  <div class="summary-card">
    <div class="label">DiD Estimate (Total)</div>
    <div class="value {'danger' if did_val < 0 else 'success'}">{sign_str(did_val)}</div>
    <div class="label">trials/year relative to controls</div>
  </div>
  <div class="summary-card">
    <div class="label">Synthetic Control Gap</div>
    <div class="value {'danger' if (sc_total or {{}}).get('avg_gap_post', 0) < 0 else 'success'}">{sign_str((sc_total or {{}}).get('avg_gap_post', 0))}</div>
    <div class="label">Uganda vs synthetic (post-period avg)</div>
  </div>
</div>

<div class="method-box">
  <strong>Study design:</strong> We apply three quasi-experimental methods to test
  whether PEPFAR (launched 2003) caused a measurable change in non-HIV trial starts
  in recipient African countries.<br><br>
  <strong>Treatment group:</strong> 10 PEPFAR-focus countries (Uganda, Kenya, Tanzania,
  Mozambique, South Africa, Nigeria, Zambia, Malawi, Zimbabwe, Ethiopia).<br>
  <strong>Control group:</strong> 5 non-PEPFAR countries (Ghana, Senegal, Burkina Faso,
  DRC, Cameroon).<br>
  <strong>Pre-period:</strong> 2000&ndash;2003 | <strong>Post-period:</strong> 2004&ndash;2025.<br>
  <strong>Outcome:</strong> Annual count of new interventional trial registrations on
  ClinicalTrials.gov, decomposed into HIV and non-HIV trials for PEPFAR countries.
</div>

<!-- ============================================================ -->
<!-- SECTION 1: Time Series Overview -->
<!-- ============================================================ -->

<h2>1. Time Series Overview</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  Annual trial starts for PEPFAR countries (total, HIV, non-HIV) and non-PEPFAR controls.
  The vertical line marks 2004, the first full year of PEPFAR implementation.
</p>

<div class="chart-container">
  <canvas id="timeSeriesChart" height="100"></canvas>
</div>

<div class="insight-box warning-box">
  <strong>Key observation:</strong> Examine whether PEPFAR countries show a divergence
  in total trials (driven primarily by HIV growth) while non-HIV trials stagnate or
  grow more slowly than the counterfactual trend. If the non-HIV line falls below what
  the pre-PEPFAR trend would predict, this supports the crowding-out hypothesis.
</div>

<!-- ============================================================ -->
<!-- SECTION 2: Interrupted Time Series (ITS) -->
<!-- ============================================================ -->

<h2>2. Interrupted Time Series Analysis</h2>

<div class="method-box">
  <strong>ITS model:</strong> Y<sub>t</sub> = &beta;<sub>0</sub> + &beta;<sub>1</sub>&middot;time
  + &beta;<sub>2</sub>&middot;D + &beta;<sub>3</sub>&middot;D&middot;(time &minus; T<sub>0</sub>) + &epsilon;<sub>t</sub><br>
  where D = 1 for t &ge; 2004, and T<sub>0</sub> = 2004 (Bernal et al., 2017).<br><br>
  &beta;<sub>2</sub> captures the <strong>immediate level change</strong> at PEPFAR onset.<br>
  &beta;<sub>3</sub> captures the <strong>change in trend</strong> (slope) after PEPFAR.
</div>

<div class="chart-container">
  <canvas id="itsChart" height="100"></canvas>
</div>

<div class="two-col">
  <div>
    <h3>PEPFAR Non-HIV Trials (Treatment)</h3>
    <table>
      <tr><td style="padding:8px;">Baseline intercept (&beta;<sub>0</sub>)</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its_nh['b0']:.2f}</td></tr>
      <tr><td style="padding:8px;">Pre-intervention slope (&beta;<sub>1</sub>)</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its_nh['b1']:.2f}</td></tr>
      <tr><td style="padding:8px;">Level change at 2004 (&beta;<sub>2</sub>)</td>
          <td style="padding:8px;text-align:right;font-weight:bold;color:{'#ef4444' if level_change < 0 else '#22c55e'};"
              class="coeff">{sign_str(level_change)} (SE {level_se:.2f})</td></tr>
      <tr><td style="padding:8px;">Slope change (&beta;<sub>3</sub>)</td>
          <td style="padding:8px;text-align:right;font-weight:bold;color:{'#ef4444' if slope_change < 0 else '#22c55e'};"
              class="coeff">{sign_str(slope_change)} (SE {slope_se:.2f})</td></tr>
      <tr><td style="padding:8px;">R&sup2;</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its_nh['r_sq']:.3f}</td></tr>
    </table>
  </div>
  <div>
    <h3>Non-PEPFAR Controls (No Intervention)</h3>
    <table>
      <tr><td style="padding:8px;">Baseline intercept (&beta;<sub>0</sub>)</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its['control']['b0']:.2f}</td></tr>
      <tr><td style="padding:8px;">Pre-intervention slope (&beta;<sub>1</sub>)</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its['control']['b1']:.2f}</td></tr>
      <tr><td style="padding:8px;">Level change at 2004 (&beta;<sub>2</sub>)</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its['control']['b2']:.2f}</td></tr>
      <tr><td style="padding:8px;">Slope change (&beta;<sub>3</sub>)</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its['control']['b3']:.2f}</td></tr>
      <tr><td style="padding:8px;">R&sup2;</td>
          <td style="padding:8px;text-align:right;" class="coeff">{its['control']['r_sq']:.3f}</td></tr>
    </table>
  </div>
</div>

<div class="insight-box {'danger' if level_change < 0 else 'success-box'}">
  <strong>ITS interpretation:</strong> At the point of PEPFAR implementation (2004),
  non-HIV trial starts in PEPFAR countries showed a {level_dir} of
  <strong>{abs(level_change):.1f} trials/year</strong> (SE = {level_se:.2f}),
  and a post-intervention trend {slope_dir} of
  <strong>{abs(slope_change):.2f} trials/year&sup2;</strong> (SE = {slope_se:.2f}).
  The dashed line shows the counterfactual &mdash; where non-HIV trials would have
  been had the pre-PEPFAR trend simply continued. The gap between actual and
  counterfactual represents the estimated causal effect.
</div>

<!-- ============================================================ -->
<!-- SECTION 3: Difference-in-Differences -->
<!-- ============================================================ -->

<h2>3. Difference-in-Differences (DiD)</h2>

<div class="method-box">
  <strong>DiD estimator:</strong> &Delta;&Delta; = (&bar;Y;<sub>treat,post</sub> &minus;
  &bar;Y;<sub>treat,pre</sub>) &minus; (&bar;Y;<sub>control,post</sub> &minus;
  &bar;Y;<sub>control,pre</sub>)<br><br>
  If PEPFAR crowded out non-HIV research, &Delta;&Delta; should be <strong>negative</strong>
  &mdash; PEPFAR countries grew less (or declined more) in total trial activity relative
  to control countries, after adjusting for the common time trend.
</div>

<h3>DiD: Total Trial Starts (avg per year)</h3>
<table>
<thead>
<tr>
  <th>Group</th>
  <th style="text-align:right;">Pre (2000-03)</th>
  <th style="text-align:right;">Post (2004-10)</th>
  <th style="text-align:right;">Change</th>
</tr>
</thead>
<tbody>
<tr>
  <td style="padding:10px;">PEPFAR Countries (Treatment)</td>
  <td style="padding:10px;text-align:right;">{did_t['pepfar_pre']:.1f}</td>
  <td style="padding:10px;text-align:right;">{did_t['pepfar_post']:.1f}</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{sign_str(did_t['pepfar_change'])}</td>
</tr>
<tr>
  <td style="padding:10px;">Non-PEPFAR Countries (Control)</td>
  <td style="padding:10px;text-align:right;">{did_t['control_pre']:.1f}</td>
  <td style="padding:10px;text-align:right;">{did_t['control_post']:.1f}</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{sign_str(did_t['control_change'])}</td>
</tr>
<tr style="background:rgba(59,130,246,0.1);">
  <td style="padding:10px;font-weight:bold;">DiD Estimate (&Delta;&Delta;)</td>
  <td colspan="2"></td>
  <td style="padding:10px;text-align:right;font-weight:bold;font-size:1.2rem;color:{'#ef4444' if did_val < 0 else '#22c55e'};">
    {sign_str(did_val)}</td>
</tr>
</tbody>
</table>

<h3>DiD: PEPFAR Non-HIV Trials vs Control Total</h3>
<table>
<thead>
<tr>
  <th>Group</th>
  <th style="text-align:right;">Pre (2000-03)</th>
  <th style="text-align:right;">Post (2004-10)</th>
  <th style="text-align:right;">Change</th>
</tr>
</thead>
<tbody>
<tr>
  <td style="padding:10px;">PEPFAR Non-HIV Trials</td>
  <td style="padding:10px;text-align:right;">{did_nh['pepfar_pre']:.1f}</td>
  <td style="padding:10px;text-align:right;">{did_nh['pepfar_post']:.1f}</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{sign_str(did_nh['pepfar_change'])}</td>
</tr>
<tr>
  <td style="padding:10px;">Non-PEPFAR Total Trials</td>
  <td style="padding:10px;text-align:right;">{did_nh['control_pre']:.1f}</td>
  <td style="padding:10px;text-align:right;">{did_nh['control_post']:.1f}</td>
  <td style="padding:10px;text-align:right;font-weight:bold;">{sign_str(did_nh['control_change'])}</td>
</tr>
<tr style="background:rgba(59,130,246,0.1);">
  <td style="padding:10px;font-weight:bold;">DiD Estimate (&Delta;&Delta;)</td>
  <td colspan="2"></td>
  <td style="padding:10px;text-align:right;font-weight:bold;font-size:1.2rem;color:{'#ef4444' if did_nh['did_estimate'] < 0 else '#22c55e'};">
    {sign_str(did_nh['did_estimate'])}</td>
</tr>
</tbody>
</table>

<div class="insight-box {'danger' if did_val < 0 else 'success-box'}">
  <strong>DiD interpretation:</strong> The DiD estimate of <strong>{sign_str(did_val)}
  trials/year</strong> suggests that PEPFAR countries grew {did_dir} than control countries
  after adjusting for the common time trend. This is <strong>{crowd_verdict}</strong>.
  The key assumption (parallel trends) requires that both groups would have followed
  similar trajectories in the absence of PEPFAR.
</div>

<!-- ============================================================ -->
<!-- SECTION 4: Sensitivity Analysis -->
<!-- ============================================================ -->

<h2>4. Sensitivity Analysis: Different Post-Period Windows</h2>
<p style="color:var(--muted);margin-bottom:1rem;">
  How robust is the DiD estimate to different post-period definitions?
  If the effect is consistent across windows, it strengthens the causal claim.
</p>

<table>
<thead>
<tr>
  <th>Pre-Period</th>
  <th>Post-Period</th>
  <th style="text-align:right;">PEPFAR Change</th>
  <th style="text-align:right;">Control Change</th>
  <th style="text-align:right;">DiD (&Delta;&Delta;)</th>
</tr>
</thead>
<tbody>
{sens_rows}
</tbody>
</table>

<!-- ============================================================ -->
<!-- SECTION 5: Synthetic Control -->
<!-- ============================================================ -->

<h2>5. Synthetic Control Method: Uganda</h2>

<div class="method-box">
  <strong>Method:</strong> Following Abadie, Diamond &amp; Hainmueller (2010), we construct
  a &ldquo;synthetic Uganda&rdquo; from a weighted combination of non-PEPFAR countries that
  best matches Uganda's pre-PEPFAR trial trajectory (2000&ndash;2003). Post-2004, the gap
  between actual Uganda and synthetic Uganda represents the estimated causal effect of PEPFAR.<br><br>
  <strong>Donor pool:</strong> Ghana, Senegal, Burkina Faso, DRC, Cameroon.<br>
  <strong>Weights:</strong> {sc_weights_str if sc_weights_str else 'N/A'}
</div>

<div class="chart-container">
  <canvas id="synthChart" height="100"></canvas>
</div>

<div class="chart-container">
  <canvas id="gapChart" height="80"></canvas>
</div>

<div class="two-col">
  <div>
    <h3>Synthetic Control Fit Statistics</h3>
    <table>
      <tr><td style="padding:8px;">Pre-period RMSPE</td>
          <td style="padding:8px;text-align:right;" class="coeff">{(sc_total or {{}}).get('rmspe_pre', 0):.3f}</td></tr>
      <tr><td style="padding:8px;">Post-period avg gap</td>
          <td style="padding:8px;text-align:right;font-weight:bold;" class="coeff">{sign_str((sc_total or {{}}).get('avg_gap_post', 0))}</td></tr>
    </table>
  </div>
  <div>
    <h3>Donor Weights</h3>
    <table>
      <thead><tr><th>Country</th><th style="text-align:right;">Weight</th></tr></thead>
      <tbody>"""

    # Add weight rows
    if sc and sc.get("weights"):
        for c, w in sorted(sc["weights"].items(), key=lambda x: -x[1]):
            html += (
                f'\n      <tr><td style="padding:8px;">{escape_html(c)}</td>'
                f'<td style="padding:8px;text-align:right;">{w:.1%}</td></tr>'
            )
    else:
        html += '\n      <tr><td colspan="2" style="padding:8px;color:var(--muted);">No data</td></tr>'

    html += f"""
      </tbody>
    </table>
  </div>
</div>

<div class="insight-box teal-box">
  <strong>Synthetic control interpretation:</strong> The gap between actual Uganda and
  its synthetic counterpart post-2004 estimates the causal effect of PEPFAR on Uganda's
  trial activity. A pre-period RMSPE of {(sc_total or {{}}).get('rmspe_pre', 0):.3f}
  indicates {'good' if (sc_total or {{}}).get('rmspe_pre', 0) < 5 else 'moderate'} fit
  during the matching period. The average post-period gap of
  <strong>{sign_str((sc_total or {{}}).get('avg_gap_post', 0))}</strong> trials/year
  suggests that Uganda's actual trajectory {'exceeded' if (sc_total or {{}}).get('avg_gap_post', 0) >= 0 else 'fell below'}
  what would have been expected without PEPFAR.
</div>

<!-- ============================================================ -->
<!-- SECTION 6: Country-Level Details -->
<!-- ============================================================ -->

<h2>6. Country-Level Pre/Post Comparison</h2>

<h3>PEPFAR Countries (Treatment Group)</h3>
<div style="overflow-x:auto;">
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Pre Avg<br>(total)</th>
  <th style="text-align:right;">Post Avg<br>(total)</th>
  <th style="text-align:right;">Change</th>
  <th style="text-align:right;">Pre Avg<br>(HIV)</th>
  <th style="text-align:right;">Post Avg<br>(HIV)</th>
  <th style="text-align:right;">Pre Avg<br>(non-HIV)</th>
  <th style="text-align:right;">Post Avg<br>(non-HIV)</th>
</tr>
</thead>
<tbody>
{pepfar_rows}
</tbody>
</table>
</div>

<h3>Non-PEPFAR Countries (Control Group)</h3>
<div style="overflow-x:auto;">
<table>
<thead>
<tr>
  <th>Country</th>
  <th style="text-align:right;">Pre Avg (total)</th>
  <th style="text-align:right;">Post Avg (total)</th>
  <th style="text-align:right;">Change</th>
</tr>
</thead>
<tbody>
{control_rows}
</tbody>
</table>
</div>

<!-- ============================================================ -->
<!-- SECTION 7: Confounders & Limitations -->
<!-- ============================================================ -->

<h2>7. Confounders, Limitations &amp; Comparison with Literature</h2>

<div class="insight-box warning-box">
  <strong>Key confounders to consider:</strong>
  <ul style="margin-top:0.5rem;padding-left:1.5rem;">
    <li><strong>GDP growth:</strong> PEPFAR countries may differ in economic trajectories,
    which independently affects research capacity. South Africa and Nigeria have larger
    economies that support more trials regardless of PEPFAR.</li>
    <li><strong>General research expansion:</strong> The 2000s saw global research growth
    worldwide. Both groups benefit from this secular trend, which the DiD design
    differentially accounts for, but the ITS may conflate it with PEPFAR effects.</li>
    <li><strong>HIV epidemic severity:</strong> PEPFAR countries were selected BECAUSE
    of high HIV burden. High HIV prevalence independently motivates HIV trials regardless
    of PEPFAR funding &mdash; a classic selection-on-the-outcome problem.</li>
    <li><strong>Other health aid:</strong> Global Fund, bilateral aid, and Gates Foundation
    funding flowed alongside PEPFAR, making attribution difficult.</li>
    <li><strong>ClinicalTrials.gov registration bias:</strong> Registration became
    mandatory (ICMJE, 2005) mid-study period, creating a step change in visible trials
    that is unrelated to PEPFAR.</li>
  </ul>
</div>

<div class="insight-box purple-box">
  <strong>Comparison with Bendavid &amp; Bhatt (2009):</strong> Their landmark JAMA
  analysis found that PEPFAR funding was associated with <strong>large, significant
  declines in primary care, nutrition, and other health services</strong> in recipient
  countries relative to non-PEPFAR comparators. Our trial registry analysis extends
  this to the research domain: if PEPFAR created institutional incentives to focus on
  HIV at the expense of other diseases, we would expect non-HIV trial starts to grow
  more slowly in PEPFAR countries &mdash; which is testable with the data above.<br><br>
  <strong>Key difference:</strong> Bendavid &amp; Bhatt used DHS survey data on health
  outcomes (immunization, family planning). We use ClinicalTrials.gov registry data on
  research activity. The mechanisms differ: their finding concerns health service delivery
  crowding out, while ours concerns research priority crowding out. Both reflect the
  same fundamental resource allocation question.
</div>

<div class="insight-box">
  <strong>Limitations of this analysis:</strong>
  <ul style="margin-top:0.5rem;padding-left:1.5rem;">
    <li>Ecological design: country-level aggregation masks within-country variation.</li>
    <li>ClinicalTrials.gov does not capture all African-led research, especially studies
    registered on other platforms (Pan African Clinical Trials Registry, WHO ICTRP).</li>
    <li>The pre-PEPFAR period (2000-2003) is short (4 years), limiting power for the
    ITS and synthetic control pre-period matching.</li>
    <li>Non-PEPFAR control countries may not satisfy the parallel trends assumption
    (a formal test requires more pre-period data).</li>
    <li>Trial registration dates do not perfectly reflect when research capacity
    decisions were made or when funding flowed.</li>
    <li>The synthetic control uses only 5 donor countries, limiting the convex
    hull of possible synthetic units.</li>
  </ul>
</div>

<!-- ============================================================ -->
<!-- SECTION 8: References -->
<!-- ============================================================ -->

<h2>8. References</h2>
<ul style="padding-left:1.5rem;margin-bottom:2rem;">
{ref_rows}
</ul>

<!-- ============================================================ -->
<!-- FOOTER -->
<!-- ============================================================ -->

<div class="footer">
  <p>Data: ClinicalTrials.gov API v2, queried {data.get('timestamp', 'N/A')[:10]}.
  15 countries &times; 26 years. Analysis uses pure Python OLS for ITS, simple
  averages for DiD, and coordinate-descent synthetic control weights.</p>
  <p style="margin-top:0.5rem;">PEPFAR Causal Inference Dashboard v1.0 |
  Project 48 of the Africa RCT Landscape series</p>
</div>

</div>

<!-- ============================================================ -->
<!-- CHARTS -->
<!-- ============================================================ -->

<script>
const YEARS = {years_json};
const PEPFAR_YEAR_LINE = 2004;

// Annotation plugin for vertical line
const pepfarAnnotation = {{
  type: 'line',
  xMin: YEARS.indexOf(PEPFAR_YEAR_LINE),
  xMax: YEARS.indexOf(PEPFAR_YEAR_LINE),
  borderColor: 'rgba(239, 68, 68, 0.6)',
  borderWidth: 2,
  borderDash: [6, 4],
  label: {{
    content: 'PEPFAR 2004',
    display: true,
    position: 'start',
    color: '#ef4444',
    backgroundColor: 'rgba(10, 14, 23, 0.8)',
    font: {{ size: 11 }},
  }},
}};

// ── Chart 1: Time Series Overview ──
new Chart(document.getElementById('timeSeriesChart'), {{
  type: 'line',
  data: {{
    labels: YEARS,
    datasets: [
      {{
        label: 'PEPFAR Total Trials',
        data: {pepfar_total_json},
        borderColor: '#7c3aed',
        backgroundColor: 'rgba(124, 58, 237, 0.1)',
        borderWidth: 2.5,
        fill: false,
        tension: 0.3,
        pointRadius: 2,
      }},
      {{
        label: 'PEPFAR HIV Trials',
        data: {pepfar_hiv_json},
        borderColor: '#ef4444',
        backgroundColor: 'rgba(239, 68, 68, 0.1)',
        borderWidth: 2,
        fill: false,
        tension: 0.3,
        pointRadius: 2,
      }},
      {{
        label: 'PEPFAR Non-HIV Trials',
        data: {pepfar_nonhiv_json},
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 2,
        fill: false,
        tension: 0.3,
        pointRadius: 2,
      }},
      {{
        label: 'Non-PEPFAR Total Trials',
        data: {control_total_json},
        borderColor: '#22c55e',
        backgroundColor: 'rgba(34, 197, 94, 0.1)',
        borderWidth: 2,
        borderDash: [5, 5],
        fill: false,
        tension: 0.3,
        pointRadius: 2,
      }},
    ],
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ labels: {{ color: '#94a3b8' }} }},
      annotation: {{ annotations: {{ pepfarLine: pepfarAnnotation }} }},
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#94a3b8', maxTicksLimit: 13 }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
      y: {{
        title: {{ display: true, text: 'Trial Starts / Year', color: '#94a3b8' }},
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
    }},
  }},
}});

// ── Chart 2: ITS with fitted lines ──
new Chart(document.getElementById('itsChart'), {{
  type: 'line',
  data: {{
    labels: YEARS,
    datasets: [
      {{
        label: 'PEPFAR Non-HIV (Actual)',
        data: {pepfar_nonhiv_json},
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 2,
        fill: false,
        tension: 0.1,
        pointRadius: 3,
        pointBackgroundColor: '#f59e0b',
      }},
      {{
        label: 'ITS Fitted (Segmented)',
        data: {its_fitted_nonhiv_json},
        borderColor: '#3b82f6',
        borderWidth: 2.5,
        borderDash: [],
        fill: false,
        tension: 0,
        pointRadius: 0,
      }},
      {{
        label: 'Counterfactual (Pre-Trend Extended)',
        data: {its_counterfactual_json},
        borderColor: 'rgba(148, 163, 184, 0.5)',
        borderWidth: 2,
        borderDash: [8, 6],
        fill: false,
        tension: 0,
        pointRadius: 0,
      }},
      {{
        label: 'Non-PEPFAR Controls',
        data: {control_total_json},
        borderColor: '#22c55e',
        borderWidth: 1.5,
        borderDash: [4, 4],
        fill: false,
        tension: 0.3,
        pointRadius: 1,
      }},
    ],
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ labels: {{ color: '#94a3b8' }} }},
      annotation: {{ annotations: {{ pepfarLine: pepfarAnnotation }} }},
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#94a3b8', maxTicksLimit: 13 }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
      y: {{
        title: {{ display: true, text: 'Trial Starts / Year', color: '#94a3b8' }},
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
    }},
  }},
}});

// ── Chart 3: Synthetic Control ──
new Chart(document.getElementById('synthChart'), {{
  type: 'line',
  data: {{
    labels: YEARS,
    datasets: [
      {{
        label: 'Actual Uganda',
        data: {sc_actual_json},
        borderColor: '#f59e0b',
        backgroundColor: 'rgba(245, 158, 11, 0.1)',
        borderWidth: 2.5,
        fill: false,
        tension: 0.3,
        pointRadius: 3,
        pointBackgroundColor: '#f59e0b',
      }},
      {{
        label: 'Synthetic Uganda',
        data: {sc_synth_json},
        borderColor: '#14b8a6',
        borderWidth: 2.5,
        borderDash: [6, 4],
        fill: false,
        tension: 0.3,
        pointRadius: 2,
        pointBackgroundColor: '#14b8a6',
      }},
    ],
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ labels: {{ color: '#94a3b8' }} }},
      annotation: {{ annotations: {{ pepfarLine: pepfarAnnotation }} }},
      title: {{
        display: true,
        text: 'Uganda vs Synthetic Uganda (Total Trial Starts)',
        color: '#94a3b8',
        font: {{ size: 14 }},
      }},
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#94a3b8', maxTicksLimit: 13 }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
      y: {{
        title: {{ display: true, text: 'Trial Starts / Year', color: '#94a3b8' }},
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
    }},
  }},
}});

// ── Chart 4: Gap Plot ──
new Chart(document.getElementById('gapChart'), {{
  type: 'bar',
  data: {{
    labels: YEARS,
    datasets: [{{
      label: 'Gap (Actual - Synthetic)',
      data: {sc_gap_json},
      backgroundColor: {sc_gap_json}.map(v => v >= 0
        ? 'rgba(34, 197, 94, 0.6)'
        : 'rgba(239, 68, 68, 0.6)'),
      borderColor: {sc_gap_json}.map(v => v >= 0 ? '#22c55e' : '#ef4444'),
      borderWidth: 1,
    }}],
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ labels: {{ color: '#94a3b8' }} }},
      annotation: {{ annotations: {{ pepfarLine: pepfarAnnotation }} }},
      title: {{
        display: true,
        text: 'Gap: Actual Uganda minus Synthetic Uganda',
        color: '#94a3b8',
        font: {{ size: 14 }},
      }},
    }},
    scales: {{
      x: {{
        ticks: {{ color: '#94a3b8', maxTicksLimit: 13 }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
      y: {{
        title: {{ display: true, text: 'Gap (trials/year)', color: '#94a3b8' }},
        ticks: {{ color: '#94a3b8' }},
        grid: {{ color: 'rgba(30, 41, 59, 0.5)' }},
      }},
    }},
  }},
}});
</script>

</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("PEPFAR Causal Inference: Did HIV Funding Crowd Out Everything Else?")
    print("=" * 60)

    # 1. Fetch data
    print("\n[1/3] Fetching year-by-year trial counts...")
    data = fetch_all_data()

    # 2. Run analysis
    print("\n[2/3] Running causal inference analysis...")
    analysis = compute_analysis(data)

    # Print key results
    its_nh = analysis["its"]["pepfar_nonhiv"]
    did_t = analysis["did"]["total"]
    sc = analysis["synthetic_control_total"]

    print(f"\n--- Results Summary ---")
    print(f"ITS (PEPFAR non-HIV trials):")
    print(f"  Level change at 2004:  {its_nh['b2']:+.2f} (SE {its_nh['se'][2]:.2f})")
    print(f"  Slope change:          {its_nh['b3']:+.2f} (SE {its_nh['se'][3]:.2f})")
    print(f"  R-squared:             {its_nh['r_sq']:.3f}")
    print(f"\nDiD (total trials, 2000-03 vs 2004-10):")
    print(f"  PEPFAR change:  {did_t['pepfar_change']:+.1f} trials/year")
    print(f"  Control change: {did_t['control_change']:+.1f} trials/year")
    print(f"  DiD estimate:   {did_t['did_estimate']:+.1f} trials/year")
    if sc:
        print(f"\nSynthetic Control (Uganda):")
        print(f"  Pre-period RMSPE:   {sc['rmspe_pre']:.3f}")
        print(f"  Post-period avg gap: {sc['avg_gap_post']:+.2f}")
        print(f"  Weights: {sc.get('weights', {})}")

    # 3. Generate HTML
    print("\n[3/3] Generating HTML dashboard...")
    html = generate_html(data, analysis)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Dashboard written to {OUTPUT_HTML}")

    print("\nDone.")


if __name__ == "__main__":
    main()
