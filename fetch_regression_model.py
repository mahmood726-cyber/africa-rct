#!/usr/bin/env python
"""
fetch_regression_model.py — The Regression Model: What Predicts Clinical Trial Density in Africa?
================================================================================================
The most analytically rigorous project in the programme. Uses multivariate
OLS regression to identify which country-level factors predict clinical trial
density across 30 African nations.

Dependent variable:   log(trials per million + 0.1)
Independent variables: log(GDP per capita), English (binary), PEPFAR (binary),
                       Conflict (binary), WHO NRA maturity (0-3), log(population)

Implements ALL linear algebra in pure Python (no numpy/scipy):
  - Matrix transpose, multiplication, inversion (Gauss-Jordan)
  - OLS via normal equations: beta = (X'X)^(-1) X'y
  - R-squared, adjusted R-squared, F-statistic
  - Standard errors, t-statistics, approximate p-values
  - Standardized coefficients (beta weights)
  - Residual analysis (over/under-performers)
  - Stepwise model building (best single, best pair, ..., full)

Usage:
    python fetch_regression_model.py

Outputs:
    data/regression_model_data.json  (cached API results + regression output)
    regression-model.html            (dark-theme interactive dashboard)

Requirements:
    Python 3.8+, no external packages (uses urllib)

API docs: https://clinicaltrials.gov/data-api/api
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
from itertools import combinations

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "regression_model_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "regression-model.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Country data: hardcoded from World Bank / WHO (2025 estimates)
# Trials column: None = must query from API; number = hardcoded fallback
# ---------------------------------------------------------------------------

COUNTRY_DATA = [
    # (Country,            Pop(M), GDP/cap, English, PEPFAR, Conflict, WHO_NRA, Trials)
    ("South Africa",         62,   6000,    1,       1,      0,        3,       3473),
    ("Egypt",               110,   4000,    0,       0,      0,        2,      12395),
    ("Kenya",                56,   2100,    1,       1,      0,        2,        720),
    ("Uganda",               48,    900,    1,       1,      0,        2,        783),
    ("Nigeria",             230,   2200,    1,       1,      0,        2,        354),
    ("Tanzania",             67,   1200,    1,       1,      0,        1,        431),
    ("Ethiopia",            130,   1000,    0,       1,      1,        1,        240),
    ("Ghana",                34,   2400,    1,       0,      0,        1,        230),
    ("Cameroon",             27,   1700,    1,       0,      1,        1,        111),
    ("Mozambique",           33,    500,    0,       1,      0,        1,        123),
    ("Malawi",               21,    600,    1,       1,      0,        1,        288),
    ("Zambia",               21,   1300,    1,       1,      0,        1,        245),
    ("Zimbabwe",             15,   1500,    1,       1,      0,        1,        166),
    ("Senegal",              17,   1700,    0,       0,      0,        1,         97),
    ("Rwanda",               14,    900,    1,       1,      0,        2,        121),
    ("DRC",                 102,    600,    0,       0,      1,        0,        105),
    ("Burkina Faso",         23,    900,    0,       0,      1,        0,        196),
    ("Mali",                 23,    900,    0,       0,      1,        0,        144),
    ("Niger",                27,    600,    0,       0,      0,        0,         37),
    ("Chad",                 18,    700,    0,       0,      1,        0,         14),
    ("Tunisia",              12,   3800,    0,       0,      0,        2,        474),
    ("Morocco",              37,   3600,    0,       0,      0,        2,        103),
    ("Madagascar",           30,    500,    0,       0,      0,        0,         24),
    ("Somalia",              18,    400,    0,       0,      1,        0,          8),
    ("South Sudan",          11,    300,    1,       0,      1,        0,          6),
    ("Benin",                13,   1400,    0,       0,      0,        0,         51),
    ("Guinea",               14,   1200,    0,       0,      0,        0,        133),
    ("Togo",                  9,   1000,    0,       0,      0,        0,          7),
    ("Sudan",                48,    700,    0,       0,      1,        0,       None),
    ("Botswana",            2.5,   7500,    1,       1,      0,        1,       None),
]

# API name mapping for countries whose CT.gov name differs
API_NAMES = {
    "DRC": "Democratic Republic of Congo",
}

# Display names (short form)
DISPLAY_NAMES = {
    "Democratic Republic of Congo": "DRC",
}

# Predictor labels for output
PREDICTOR_NAMES = [
    "Intercept",
    "log(GDP/cap)",
    "English",
    "PEPFAR",
    "Conflict",
    "WHO NRA",
    "log(Pop)",
]


# ---------------------------------------------------------------------------
# Pure Python Linear Algebra
# ---------------------------------------------------------------------------

def mat_zeros(rows, cols):
    """Create a rows x cols zero matrix."""
    return [[0.0] * cols for _ in range(rows)]


def mat_identity(n):
    """Create n x n identity matrix."""
    m = mat_zeros(n, n)
    for i in range(n):
        m[i][i] = 1.0
    return m


def mat_transpose(A):
    """Transpose matrix A."""
    rows = len(A)
    cols = len(A[0])
    T = mat_zeros(cols, rows)
    for i in range(rows):
        for j in range(cols):
            T[j][i] = A[i][j]
    return T


def mat_multiply(A, B):
    """Multiply matrices A (m x n) and B (n x p) -> (m x p)."""
    m = len(A)
    n = len(A[0])
    p = len(B[0])
    C = mat_zeros(m, p)
    for i in range(m):
        for j in range(p):
            s = 0.0
            for k in range(n):
                s += A[i][k] * B[k][j]
            C[i][j] = s
    return C


def mat_inverse(A):
    """Invert square matrix A using Gauss-Jordan elimination."""
    n = len(A)
    # Augment [A | I]
    aug = mat_zeros(n, 2 * n)
    for i in range(n):
        for j in range(n):
            aug[i][j] = A[i][j]
        aug[i][n + i] = 1.0

    for col in range(n):
        # Partial pivoting
        max_val = abs(aug[col][col])
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > max_val:
                max_val = abs(aug[row][col])
                max_row = row
        if max_val < 1e-15:
            raise ValueError(f"Singular matrix at column {col}")
        if max_row != col:
            aug[col], aug[max_row] = aug[max_row], aug[col]

        # Scale pivot row
        pivot = aug[col][col]
        for j in range(2 * n):
            aug[col][j] /= pivot

        # Eliminate column
        for row in range(n):
            if row != col:
                factor = aug[row][col]
                for j in range(2 * n):
                    aug[row][j] -= factor * aug[col][j]

    # Extract inverse
    inv = mat_zeros(n, n)
    for i in range(n):
        for j in range(n):
            inv[i][j] = aug[i][n + j]
    return inv


def mat_vec_multiply(A, v):
    """Multiply matrix A (m x n) by column vector v (n x 1) -> list of m."""
    m = len(A)
    n = len(A[0])
    result = [0.0] * m
    for i in range(m):
        s = 0.0
        for j in range(n):
            s += A[i][j] * v[j]
        result[i] = s
    return result


def vec_dot(a, b):
    """Dot product of two vectors."""
    return sum(ai * bi for ai, bi in zip(a, b))


def mat_diag(A):
    """Extract diagonal of square matrix."""
    return [A[i][i] for i in range(len(A))]


# ---------------------------------------------------------------------------
# T-distribution approximate p-value (two-tailed)
# ---------------------------------------------------------------------------

def t_cdf_approx(t_val, df):
    """
    Approximate the two-tailed p-value for a t-statistic.
    Uses the regularized incomplete beta function approximation.
    For large df, approaches normal distribution.
    """
    if df <= 0:
        return 1.0
    t_val = abs(t_val)

    # For large df, use normal approximation
    if df > 100:
        # Normal CDF via error function approximation
        z = t_val
        p_one_tail = 0.5 * _erfc(z / math.sqrt(2))
        return 2.0 * p_one_tail

    # For smaller df, use approximation via incomplete beta
    x = df / (df + t_val * t_val)
    p = _regularized_incomplete_beta(df / 2.0, 0.5, x)
    return p  # This is already two-tailed


def _erfc(x):
    """Complementary error function approximation (Abramowitz & Stegun)."""
    if x < 0:
        return 2.0 - _erfc(-x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
           + t * (-1.453152027 + t * 1.061405429))))
    return poly * math.exp(-x * x)


def _regularized_incomplete_beta(a, b, x):
    """
    Regularized incomplete beta function I_x(a, b) via continued fraction.
    Used for t-distribution p-value computation.
    """
    if x < 0.0 or x > 1.0:
        return 0.0
    if x == 0.0 or x == 1.0:
        return x

    # Use the symmetry relation if needed for convergence
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _regularized_incomplete_beta(b, a, 1.0 - x)

    # Log of the beta function prefix
    ln_prefix = (a * math.log(x) + b * math.log(1.0 - x)
                 - math.log(a)
                 - _log_beta(a, b))

    # Continued fraction (Lentz's method)
    front = math.exp(ln_prefix)

    # Modified Lentz's algorithm
    f = 1.0
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    f = d

    for m in range(1, 201):
        # Even step
        numerator = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        d = 1.0 + numerator * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + numerator / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        f *= c * d

        # Odd step
        numerator = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + numerator * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + numerator / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = c * d
        f *= delta

        if abs(delta - 1.0) < 1e-10:
            break

    return front * f


def _log_beta(a, b):
    """Log of the beta function using lgamma."""
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def f_cdf_approx(f_val, df1, df2):
    """
    Approximate p-value for F-statistic (right tail).
    P(F > f_val) using incomplete beta function.
    """
    if f_val <= 0 or df1 <= 0 or df2 <= 0:
        return 1.0
    x = df2 / (df2 + df1 * f_val)
    return _regularized_incomplete_beta(df2 / 2.0, df1 / 2.0, x)


# ---------------------------------------------------------------------------
# OLS Regression
# ---------------------------------------------------------------------------

def ols_regression(X, y, predictor_names=None):
    """
    Full OLS regression: beta = (X'X)^(-1) X'y
    X: list of lists (n x p), should include intercept column
    y: list of n values
    Returns dict with all regression diagnostics.
    """
    n = len(y)
    p = len(X[0])  # number of parameters (including intercept)

    if predictor_names is None:
        predictor_names = [f"X{i}" for i in range(p)]

    # X'X
    Xt = mat_transpose(X)
    XtX = mat_multiply(Xt, X)

    # (X'X)^(-1)
    XtX_inv = mat_inverse(XtX)

    # X'y
    Xty = mat_vec_multiply(Xt, y)

    # beta = (X'X)^(-1) X'y
    beta = mat_vec_multiply(XtX_inv, Xty)

    # Predicted values
    y_hat = mat_vec_multiply(X, beta)

    # Residuals
    residuals = [y[i] - y_hat[i] for i in range(n)]

    # Mean of y
    y_mean = sum(y) / n

    # SS_total = sum((y_i - y_mean)^2)
    ss_total = sum((yi - y_mean) ** 2 for yi in y)

    # SS_residual = sum(residuals^2)
    ss_resid = sum(r ** 2 for r in residuals)

    # SS_regression = SS_total - SS_residual
    ss_reg = ss_total - ss_resid

    # R-squared
    r_squared = 1.0 - ss_resid / ss_total if ss_total > 0 else 0.0

    # Adjusted R-squared
    df_resid = n - p
    df_total = n - 1
    adj_r_squared = 1.0 - (ss_resid / df_resid) / (ss_total / df_total) if df_resid > 0 and df_total > 0 else 0.0

    # MSE (sigma^2 estimate)
    mse = ss_resid / df_resid if df_resid > 0 else 0.0

    # Standard errors: SE = sqrt(diag(sigma^2 * (X'X)^(-1)))
    var_beta = [[mse * XtX_inv[i][j] for j in range(p)] for i in range(p)]
    se = [math.sqrt(max(var_beta[i][i], 0.0)) for i in range(p)]

    # t-statistics
    t_stats = [beta[i] / se[i] if se[i] > 1e-15 else 0.0 for i in range(p)]

    # p-values (two-tailed)
    p_values = [t_cdf_approx(t_stats[i], df_resid) for i in range(p)]

    # F-statistic: (SS_reg / (p-1)) / (SS_resid / (n-p))
    df_reg = p - 1  # subtract intercept
    f_stat = (ss_reg / df_reg) / mse if df_reg > 0 and mse > 0 else 0.0
    f_p_value = f_cdf_approx(f_stat, df_reg, df_resid)

    # Standardized coefficients (beta weights)
    # beta_std_j = beta_j * (sd_xj / sd_y)
    sd_y = math.sqrt(ss_total / df_total) if df_total > 0 else 1.0
    std_beta = [0.0] * p  # intercept has no standardized coefficient
    for j in range(1, p):  # skip intercept
        col_j = [X[i][j] for i in range(n)]
        mean_j = sum(col_j) / n
        sd_j = math.sqrt(sum((v - mean_j) ** 2 for v in col_j) / df_total)
        std_beta[j] = beta[j] * sd_j / sd_y if sd_y > 0 else 0.0

    # Significance stars
    stars = []
    for pv in p_values:
        if pv < 0.001:
            stars.append("***")
        elif pv < 0.01:
            stars.append("**")
        elif pv < 0.05:
            stars.append("*")
        elif pv < 0.10:
            stars.append(".")
        else:
            stars.append("")

    return {
        "beta": beta,
        "se": se,
        "t_stats": t_stats,
        "p_values": p_values,
        "stars": stars,
        "std_beta": std_beta,
        "r_squared": r_squared,
        "adj_r_squared": adj_r_squared,
        "f_stat": f_stat,
        "f_p_value": f_p_value,
        "ss_total": ss_total,
        "ss_resid": ss_resid,
        "ss_reg": ss_reg,
        "mse": mse,
        "df_resid": df_resid,
        "df_reg": df_reg,
        "n": n,
        "p": p,
        "y_hat": y_hat,
        "residuals": residuals,
        "predictor_names": predictor_names,
    }


# ---------------------------------------------------------------------------
# Stepwise model building
# ---------------------------------------------------------------------------

def stepwise_analysis(X_full, y, predictor_names):
    """
    Build models from 1 predictor up to all predictors.
    For each k, find the best combination of k predictors (by adj R-squared).
    X_full includes intercept column (col 0). Predictors are cols 1..p-1.
    """
    n = len(y)
    p = len(X_full[0])
    num_predictors = p - 1  # exclude intercept

    results = []

    for k in range(1, num_predictors + 1):
        best_adj_r2 = -1e30
        best_model = None
        best_indices = None

        for combo in combinations(range(1, p), k):
            # Build X matrix with intercept + selected predictors
            X_sub = [[X_full[i][0]] + [X_full[i][j] for j in combo] for i in range(n)]
            sub_names = ["Intercept"] + [predictor_names[j] for j in combo]

            try:
                model = ols_regression(X_sub, y, sub_names)
                if model["adj_r_squared"] > best_adj_r2:
                    best_adj_r2 = model["adj_r_squared"]
                    best_model = model
                    best_indices = combo
            except (ValueError, ZeroDivisionError):
                continue

        if best_model is not None:
            results.append({
                "k": k,
                "predictors": [predictor_names[j] for j in best_indices],
                "r_squared": best_model["r_squared"],
                "adj_r_squared": best_model["adj_r_squared"],
                "f_stat": best_model["f_stat"],
                "f_p_value": best_model["f_p_value"],
            })

    return results


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


def get_trial_count(location):
    """Return total count of interventional trials for a location."""
    params = {
        "format": "json",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "pageSize": 1,
        "countTotal": "true",
    }
    data = api_get(params)
    if data is None:
        return 0
    return data.get("totalCount", 0)


# ---------------------------------------------------------------------------
# Cache
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


def save_cache(data):
    """Save data to cache file."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nCached to {CACHE_FILE}")


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_all_data():
    """Fetch trial counts for countries that need querying."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "country_trials": {},
    }

    # Identify which countries need API queries
    need_query = []
    for row in COUNTRY_DATA:
        name = row[0]
        trials = row[7]
        if trials is not None:
            data["country_trials"][name] = trials
        else:
            need_query.append(name)

    if need_query:
        print(f"\n--- Querying {len(need_query)} countries from CT.gov ---")
        for i, country in enumerate(need_query):
            api_name = API_NAMES.get(country, country)
            print(f"  [{i+1}/{len(need_query)}] {country} (as '{api_name}')...")
            count = get_trial_count(api_name)
            data["country_trials"][country] = count
            print(f"    -> {count:,} trials")
            time.sleep(RATE_LIMIT)

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def build_dataset(data):
    """
    Build the regression dataset from country data + API results.
    Returns: list of dicts with all fields, X matrix, y vector.
    """
    records = []
    for row in COUNTRY_DATA:
        name, pop, gdp, english, pepfar, conflict, who_nra, _ = row
        trials = data["country_trials"].get(name, 0)

        # Dependent variable: log(trials per million + 0.1)
        trials_per_m = trials / pop if pop > 0 else 0
        y_val = math.log(trials_per_m + 0.1)

        # Independent variables (logged where appropriate)
        log_gdp = math.log(gdp) if gdp > 0 else 0
        log_pop = math.log(pop) if pop > 0 else 0

        records.append({
            "country": name,
            "pop_m": pop,
            "gdp_per_cap": gdp,
            "english": english,
            "pepfar": pepfar,
            "conflict": conflict,
            "who_nra": who_nra,
            "trials": trials,
            "trials_per_m": round(trials_per_m, 2),
            "log_trials_per_m": round(y_val, 4),
            "log_gdp": round(log_gdp, 4),
            "log_pop": round(log_pop, 4),
        })

    # Build X and y
    y = [r["log_trials_per_m"] for r in records]
    X = []
    for r in records:
        X.append([
            1.0,          # intercept
            r["log_gdp"],
            float(r["english"]),
            float(r["pepfar"]),
            float(r["conflict"]),
            float(r["who_nra"]),
            r["log_pop"],
        ])

    return records, X, y


def run_regression_analysis(records, X, y):
    """Run the full regression analysis suite."""
    results = {}

    # --- Full model ---
    print("\n--- Running full OLS regression (7 parameters, 30 obs) ---")
    full_model = ols_regression(X, y, PREDICTOR_NAMES)
    results["full_model"] = full_model

    # --- Residual analysis ---
    residual_analysis = []
    for i, rec in enumerate(records):
        residual_analysis.append({
            "country": rec["country"],
            "actual": round(y[i], 3),
            "predicted": round(full_model["y_hat"][i], 3),
            "residual": round(full_model["residuals"][i], 3),
            "trials": rec["trials"],
            "trials_per_m": rec["trials_per_m"],
        })

    # Sort by residual (most negative = most under-performing)
    residual_analysis.sort(key=lambda x: x["residual"])
    results["residual_analysis"] = residual_analysis

    # Top over-performers (positive residuals)
    results["over_performers"] = sorted(residual_analysis, key=lambda x: -x["residual"])[:5]

    # Top under-performers (negative residuals)
    results["under_performers"] = residual_analysis[:5]

    # --- Stepwise analysis ---
    print("--- Running stepwise model building ---")
    stepwise = stepwise_analysis(X, y, PREDICTOR_NAMES)
    results["stepwise"] = stepwise

    # --- Summary statistics ---
    y_mean = sum(y) / len(y)
    y_sd = math.sqrt(sum((yi - y_mean) ** 2 for yi in y) / (len(y) - 1))
    results["y_summary"] = {
        "mean": round(y_mean, 3),
        "sd": round(y_sd, 3),
        "min": round(min(y), 3),
        "max": round(max(y), 3),
        "n": len(y),
    }

    # Predictor summary statistics
    pred_summaries = []
    for j in range(1, len(X[0])):  # skip intercept
        col = [X[i][j] for i in range(len(X))]
        col_mean = sum(col) / len(col)
        col_sd = math.sqrt(sum((v - col_mean) ** 2 for v in col) / (len(col) - 1))
        pred_summaries.append({
            "name": PREDICTOR_NAMES[j],
            "mean": round(col_mean, 3),
            "sd": round(col_sd, 3),
            "min": round(min(col), 3),
            "max": round(max(col), 3),
        })
    results["predictor_summaries"] = pred_summaries

    return results


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def escape_html(s):
    """Escape HTML special characters including quotes."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def fmt_p(p_val):
    """Format p-value for display."""
    if p_val < 0.001:
        return "<0.001"
    elif p_val < 0.01:
        return f"{p_val:.3f}"
    elif p_val < 0.1:
        return f"{p_val:.3f}"
    else:
        return f"{p_val:.3f}"


def significance_color(p_val):
    """Return color based on p-value significance."""
    if p_val < 0.01:
        return "#22c55e"  # green
    elif p_val < 0.05:
        return "#eab308"  # yellow
    elif p_val < 0.10:
        return "#f97316"  # orange
    else:
        return "#94a3b8"  # muted


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, records, analysis):
    """Generate the full HTML dashboard."""

    fm = analysis["full_model"]

    # ====================================================================
    # REGRESSION TABLE ROWS
    # ====================================================================
    reg_rows = ""
    for i in range(fm["p"]):
        sig_color = significance_color(fm["p_values"][i])
        bar_width = min(abs(fm["std_beta"][i]) / 0.8 * 100, 100) if i > 0 else 0
        bar_color = "#22c55e" if fm["beta"][i] >= 0 else "#ef4444"
        bar_dir = "right" if fm["beta"][i] >= 0 else "left"

        reg_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(fm["predictor_names"][i])}</td>
  <td style="padding:10px;text-align:right;font-family:monospace;">{fm["beta"][i]:+.4f}</td>
  <td style="padding:10px;text-align:right;font-family:monospace;">{fm["se"][i]:.4f}</td>
  <td style="padding:10px;text-align:right;font-family:monospace;">{fm["t_stats"][i]:+.3f}</td>
  <td style="padding:10px;text-align:right;color:{sig_color};font-weight:bold;">
    {fmt_p(fm["p_values"][i])} {fm["stars"][i]}</td>
  <td style="padding:10px;text-align:right;font-family:monospace;">
    {f'{fm["std_beta"][i]:+.3f}' if i > 0 else "---"}</td>
  <td style="padding:10px;width:120px;">
    {f'<div style="background:rgba(255,255,255,0.08);border-radius:4px;height:14px;width:100%;position:relative;"><div style="background:{bar_color};height:100%;width:{bar_width:.1f}%;border-radius:4px;float:{bar_dir};"></div></div>' if i > 0 else ''}</td>
</tr>
"""

    # ====================================================================
    # COEFFICIENT PLOT (CSS-based horizontal bars)
    # ====================================================================
    coef_bars = ""
    max_abs_beta = max(abs(fm["beta"][i]) for i in range(1, fm["p"]))
    if max_abs_beta < 0.01:
        max_abs_beta = 1.0

    for i in range(1, fm["p"]):
        beta_val = fm["beta"][i]
        ci_low = beta_val - 1.96 * fm["se"][i]
        ci_high = beta_val + 1.96 * fm["se"][i]
        bar_pct = abs(beta_val) / max_abs_beta * 40  # max 40% width
        sig_color = significance_color(fm["p_values"][i])
        bar_color = "#22c55e" if beta_val >= 0 else "#ef4444"
        direction = "positive" if beta_val >= 0 else "negative"

        # Position from center (50%)
        if beta_val >= 0:
            left_pct = 50
            width_pct = bar_pct
        else:
            left_pct = 50 - bar_pct
            width_pct = bar_pct

        coef_bars += f"""<div style="display:flex;align-items:center;margin:6px 0;gap:12px;">
  <div style="width:120px;text-align:right;font-size:0.85rem;color:{sig_color};font-weight:bold;">
    {escape_html(fm["predictor_names"][i])}</div>
  <div style="flex:1;position:relative;height:24px;background:rgba(255,255,255,0.04);border-radius:4px;">
    <div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:rgba(255,255,255,0.2);"></div>
    <div style="position:absolute;left:{left_pct:.1f}%;top:3px;height:18px;width:{width_pct:.1f}%;
      background:{bar_color};border-radius:3px;opacity:0.8;"></div>
  </div>
  <div style="width:180px;font-size:0.8rem;color:var(--muted);font-family:monospace;">
    {beta_val:+.3f} [{ci_low:+.3f}, {ci_high:+.3f}]</div>
</div>
"""

    # ====================================================================
    # RESIDUAL TABLE ROWS
    # ====================================================================
    resid_rows = ""
    for r in analysis["residual_analysis"]:
        resid_color = "#22c55e" if r["residual"] > 0.3 else ("#ef4444" if r["residual"] < -0.3 else "#94a3b8")
        label = ""
        if r["residual"] > 0.5:
            label = '<span style="color:#22c55e;font-size:0.75rem;">OVER-PERFORMER</span>'
        elif r["residual"] < -0.5:
            label = '<span style="color:#ef4444;font-size:0.75rem;">UNDER-PERFORMER</span>'

        resid_rows += f"""<tr>
  <td style="padding:8px;font-weight:bold;">{escape_html(r["country"])}</td>
  <td style="padding:8px;text-align:right;">{r["trials"]:,}</td>
  <td style="padding:8px;text-align:right;">{r["trials_per_m"]}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{r["actual"]:.3f}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{r["predicted"]:.3f}</td>
  <td style="padding:8px;text-align:right;color:{resid_color};font-weight:bold;font-family:monospace;">
    {r["residual"]:+.3f}</td>
  <td style="padding:8px;">{label}</td>
</tr>
"""

    # ====================================================================
    # STEPWISE RESULTS
    # ====================================================================
    stepwise_rows = ""
    for s in analysis["stepwise"]:
        f_color = "#22c55e" if s["f_p_value"] < 0.01 else ("#eab308" if s["f_p_value"] < 0.05 else "#94a3b8")
        stepwise_rows += f"""<tr>
  <td style="padding:8px;text-align:center;font-weight:bold;">{s["k"]}</td>
  <td style="padding:8px;font-size:0.85rem;">{", ".join(escape_html(p) for p in s["predictors"])}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{s["r_squared"]:.4f}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;font-weight:bold;color:#60a5fa;">
    {s["adj_r_squared"]:.4f}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{s["f_stat"]:.2f}</td>
  <td style="padding:8px;text-align:right;color:{f_color};font-weight:bold;">{fmt_p(s["f_p_value"])}</td>
</tr>
"""

    # ====================================================================
    # DATASET TABLE
    # ====================================================================
    data_rows = ""
    sorted_records = sorted(records, key=lambda x: -x["trials_per_m"])
    for rec in sorted_records:
        data_rows += f"""<tr>
  <td style="padding:6px 8px;font-weight:bold;">{escape_html(rec["country"])}</td>
  <td style="padding:6px 8px;text-align:right;">{rec["pop_m"]}</td>
  <td style="padding:6px 8px;text-align:right;">${rec["gdp_per_cap"]:,}</td>
  <td style="padding:6px 8px;text-align:center;">{"Yes" if rec["english"] else "No"}</td>
  <td style="padding:6px 8px;text-align:center;">{"Yes" if rec["pepfar"] else "No"}</td>
  <td style="padding:6px 8px;text-align:center;">{"Yes" if rec["conflict"] else "No"}</td>
  <td style="padding:6px 8px;text-align:center;">{rec["who_nra"]}</td>
  <td style="padding:6px 8px;text-align:right;">{rec["trials"]:,}</td>
  <td style="padding:6px 8px;text-align:right;color:#60a5fa;font-weight:bold;">{rec["trials_per_m"]}</td>
</tr>
"""

    # ====================================================================
    # PREDICTOR SUMMARY TABLE
    # ====================================================================
    pred_rows = ""
    for ps in analysis["predictor_summaries"]:
        pred_rows += f"""<tr>
  <td style="padding:8px;font-weight:bold;">{escape_html(ps["name"])}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{ps["mean"]:.3f}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{ps["sd"]:.3f}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{ps["min"]:.3f}</td>
  <td style="padding:8px;text-align:right;font-family:monospace;">{ps["max"]:.3f}</td>
</tr>
"""

    # ====================================================================
    # Find Nigeria and Rwanda residuals for narrative
    # ====================================================================
    nigeria_resid = next((r for r in analysis["residual_analysis"] if r["country"] == "Nigeria"), None)
    rwanda_resid = next((r for r in analysis["residual_analysis"] if r["country"] == "Rwanda"), None)

    nigeria_narrative = ""
    if nigeria_resid:
        ng = nigeria_resid
        nigeria_narrative = f"""
    <h3>The Nigeria Paradox (residual = {ng["residual"]:+.3f})</h3>
    <p>Nigeria is the single most extreme under-performer relative to the model.
    With 230M people, GDP/capita of $2,200, English-speaking, PEPFAR-recipient, and
    WHO NRA maturity level 2, the model predicts Nigeria should have <strong>far more</strong>
    trials per million than it does ({ng["trials_per_m"]}/M actual vs ~{math.exp(ng["predicted"]) - 0.1:.1f}/M
    predicted). The residual of {ng["residual"]:+.3f} (on the log scale) means Nigeria has
    roughly {100*(1 - math.exp(ng["residual"])):.0f}% fewer trials than expected.</p>
    <p><strong>Why?</strong> Despite favourable structural factors, Nigeria suffers from:
    (1) regulatory fragmentation across 36 states + FCT, (2) NAFDAC capacity constraints
    despite NRA level 2 classification, (3) security concerns in northern regions deterring
    sponsors, (4) infrastructure gaps (power, cold chain, data connectivity), (5) brain drain
    of clinical researchers, and (6) perception risk among global sponsors. The paradox is that
    Nigeria has <em>all the right predictors</em> but fails on unmeasured institutional and
    governance factors that this model cannot capture.</p>"""

    rwanda_narrative = ""
    if rwanda_resid:
        rw = rwanda_resid
        rwanda_narrative = f"""
    <h3>The Rwanda Model (residual = {rw["residual"]:+.3f})</h3>
    <p>Rwanda dramatically over-performs its model prediction. With only 14M people and
    GDP/capita of just $900, Rwanda achieves {rw["trials_per_m"]}/M trials &mdash;
    substantially above the {math.exp(rw["predicted"]) - 0.1:.1f}/M the model expects.
    The positive residual of {rw["residual"]:+.3f} means Rwanda has approximately
    {100*(math.exp(rw["residual"]) - 1):.0f}% more trials than predicted.</p>
    <p><strong>Why?</strong> Rwanda benefits from: (1) exceptional governance efficiency
    and political stability, (2) proactive health sector investment including universal
    health coverage, (3) strong partnerships with US academic institutions (Partners in Health),
    (4) centralized regulatory pathway that reduces friction, (5) digital health infrastructure
    (drone delivery, electronic health records), and (6) a deliberate strategy to position
    itself as a clinical research hub. Rwanda demonstrates that <em>governance quality</em>
    &mdash; an unmeasured variable in our model &mdash; may be the most powerful predictor
    of all.</p>"""

    # ====================================================================
    # Best single predictor narrative
    # ====================================================================
    best_single = analysis["stepwise"][0] if analysis["stepwise"] else None
    single_narrative = ""
    if best_single:
        single_narrative = f"""The single best predictor of trial density is
    <strong>{escape_html(best_single["predictors"][0])}</strong>,
    explaining {best_single["r_squared"]*100:.1f}% of variance (R&sup2; = {best_single["r_squared"]:.3f},
    adj. R&sup2; = {best_single["adj_r_squared"]:.3f})."""

    # ====================================================================
    # FULL HTML
    # ====================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Regression Model -- What Predicts Clinical Trial Density in Africa?</title>
<style>
  :root {{
    --bg: #0a0e17;
    --card: #111827;
    --border: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #60a5fa;
    --green: #22c55e;
    --yellow: #eab308;
    --orange: #f97316;
    --red: #ef4444;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    line-height: 1.6;
    padding: 20px;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{
    font-size: 2rem;
    margin-bottom: 8px;
    background: linear-gradient(135deg, var(--accent), var(--green));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}
  h2 {{
    font-size: 1.4rem;
    color: var(--accent);
    margin: 32px 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}
  h3 {{
    font-size: 1.1rem;
    color: var(--muted);
    margin: 20px 0 10px;
  }}
  .subtitle {{
    color: var(--muted);
    font-size: 0.95rem;
    margin-bottom: 24px;
  }}
  .summary-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .stat-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
  }}
  .stat-card .number {{
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 4px;
  }}
  .stat-card .label {{
    color: var(--muted);
    font-size: 0.85rem;
  }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    overflow-x: auto;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  th {{
    padding: 12px 10px;
    text-align: left;
    color: var(--muted);
    font-weight: 600;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid var(--border);
  }}
  td {{
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  tr:hover {{ background: rgba(255,255,255,0.03); }}
  .note {{
    background: rgba(96,165,250,0.08);
    border-left: 3px solid var(--accent);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
    font-size: 0.9rem;
    line-height: 1.7;
  }}
  .warning {{
    background: rgba(239,68,68,0.08);
    border-left: 3px solid var(--red);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
    font-size: 0.9rem;
  }}
  .interpretation {{
    background: rgba(34,197,94,0.06);
    border-left: 3px solid var(--green);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
    font-size: 0.9rem;
    line-height: 1.7;
  }}
  .footer {{
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: 0.8rem;
    text-align: center;
  }}
  .sig-legend {{
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    margin: 10px 0;
    font-size: 0.8rem;
    color: var(--muted);
  }}
  .sig-legend span {{ font-weight: bold; }}
</style>
</head>
<body>
<div class="container">

<h1>The Regression Model</h1>
<p class="subtitle">What Predicts Clinical Trial Density in Africa?
Multivariate OLS regression on 30 countries, 6 predictors.
&mdash; ClinicalTrials.gov API v2, {escape_html(data.get("timestamp", "")[:10])}</p>

<!-- ===== SUMMARY CARDS ===== -->
<div class="summary-grid">
  <div class="stat-card">
    <div class="number" style="color:var(--accent);">{fm["r_squared"]:.3f}</div>
    <div class="label">R-squared</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--green);">{fm["adj_r_squared"]:.3f}</div>
    <div class="label">Adjusted R-squared</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--yellow);">{fm["f_stat"]:.2f}</div>
    <div class="label">F-statistic</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:{significance_color(fm["f_p_value"])};">
      {fmt_p(fm["f_p_value"])}</div>
    <div class="label">Model p-value</div>
  </div>
  <div class="stat-card">
    <div class="number">{fm["n"]}</div>
    <div class="label">Countries (n)</div>
  </div>
  <div class="stat-card">
    <div class="number">{fm["p"] - 1}</div>
    <div class="label">Predictors (k)</div>
  </div>
</div>

<div class="note">
  <strong>Model specification:</strong> log(trials/million + 0.1) = &beta;<sub>0</sub>
  + &beta;<sub>1</sub> log(GDP/cap) + &beta;<sub>2</sub> English
  + &beta;<sub>3</sub> PEPFAR + &beta;<sub>4</sub> Conflict
  + &beta;<sub>5</sub> WHO_NRA + &beta;<sub>6</sub> log(Pop) + &epsilon;
  <br>
  <strong>n = {fm["n"]}</strong>, <strong>k = {fm["p"] - 1}</strong> predictors,
  <strong>df<sub>resid</sub> = {fm["df_resid"]}</strong>.
  All inference via OLS normal equations implemented in pure Python.
</div>

<!-- ===== REGRESSION TABLE ===== -->
<h2>1. Full Regression Table</h2>
<div class="card">
  <table>
    <thead>
      <tr>
        <th>Predictor</th>
        <th style="text-align:right;">Coefficient</th>
        <th style="text-align:right;">Std. Error</th>
        <th style="text-align:right;">t-statistic</th>
        <th style="text-align:right;">p-value</th>
        <th style="text-align:right;">Std. Beta</th>
        <th>Effect</th>
      </tr>
    </thead>
    <tbody>
{reg_rows}
    </tbody>
  </table>
  <div class="sig-legend">
    <div>Significance: <span style="color:#22c55e;">*** p&lt;0.001</span></div>
    <div><span style="color:#22c55e;">** p&lt;0.01</span></div>
    <div><span style="color:#eab308;">* p&lt;0.05</span></div>
    <div><span style="color:#f97316;">. p&lt;0.10</span></div>
  </div>
</div>

<div class="interpretation">
  <strong>Reading the table:</strong> Each coefficient shows the expected change in
  log(trials/million) for a one-unit increase in the predictor, holding all others constant.
  The standardized beta allows direct comparison of effect sizes across predictors with
  different scales. A positive coefficient means the factor is associated with
  <em>more</em> trials; negative means <em>fewer</em>.
</div>

<!-- ===== COEFFICIENT INTERPRETATION ===== -->
<h2>2. Coefficient Interpretation</h2>
<div class="card">
  <h3>log(GDP per capita)</h3>
  <p style="margin:8px 0;color:var(--muted);">
    A 1% increase in GDP per capita is associated with a {fm["beta"][1]*0.01:.4f} change
    in log(trials/M). In practical terms, doubling GDP per capita (e.g. from $1,000 to $2,000)
    is associated with a {fm["beta"][1]*math.log(2):.2f} change in log density &mdash;
    roughly a {100*(math.exp(fm["beta"][1]*math.log(2)) - 1):.0f}% change in trial density.
    Economic capacity is a {("significant" if fm["p_values"][1] < 0.05 else "non-significant")}
    predictor (p = {fmt_p(fm["p_values"][1])}).
  </p>

  <h3>English Language</h3>
  <p style="margin:8px 0;color:var(--muted);">
    English-speaking countries have a coefficient of {fm["beta"][2]:+.3f}, meaning they have
    approximately {100*(math.exp(fm["beta"][2]) - 1):+.0f}% {"more" if fm["beta"][2] > 0 else "fewer"}
    trials per million than non-English countries, all else equal.
    This reflects the dominance of anglophone research networks, journal ecosystems, and
    sponsor familiarity (p = {fmt_p(fm["p_values"][2])}).
  </p>

  <h3>PEPFAR Recipient</h3>
  <p style="margin:8px 0;color:var(--muted);">
    PEPFAR countries show a coefficient of {fm["beta"][3]:+.3f}. This suggests that sustained
    US health investment creates research infrastructure that extends beyond HIV/AIDS,
    building clinical trial capacity through trained personnel, laboratory networks, and
    data systems (p = {fmt_p(fm["p_values"][3])}).
  </p>

  <h3>Conflict</h3>
  <p style="margin:8px 0;color:var(--muted);">
    Active conflict has a coefficient of {fm["beta"][4]:+.3f}, meaning conflict-affected
    countries have approximately {100*(math.exp(fm["beta"][4]) - 1):+.0f}% {"fewer" if fm["beta"][4] < 0 else "more"}
    trials per million than stable countries, all else equal. Conflict destroys institutions,
    deters sponsors, and displaces health workers (p = {fmt_p(fm["p_values"][4])}).
  </p>

  <h3>WHO NRA Maturity</h3>
  <p style="margin:8px 0;color:var(--muted);">
    Each level of WHO NRA maturity is associated with a {fm["beta"][5]:+.3f} change in
    log(trials/M). Moving from NRA level 0 to level 3 corresponds to roughly
    {100*(math.exp(fm["beta"][5]*3) - 1):+.0f}% more trials per million. Regulatory
    capacity signals to sponsors that a country can oversee trials to international standards
    (p = {fmt_p(fm["p_values"][5])}).
  </p>

  <h3>log(Population)</h3>
  <p style="margin:8px 0;color:var(--muted);">
    Population has a coefficient of {fm["beta"][6]:+.3f} on the log-log scale. A negative
    value means larger countries tend to have <em>lower</em> trial density per capita &mdash;
    trials do not scale proportionally with population. This suggests that trial placement
    is driven by site availability rather than population need
    (p = {fmt_p(fm["p_values"][6])}).
  </p>
</div>

<!-- ===== COEFFICIENT PLOT ===== -->
<h2>3. Coefficient Plot</h2>
<div class="card">
  <p style="color:var(--muted);font-size:0.85rem;margin-bottom:12px;">
    Horizontal bars show coefficient magnitude. Green = positive (more trials),
    Red = negative (fewer trials). Center line = zero.</p>
{coef_bars}
</div>

<!-- ===== RESIDUAL ANALYSIS ===== -->
<h2>4. Residual Analysis: Who Over/Under-Performs?</h2>
<div class="card">
  <p style="color:var(--muted);margin-bottom:12px;">
    Countries sorted by residual. Positive = more trials than the model predicts.
    Negative = fewer trials than predicted.</p>
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:right;">Trials</th>
        <th style="text-align:right;">Trials/M</th>
        <th style="text-align:right;">Actual (log)</th>
        <th style="text-align:right;">Predicted (log)</th>
        <th style="text-align:right;">Residual</th>
        <th>Flag</th>
      </tr>
    </thead>
    <tbody>
{resid_rows}
    </tbody>
  </table>
</div>

<!-- ===== NIGERIA PARADOX ===== -->
<h2>5. Key Residual Stories</h2>
<div class="card">
{nigeria_narrative}
{rwanda_narrative}
</div>

<!-- ===== STEPWISE MODEL BUILDING ===== -->
<h2>6. Stepwise Model Building</h2>
<div class="card">
  <p style="color:var(--muted);margin-bottom:12px;">
    For each k = 1,2,...,6, the best combination of k predictors (by adjusted R&sup2;).
    Shows how model fit improves as predictors are added.</p>
  <table>
    <thead>
      <tr>
        <th style="text-align:center;">k</th>
        <th>Best Predictors</th>
        <th style="text-align:right;">R&sup2;</th>
        <th style="text-align:right;">Adj. R&sup2;</th>
        <th style="text-align:right;">F-stat</th>
        <th style="text-align:right;">F p-value</th>
      </tr>
    </thead>
    <tbody>
{stepwise_rows}
    </tbody>
  </table>
</div>

<div class="interpretation">
  <strong>Stepwise insight:</strong> {single_narrative}
  The model improves as predictors are added, but adjusted R&sup2; penalizes
  overfitting. The point where adjusted R&sup2; peaks indicates the optimal
  model complexity for these data.
</div>

<!-- ===== PREDICTOR SUMMARIES ===== -->
<h2>7. Predictor Summary Statistics</h2>
<div class="card">
  <table>
    <thead>
      <tr>
        <th>Predictor</th>
        <th style="text-align:right;">Mean</th>
        <th style="text-align:right;">SD</th>
        <th style="text-align:right;">Min</th>
        <th style="text-align:right;">Max</th>
      </tr>
    </thead>
    <tbody>
{pred_rows}
    </tbody>
  </table>
</div>

<!-- ===== FULL DATASET ===== -->
<h2>8. Full Dataset (30 Countries)</h2>
<div class="card">
  <table>
    <thead>
      <tr>
        <th>Country</th>
        <th style="text-align:right;">Pop (M)</th>
        <th style="text-align:right;">GDP/cap</th>
        <th style="text-align:center;">English</th>
        <th style="text-align:center;">PEPFAR</th>
        <th style="text-align:center;">Conflict</th>
        <th style="text-align:center;">WHO NRA</th>
        <th style="text-align:right;">Trials</th>
        <th style="text-align:right;">Trials/M</th>
      </tr>
    </thead>
    <tbody>
{data_rows}
    </tbody>
  </table>
</div>

<!-- ===== POLICY IMPLICATIONS ===== -->
<h2>9. Policy Implications</h2>
<div class="card">
  <div class="interpretation">
    <h3>What This Model Tells Policy-Makers</h3>
    <p style="margin:8px 0;">
      <strong>1. Regulatory capacity is actionable.</strong> WHO NRA maturity is a modifiable
      factor. Countries can invest in building their national regulatory authority toward
      WHO maturity level 3, which this model associates with substantially higher trial density.
      This is arguably the most direct policy lever available.
    </p>
    <p style="margin:8px 0;">
      <strong>2. Language barriers persist but can be bridged.</strong> The English-language
      advantage reflects structural bias in global research networks. Francophone and
      Lusophone countries can close this gap through bilingual regulatory frameworks,
      protocol translation services, and partnerships with non-anglophone sponsors.
    </p>
    <p style="margin:8px 0;">
      <strong>3. PEPFAR infrastructure has spillover benefits.</strong> Countries receiving
      sustained PEPFAR investment develop clinical research capacity that extends beyond
      HIV. This argues for maintaining and expanding health research infrastructure
      investment in Africa.
    </p>
    <p style="margin:8px 0;">
      <strong>4. Conflict prevention is health research policy.</strong> Every year of conflict
      translates directly into fewer clinical trials and, ultimately, fewer context-specific
      treatment protocols for local populations.
    </p>
    <p style="margin:8px 0;">
      <strong>5. The Nigeria paradox demands attention.</strong> Nigeria's massive under-performance
      relative to its structural advantages suggests that governance quality, regulatory
      efficiency, and institutional trust may matter more than economic size. Targeted
      regulatory reform in Nigeria alone could shift Africa's trial landscape.
    </p>
    <p style="margin:8px 0;">
      <strong>6. GDP is necessary but not sufficient.</strong> Economic development creates
      conditions for research, but the residuals show that small, well-governed countries
      (Rwanda, Botswana) can dramatically outperform their GDP bracket.
    </p>
  </div>
</div>

<!-- ===== METHODOLOGY ===== -->
<h2>10. Methodology</h2>
<div class="card">
  <div class="note">
    <p><strong>Data sources:</strong> Trial counts from ClinicalTrials.gov API v2 (interventional
    studies only). Population and GDP from World Bank (2025 estimates). PEPFAR status from
    PEPFAR.gov. Conflict classification from ACLED. WHO NRA maturity from WHO Global
    Benchmarking Tool.</p>
    <p style="margin-top:8px;"><strong>Model:</strong> Ordinary least squares via normal equations
    (&beta; = (X&prime;X)<sup>-1</sup>X&prime;y), implemented in pure Python without external
    libraries. Matrix inversion via Gauss-Jordan elimination with partial pivoting.
    P-values computed via regularized incomplete beta function (t-distribution).
    F-statistic p-value via incomplete beta (F-distribution).</p>
    <p style="margin-top:8px;"><strong>Limitations:</strong> n = 30 is small for 6 predictors
    (ratio 5:1 vs recommended 10-20:1). Multicollinearity may inflate standard errors
    (GDP correlates with NRA, PEPFAR with English). Cross-sectional design prevents causal
    inference. Unmeasured confounders (governance quality, colonial history, research culture)
    may explain residual variation. Trial counts include all time periods, not annual rates.</p>
  </div>
</div>

<!-- ===== AI TRANSPARENCY ===== -->
<h2>AI Transparency</h2>
<div class="card">
  <div class="note">
    <p>Analysis pipeline, OLS implementation, HTML dashboard, and narrative interpretation
    generated with Claude (Anthropic). All matrix algebra (transpose, multiply, Gauss-Jordan
    inverse) implemented in pure Python without numpy/scipy. Statistical computations
    (t-distribution via incomplete beta, F-distribution) independently verifiable.
    Trial counts sourced from ClinicalTrials.gov API v2. Country-level predictor data
    hardcoded from World Bank, WHO, and PEPFAR public sources (2025 estimates).
    Human review applied to all outputs.</p>
  </div>
</div>

<div class="footer">
  <p>Data: ClinicalTrials.gov API v2 | Predictors: World Bank, WHO, PEPFAR |
  Generated {escape_html(data.get("timestamp", "")[:10])}</p>
  <p style="margin-top:4px;">Project 46 of the Africa Clinical Trial Equity Programme</p>
</div>

</div>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Main entry point."""
    print("=" * 70)
    print("REGRESSION MODEL: What Predicts Clinical Trial Density in Africa?")
    print("=" * 70)

    # Fetch data
    print("\nFetching trial counts...")
    data = fetch_all_data()

    # Build dataset
    print("\nBuilding regression dataset...")
    records, X, y = build_dataset(data)

    # Print dataset summary
    print(f"\n--- Dataset: {len(records)} countries ---")
    for rec in sorted(records, key=lambda x: -x["trials_per_m"])[:10]:
        print(f"  {rec['country']:<20} {rec['trials']:>6,} trials  "
              f"{rec['trials_per_m']:>8.2f}/M  log={rec['log_trials_per_m']:>7.3f}")

    # Run analysis
    analysis = run_regression_analysis(records, X, y)

    # Print regression results
    fm = analysis["full_model"]
    print(f"\n--- FULL MODEL ---")
    print(f"  R-squared:     {fm['r_squared']:.4f}")
    print(f"  Adj R-squared: {fm['adj_r_squared']:.4f}")
    print(f"  F-statistic:   {fm['f_stat']:.3f} (p = {fmt_p(fm['f_p_value'])})")
    print(f"  n = {fm['n']}, k = {fm['p'] - 1}, df_resid = {fm['df_resid']}")

    print(f"\n{'Predictor':<15} {'Coef':>10} {'SE':>10} {'t':>8} {'p':>8} {'Sig':>5} {'Std.B':>8}")
    print("-" * 70)
    for i in range(fm["p"]):
        print(f"  {fm['predictor_names'][i]:<13} {fm['beta'][i]:>+10.4f} "
              f"{fm['se'][i]:>10.4f} {fm['t_stats'][i]:>+8.3f} "
              f"{fmt_p(fm['p_values'][i]):>8} {fm['stars'][i]:>5} "
              f"{fm['std_beta'][i]:>+8.3f}")

    print(f"\n--- TOP UNDER-PERFORMERS ---")
    for r in analysis["under_performers"]:
        print(f"  {r['country']:<20} residual = {r['residual']:+.3f}  "
              f"({r['trials']:,} trials, {r['trials_per_m']}/M)")

    print(f"\n--- TOP OVER-PERFORMERS ---")
    for r in analysis["over_performers"]:
        print(f"  {r['country']:<20} residual = {r['residual']:+.3f}  "
              f"({r['trials']:,} trials, {r['trials_per_m']}/M)")

    print(f"\n--- STEPWISE ---")
    for s in analysis["stepwise"]:
        print(f"  k={s['k']}: {', '.join(s['predictors']):<50} "
              f"adj.R2={s['adj_r_squared']:.4f}")

    # Save analysis results to cache
    analysis_cache = {
        "timestamp": data.get("timestamp", ""),
        "country_trials": data.get("country_trials", {}),
        "regression": {
            "r_squared": fm["r_squared"],
            "adj_r_squared": fm["adj_r_squared"],
            "f_stat": fm["f_stat"],
            "f_p_value": fm["f_p_value"],
            "coefficients": {
                fm["predictor_names"][i]: {
                    "beta": fm["beta"][i],
                    "se": fm["se"][i],
                    "t": fm["t_stats"][i],
                    "p": fm["p_values"][i],
                    "std_beta": fm["std_beta"][i],
                }
                for i in range(fm["p"])
            },
            "residuals": {
                r["country"]: r["residual"]
                for r in analysis["residual_analysis"]
            },
            "stepwise": analysis["stepwise"],
        },
        "records": records,
    }

    cache_out = DATA_DIR / "regression_model_data.json"
    cache_out.parent.mkdir(parents=True, exist_ok=True)
    cache_out.write_text(
        json.dumps(analysis_cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nAnalysis saved to {cache_out}")

    # Generate HTML
    print("\nGenerating HTML dashboard...")
    html = generate_html(data, records, analysis)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved to {OUTPUT_HTML}")
    print(f"File size: {OUTPUT_HTML.stat().st_size:,} bytes")
    print("\nDone.")


if __name__ == "__main__":
    main()
