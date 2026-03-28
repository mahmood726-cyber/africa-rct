#!/usr/bin/env python
"""
fetch_power_law.py -- Project 39: The Power Law of Research
============================================================
Fits a power law to the clinical trial count distribution across African
countries. Computes Gini coefficient, Herfindahl-Hirschman Index (HHI),
and compares concentration metrics with Latin America and Asia. Identifies
the rank tipping point below which countries have <1 trial/million.

Inspired by the Matthew Effect (Merton, 1968): research attracts more
research through network effects, reputation, and infrastructure snowball.

Usage:
    python fetch_power_law.py

Outputs:
    data/power_law_data.json   (cached API results, 24h TTL)
    power-law.html             (dark-theme interactive dashboard)

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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "power_law_data.json"
OUTPUT_HTML = Path(__file__).resolve().parent / "power-law.html"
RATE_LIMIT = 0.5  # seconds between API calls
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24

# -- African countries: name -> population in millions (2025 est.) ----------

AFRICAN_COUNTRIES = {
    "South Africa":             62,
    "Egypt":                    110,
    "Kenya":                    56,
    "Uganda":                   48,
    "Nigeria":                  230,
    "Tanzania":                 67,
    "Ethiopia":                 130,
    "Ghana":                    34,
    "Cameroon":                 27,
    "Mozambique":               33,
    "Malawi":                   21,
    "Zambia":                   21,
    "Zimbabwe":                 15,
    "Senegal":                  17,
    "Rwanda":                   14,
    "Democratic Republic of Congo": 102,
    "Burkina Faso":             23,
    "Mali":                     23,
    "Niger":                    27,
    "Chad":                     18,
    "Somalia":                  18,
    "South Sudan":              11,
    "Sudan":                    48,
    "Benin":                    13,
    "Togo":                     9,
    "Guinea":                   14,
    "Madagascar":               30,
    "Central African Republic": 5.5,
    "Botswana":                 2.5,
    "Namibia":                  2.7,
}

DISPLAY_NAMES = {
    "Democratic Republic of Congo": "DRC",
    "Central African Republic": "CAR",
}

# -- Latin America comparators: name -> population in millions ---------------

LATAM_COUNTRIES = {
    "Brazil":      216,
    "Mexico":      130,
    "Argentina":   46,
    "Colombia":    52,
    "Chile":       19.5,
    "Peru":        34,
    "Ecuador":     18,
    "Guatemala":   18,
    "Cuba":        11,
    "Bolivia":     12,
    "Honduras":    10.5,
    "Paraguay":    7.5,
    "Uruguay":     3.5,
    "Costa Rica":  5.2,
    "Panama":      4.5,
}

# -- Asia comparators: name -> population in millions -------------------------

ASIA_COUNTRIES = {
    "China":       1410,
    "India":       1430,
    "Japan":       124,
    "South Korea": 52,
    "Thailand":    72,
    "Indonesia":   280,
    "Philippines": 117,
    "Vietnam":     100,
    "Malaysia":    34,
    "Bangladesh":  175,
    "Pakistan":    240,
    "Sri Lanka":   22,
    "Nepal":       30,
    "Myanmar":     55,
    "Cambodia":    17,
}

# -- Income Gini coefficients (World Bank, approximate 2023) -----------------

INCOME_GINI = {
    "Africa":       0.44,
    "Latin America": 0.46,
    "Asia":         0.38,
    "Europe":       0.31,
}


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
    """Fetch trial counts for all countries (African + LatAm + Asia)."""
    cached = load_cache()
    if cached is not None:
        return cached

    data = {
        "timestamp": datetime.now().isoformat(),
        "african_counts": {},
        "latam_counts": {},
        "asia_counts": {},
    }

    all_regions = [
        ("African", AFRICAN_COUNTRIES, "african_counts"),
        ("Latin American", LATAM_COUNTRIES, "latam_counts"),
        ("Asian", ASIA_COUNTRIES, "asia_counts"),
    ]

    total_calls = sum(len(d) for _, d, _ in all_regions)
    call_num = 0

    for label, countries, key in all_regions:
        print(f"\n--- Querying {label} countries ---")
        for country in countries:
            call_num += 1
            dname = DISPLAY_NAMES.get(country, country)
            print(f"  [{call_num}/{total_calls}] {dname}...")
            count = get_trial_count(country)
            data[key][country] = count
            print(f"    -> {count:,} trials")
            time.sleep(RATE_LIMIT)

    save_cache(data)
    return data


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def compute_gini(values):
    """Compute Gini coefficient for a list of non-negative values."""
    if not values or all(v == 0 for v in values):
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    cumsum = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(sorted_vals):
        cumsum += v
        weighted_sum += (2 * (i + 1) - n - 1) * v
    return weighted_sum / (n * total)


def compute_hhi(values):
    """Compute Herfindahl-Hirschman Index (sum of squared market shares)."""
    total = sum(values)
    if total == 0:
        return 0.0
    shares = [v / total for v in values]
    return sum(s * s for s in shares)


def fit_power_law(ranked_counts):
    """Fit log(count) = alpha - beta * log(rank) via OLS on log-log.

    Returns (alpha, beta, r_squared) where beta is the power-law exponent.
    Only uses entries with count > 0.
    """
    points = [(i + 1, c) for i, c in enumerate(ranked_counts) if c > 0]
    if len(points) < 3:
        return None, None, None

    n = len(points)
    log_ranks = [math.log(r) for r, _ in points]
    log_counts = [math.log(c) for _, c in points]

    sum_x = sum(log_ranks)
    sum_y = sum(log_counts)
    sum_xy = sum(log_ranks[i] * log_counts[i] for i in range(n))
    sum_x2 = sum(x * x for x in log_ranks)

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-12:
        return None, None, None

    beta = (n * sum_xy - sum_x * sum_y) / denom
    alpha = (sum_y - beta * sum_x) / n

    # R-squared
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in log_counts)
    ss_res = sum((log_counts[i] - alpha - beta * log_ranks[i]) ** 2 for i in range(n))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # beta is negative for power-law decay; exponent magnitude = -beta
    return round(alpha, 4), round(beta, 4), round(r_squared, 4)


def compute_shannon_entropy(values):
    """Shannon entropy of distribution (in bits)."""
    total = sum(values)
    if total == 0:
        return 0.0
    entropy = 0.0
    for v in values:
        if v > 0:
            p = v / total
            entropy -= p * math.log2(p)
    return entropy


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_region(counts_dict, pop_dict, region_name):
    """Analyze a single region's trial distribution."""
    entries = []
    for country, pop in pop_dict.items():
        trials = counts_dict.get(country, 0)
        per_m = round(trials / pop, 2) if pop > 0 else 0
        dname = DISPLAY_NAMES.get(country, country)
        entries.append({
            "country": country,
            "display_name": dname,
            "population_m": pop,
            "trials": trials,
            "per_million": per_m,
        })

    # Sort by trials descending for rank-frequency
    entries.sort(key=lambda x: -x["trials"])
    for i, e in enumerate(entries):
        e["rank"] = i + 1

    trial_counts = [e["trials"] for e in entries]
    total_trials = sum(trial_counts)
    total_pop = sum(e["population_m"] for e in entries)

    gini = round(compute_gini(trial_counts), 4)
    hhi = round(compute_hhi(trial_counts), 4)
    alpha, beta, r2 = fit_power_law(trial_counts)
    entropy = round(compute_shannon_entropy(trial_counts), 3)
    max_entropy = round(math.log2(len(entries)), 3) if len(entries) > 0 else 0
    evenness = round(entropy / max_entropy, 3) if max_entropy > 0 else 0

    # Tipping point: rank at which per_million drops below 1
    tipping_rank = None
    for e in entries:
        if e["per_million"] < 1.0 and tipping_rank is None:
            tipping_rank = e["rank"]

    return {
        "region": region_name,
        "entries": entries,
        "total_trials": total_trials,
        "total_pop": total_pop,
        "avg_per_million": round(total_trials / total_pop, 2) if total_pop > 0 else 0,
        "gini": gini,
        "hhi": hhi,
        "hhi_normalized": round(hhi * len(entries), 4) if entries else 0,
        "power_law_alpha": alpha,
        "power_law_beta": beta,
        "power_law_r2": r2,
        "entropy": entropy,
        "max_entropy": max_entropy,
        "evenness": evenness,
        "tipping_rank": tipping_rank,
        "n_countries": len(entries),
    }


def analyze_data(data):
    """Compute all analyses across three regions."""
    results = {}

    # -- Africa --
    africa = analyze_region(data["african_counts"], AFRICAN_COUNTRIES, "Africa")
    results["africa"] = africa

    # -- Latin America --
    latam = analyze_region(data["latam_counts"], LATAM_COUNTRIES, "Latin America")
    results["latam"] = latam

    # -- Asia --
    asia = analyze_region(data["asia_counts"], ASIA_COUNTRIES, "Asia")
    results["asia"] = asia

    # -- Cross-region comparison --
    results["comparison"] = {
        "trial_gini": {
            "Africa": africa["gini"],
            "Latin America": latam["gini"],
            "Asia": asia["gini"],
        },
        "income_gini": INCOME_GINI,
        "hhi": {
            "Africa": africa["hhi"],
            "Latin America": latam["hhi"],
            "Asia": asia["hhi"],
        },
        "power_law_exponent": {
            "Africa": abs(africa["power_law_beta"]) if africa["power_law_beta"] is not None else None,
            "Latin America": abs(latam["power_law_beta"]) if latam["power_law_beta"] is not None else None,
            "Asia": abs(asia["power_law_beta"]) if asia["power_law_beta"] is not None else None,
        },
        "evenness": {
            "Africa": africa["evenness"],
            "Latin America": latam["evenness"],
            "Asia": asia["evenness"],
        },
    }

    # -- Matthew Effect quantification for Africa --
    # Top 2 countries' share of total trials
    af_entries = africa["entries"]
    if len(af_entries) >= 2:
        top2_trials = af_entries[0]["trials"] + af_entries[1]["trials"]
        top2_share = round(top2_trials / africa["total_trials"] * 100, 1) if africa["total_trials"] > 0 else 0
        results["matthew_effect"] = {
            "top2_countries": [af_entries[0]["display_name"], af_entries[1]["display_name"]],
            "top2_trials": top2_trials,
            "top2_share_pct": top2_share,
            "bottom_half_trials": sum(e["trials"] for e in af_entries[len(af_entries) // 2:]),
            "bottom_half_share_pct": round(
                sum(e["trials"] for e in af_entries[len(af_entries) // 2:]) /
                africa["total_trials"] * 100, 1
            ) if africa["total_trials"] > 0 else 0,
        }
    else:
        results["matthew_effect"] = None

    # -- Countries below 1 trial/million --
    results["below_threshold"] = [
        {
            "country": e["display_name"],
            "trials": e["trials"],
            "population_m": e["population_m"],
            "per_million": e["per_million"],
            "rank": e["rank"],
        }
        for e in af_entries if e["per_million"] < 1.0
    ]

    # -- Lorenz curve data for Africa --
    if af_entries:
        sorted_by_trials = sorted(af_entries, key=lambda x: x["trials"])
        cumulative_countries = []
        cumulative_trials = []
        cum_t = 0
        for i, e in enumerate(sorted_by_trials):
            cum_t += e["trials"]
            cumulative_countries.append(round((i + 1) / len(sorted_by_trials) * 100, 1))
            cumulative_trials.append(round(cum_t / africa["total_trials"] * 100, 1) if africa["total_trials"] > 0 else 0)
        results["lorenz"] = {
            "cumulative_countries_pct": cumulative_countries,
            "cumulative_trials_pct": cumulative_trials,
        }
    else:
        results["lorenz"] = None

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


def gini_color(gini):
    """Return color based on Gini value."""
    if gini >= 0.7:
        return "#ef4444"
    elif gini >= 0.5:
        return "#f97316"
    elif gini >= 0.3:
        return "#eab308"
    else:
        return "#22c55e"


def hhi_interpret(hhi, n):
    """Interpret HHI value."""
    # Normalize: HHI ranges from 1/n (equal) to 1 (monopoly)
    if n <= 0:
        return "N/A"
    min_hhi = 1.0 / n
    if hhi > 0.25:
        return "Highly concentrated"
    elif hhi > 0.15:
        return "Moderately concentrated"
    else:
        return "Unconcentrated"


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

def generate_html(data, results):
    """Generate the full HTML dashboard."""

    africa = results["africa"]
    latam = results["latam"]
    asia = results["asia"]
    comparison = results["comparison"]
    matthew = results["matthew_effect"]
    below = results["below_threshold"]

    # ====================================================================
    # RANK-FREQUENCY TABLE (Africa)
    # ====================================================================
    rank_rows = ""
    max_trials = africa["entries"][0]["trials"] if africa["entries"] else 1
    if max_trials < 1:
        max_trials = 1
    for e in africa["entries"]:
        bar_width = min(e["trials"] / max_trials * 100, 100)
        pm_color = "#22c55e" if e["per_million"] >= 3 else ("#eab308" if e["per_million"] >= 1 else "#ef4444")
        rank_rows += f"""<tr>
  <td style="padding:8px;text-align:center;font-weight:bold;">{e["rank"]}</td>
  <td style="padding:8px;font-weight:bold;">{escape_html(e["display_name"])}</td>
  <td style="padding:8px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:8px;text-align:right;">{e["population_m"]}M</td>
  <td style="padding:8px;text-align:right;color:{pm_color};font-weight:bold;">{e["per_million"]}</td>
  <td style="padding:8px;">
    <div style="background:rgba(255,255,255,0.08);border-radius:4px;height:16px;width:100%;position:relative;">
      <div style="background:var(--accent);height:100%;width:{bar_width:.1f}%;border-radius:4px;"></div>
    </div></td>
</tr>
"""

    # ====================================================================
    # COMPARISON TABLE (Gini / HHI / Power Law)
    # ====================================================================
    comp_rows = ""
    for region in ["Africa", "Latin America", "Asia"]:
        tg = comparison["trial_gini"].get(region, 0)
        ig = INCOME_GINI.get(region, 0)
        hhi_val = comparison["hhi"].get(region, 0)
        ple = comparison["power_law_exponent"].get(region)
        eve = comparison["evenness"].get(region, 0)
        tg_c = gini_color(tg)
        ig_c = gini_color(ig)
        ple_str = f"{ple:.2f}" if ple is not None else "N/A"
        comp_rows += f"""<tr>
  <td style="padding:10px;font-weight:bold;">{escape_html(region)}</td>
  <td style="padding:10px;text-align:right;color:{tg_c};font-weight:bold;">{tg:.3f}</td>
  <td style="padding:10px;text-align:right;color:{ig_c};font-weight:bold;">{ig:.2f}</td>
  <td style="padding:10px;text-align:right;">{hhi_val:.4f}</td>
  <td style="padding:10px;text-align:right;">{ple_str}</td>
  <td style="padding:10px;text-align:right;">{eve:.3f}</td>
</tr>
"""

    # ====================================================================
    # BELOW THRESHOLD TABLE
    # ====================================================================
    below_rows = ""
    for e in below:
        below_rows += f"""<tr style="background:rgba(239,68,68,0.06);">
  <td style="padding:8px;color:#ef4444;font-weight:bold;">{escape_html(e["country"])}</td>
  <td style="padding:8px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:8px;text-align:right;">{e["population_m"]}M</td>
  <td style="padding:8px;text-align:right;color:#ef4444;font-weight:bold;">{e["per_million"]}</td>
</tr>
"""

    # ====================================================================
    # LATAM RANK TABLE
    # ====================================================================
    latam_rows = ""
    for e in latam["entries"]:
        latam_rows += f"""<tr>
  <td style="padding:8px;text-align:center;">{e["rank"]}</td>
  <td style="padding:8px;font-weight:bold;">{escape_html(e["display_name"])}</td>
  <td style="padding:8px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:8px;text-align:right;">{e["per_million"]}</td>
</tr>
"""

    # ====================================================================
    # ASIA RANK TABLE
    # ====================================================================
    asia_rows = ""
    for e in asia["entries"]:
        asia_rows += f"""<tr>
  <td style="padding:8px;text-align:center;">{e["rank"]}</td>
  <td style="padding:8px;font-weight:bold;">{escape_html(e["display_name"])}</td>
  <td style="padding:8px;text-align:right;">{e["trials"]:,}</td>
  <td style="padding:8px;text-align:right;">{e["per_million"]}</td>
</tr>
"""

    # ====================================================================
    # LORENZ CURVE DATA (for conceptual display)
    # ====================================================================
    lorenz = results.get("lorenz")
    lorenz_text = ""
    if lorenz:
        # Find where 50% of countries contribute (cumulative)
        half_idx = len(lorenz["cumulative_countries_pct"]) // 2
        trials_at_half = lorenz["cumulative_trials_pct"][half_idx] if half_idx < len(lorenz["cumulative_trials_pct"]) else 0
        lorenz_text = f"The bottom 50% of African countries by trial count contribute only {trials_at_half}% of all trials."

    # Power law fit details
    af_alpha = africa["power_law_alpha"]
    af_beta = africa["power_law_beta"]
    af_r2 = africa["power_law_r2"]
    pl_text = ""
    if af_beta is not None:
        exponent = abs(af_beta)
        pl_text = (
            f"Power-law fit: log(trials) = {af_alpha} + ({af_beta}) x log(rank), "
            f"R-squared = {af_r2}. Exponent magnitude = {exponent:.2f}."
        )
        if exponent > 1.5:
            pl_text += " This steep exponent indicates extreme concentration -- a classic Zipf-like distribution."
        elif exponent > 1.0:
            pl_text += " This moderate exponent indicates significant but not extreme concentration."
        else:
            pl_text += " This shallow exponent indicates a relatively gentle decline from top to bottom."

    # Matthew effect summary
    matthew_text = ""
    if matthew:
        matthew_text = (
            f"{matthew['top2_countries'][0]} and {matthew['top2_countries'][1]} together account for "
            f"{matthew['top2_share_pct']}% of all African trials ({matthew['top2_trials']:,} trials). "
            f"Meanwhile, the bottom half of countries contribute just {matthew['bottom_half_share_pct']}%."
        )

    # Tipping point
    tipping = africa.get("tipping_rank")
    tipping_text = ""
    if tipping is not None:
        tipping_text = (
            f"The tipping point occurs at rank {tipping}: countries ranked {tipping} and below "
            f"have fewer than 1 trial per million population. That leaves "
            f"{africa['n_countries'] - tipping + 1} of {africa['n_countries']} countries "
            f"in the sub-threshold zone."
        )

    # Africa Gini interpretation
    af_gini = africa["gini"]
    gini_text = f"Africa's trial Gini = {af_gini:.3f}"
    if af_gini > INCOME_GINI.get("Africa", 0):
        gini_text += (
            f", which exceeds even Africa's notoriously high income Gini ({INCOME_GINI['Africa']:.2f}). "
            f"Research is distributed more unequally than wealth itself."
        )
    else:
        gini_text += (
            f", which is comparable to Africa's income Gini ({INCOME_GINI['Africa']:.2f}). "
            f"Research inequality mirrors economic inequality."
        )

    # ====================================================================
    # FULL HTML
    # ====================================================================
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Power Law of Research -- Why the Rich Get Richer</title>
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
    --purple: #a78bfa;
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
    background: linear-gradient(135deg, var(--purple), var(--accent));
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
  .insight {{
    background: rgba(167,139,250,0.08);
    border-left: 4px solid var(--purple);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
    font-size: 0.95rem;
    line-height: 1.7;
  }}
  .insight strong {{ color: var(--purple); }}
  .warning {{
    background: rgba(239,68,68,0.08);
    border-left: 4px solid var(--red);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
  }}
  .warning strong {{ color: var(--red); }}
  .policy {{
    background: rgba(34,197,94,0.08);
    border-left: 4px solid var(--green);
    padding: 16px 20px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
  }}
  .policy strong {{ color: var(--green); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  th {{
    text-align: left;
    padding: 12px 8px;
    border-bottom: 2px solid var(--border);
    color: var(--muted);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  td {{
    padding: 8px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}
  .method {{
    color: var(--muted);
    font-size: 0.82rem;
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    line-height: 1.7;
  }}
  .formula {{
    background: rgba(96,165,250,0.1);
    border: 1px solid rgba(96,165,250,0.2);
    border-radius: 8px;
    padding: 16px;
    margin: 12px 0;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 0.88rem;
    color: var(--accent);
    line-height: 1.8;
  }}
  .footer {{
    text-align: center;
    color: var(--muted);
    font-size: 0.8rem;
    margin-top: 48px;
    padding: 24px;
    border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>
<div class="container">

<h1>The Power Law of Research</h1>
<p class="subtitle">
  Why the Rich Get Richer: Zipf/Pareto Distribution of Clinical Trials Across Africa
  -- Data from ClinicalTrials.gov API v2, {escape_html(data.get("timestamp", "")[:10])}
</p>

<!-- ============================================================ -->
<!-- SUMMARY CARDS -->
<!-- ============================================================ -->
<div class="summary-grid">
  <div class="stat-card">
    <div class="number" style="color:var(--purple);">{af_gini:.3f}</div>
    <div class="label">Trial Gini Coefficient<br>(0=equal, 1=monopoly)</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--accent);">{africa["hhi"]:.4f}</div>
    <div class="label">HHI Concentration<br>({hhi_interpret(africa["hhi"], africa["n_countries"])})</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--orange);">{abs(af_beta) if af_beta is not None else "N/A"}</div>
    <div class="label">Power-Law Exponent<br>(Zipf slope magnitude)</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--red);">{africa["n_countries"] - (tipping - 1 if tipping else 0)}/{africa["n_countries"]}</div>
    <div class="label">Countries Below<br>1 Trial/Million</div>
  </div>
  <div class="stat-card">
    <div class="number" style="color:var(--green);">{africa["total_trials"]:,}</div>
    <div class="label">Total African<br>Interventional Trials</div>
  </div>
</div>

<!-- ============================================================ -->
<!-- THE MATTHEW EFFECT -->
<!-- ============================================================ -->
<h2>The Matthew Effect in African Research</h2>
<div class="card">
  <p style="font-size:1.05rem;line-height:1.7;margin-bottom:16px;">
    <em>"For to everyone who has, more will be given, and he will have abundance;
    but from him who does not have, even what he has will be taken away."</em>
    -- Matthew 25:29
  </p>
  <p style="line-height:1.7;margin-bottom:16px;">
    Sociologist Robert K. Merton (1968) coined the <strong>Matthew Effect</strong> to describe
    how eminent scientists get disproportionately more credit for their contributions, while
    unknown scientists get disproportionately less. The same mechanism operates in research
    geography: countries with existing infrastructure attract more trials, which builds more
    infrastructure, which attracts more trials -- a self-reinforcing feedback loop.
  </p>
  <div class="insight">
    <strong>The African Matthew Effect:</strong> {escape_html(matthew_text)}
  </div>
  <p style="line-height:1.7;margin-top:12px;">
    This is the signature of a <strong>power-law distribution</strong> (also called Zipf's law
    or Pareto distribution). In physics and complexity science, power laws emerge from
    <strong>preferential attachment</strong>: new connections (trials) are more likely to attach
    to already well-connected nodes (hubs). The rich get richer not through conspiracy but
    through the natural dynamics of networked systems.
  </p>
</div>

<!-- ============================================================ -->
<!-- POWER LAW FIT -->
<!-- ============================================================ -->
<h2>Power-Law Fit: Log-Log Analysis</h2>
<div class="card">
  <p style="line-height:1.7;margin-bottom:16px;">
    A true power law appears as a straight line on a log-log plot. We fit
    <code>log(trials) = alpha + beta x log(rank)</code> via ordinary least squares
    on the rank-frequency distribution of trial counts.
  </p>
  <div class="formula">
    {escape_html(pl_text)}
  </div>
  <p style="line-height:1.7;margin-top:12px;">
    An R-squared close to 1 confirms the power-law hypothesis. For comparison,
    city populations (Zipf's law) typically have exponents near 1.0; scientific
    citation distributions have exponents of 1.5-2.0.
  </p>
</div>

<!-- ============================================================ -->
<!-- RANK-FREQUENCY TABLE -->
<!-- ============================================================ -->
<h2>Rank-Frequency Table: 30 African Countries</h2>
<div class="card">
  <table>
    <thead>
      <tr>
        <th>Rank</th><th>Country</th><th>Trials</th>
        <th>Population</th><th>Per Million</th><th>Bar</th>
      </tr>
    </thead>
    <tbody>
{rank_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- GINI COEFFICIENT -->
<!-- ============================================================ -->
<h2>Gini Coefficient: Research vs Income Inequality</h2>
<div class="card">
  <p style="line-height:1.7;margin-bottom:16px;">
    The Gini coefficient measures inequality on a 0-1 scale. A Gini of 0 means perfect
    equality (every country has identical trial counts); 1 means total monopoly (one country
    has all trials). We compare Africa's <strong>trial Gini</strong> with the continent's
    <strong>income Gini</strong> and with trial Ginis of Latin America and Asia.
  </p>
  <div class="insight">
    <strong>Key finding:</strong> {escape_html(gini_text)}
  </div>
  <table style="margin-top:20px;">
    <thead>
      <tr>
        <th>Region</th><th style="text-align:right;">Trial Gini</th>
        <th style="text-align:right;">Income Gini</th>
        <th style="text-align:right;">HHI</th>
        <th style="text-align:right;">Power-Law Exponent</th>
        <th style="text-align:right;">Evenness</th>
      </tr>
    </thead>
    <tbody>
{comp_rows}
    </tbody>
  </table>
  <p class="method" style="margin-top:16px;">
    <strong>Evenness</strong> = Shannon entropy / max entropy. Values near 1 indicate equal distribution;
    near 0 indicates extreme concentration. HHI (Herfindahl-Hirschman Index) is the sum of squared
    market shares -- values above 0.25 indicate high concentration in antitrust economics.
  </p>
</div>

<!-- ============================================================ -->
<!-- LORENZ CURVE -->
<!-- ============================================================ -->
<h2>Lorenz Curve: Visualizing Concentration</h2>
<div class="card">
  <p style="line-height:1.7;margin-bottom:16px;">
    The Lorenz curve plots the cumulative share of trials (y-axis) against the cumulative
    share of countries ranked from fewest to most trials (x-axis). Perfect equality is
    the 45-degree diagonal. The further the curve bows below the diagonal, the greater
    the inequality. The Gini coefficient equals twice the area between the curve and the diagonal.
  </p>
  <div class="warning">
    <strong>Concentration:</strong> {escape_html(lorenz_text)}
  </div>
</div>

<!-- ============================================================ -->
<!-- TIPPING POINT -->
<!-- ============================================================ -->
<h2>The Tipping Point: Below 1 Trial per Million</h2>
<div class="card">
  <div class="warning">
    <strong>Threshold breach:</strong> {escape_html(tipping_text)}
  </div>
  <table style="margin-top:20px;">
    <thead>
      <tr>
        <th>Country</th><th style="text-align:right;">Trials</th>
        <th style="text-align:right;">Population</th>
        <th style="text-align:right;">Per Million</th>
      </tr>
    </thead>
    <tbody>
{below_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- COMPARATOR REGIONS -->
<!-- ============================================================ -->
<h2>Regional Comparators: Latin America</h2>
<div class="card">
  <p style="margin-bottom:12px;color:var(--muted);">
    Gini: {latam["gini"]:.3f} | HHI: {latam["hhi"]:.4f} | Total: {latam["total_trials"]:,} trials across {latam["n_countries"]} countries
  </p>
  <table>
    <thead>
      <tr><th>Rank</th><th>Country</th><th style="text-align:right;">Trials</th><th style="text-align:right;">Per Million</th></tr>
    </thead>
    <tbody>
{latam_rows}
    </tbody>
  </table>
</div>

<h2>Regional Comparators: Asia</h2>
<div class="card">
  <p style="margin-bottom:12px;color:var(--muted);">
    Gini: {asia["gini"]:.3f} | HHI: {asia["hhi"]:.4f} | Total: {asia["total_trials"]:,} trials across {asia["n_countries"]} countries
  </p>
  <table>
    <thead>
      <tr><th>Rank</th><th>Country</th><th style="text-align:right;">Trials</th><th style="text-align:right;">Per Million</th></tr>
    </thead>
    <tbody>
{asia_rows}
    </tbody>
  </table>
</div>

<!-- ============================================================ -->
<!-- POLICY IMPLICATIONS -->
<!-- ============================================================ -->
<h2>Policy Implications: Invest at the Elbow</h2>
<div class="card">
  <div class="policy">
    <strong>The strategic insight:</strong> Power-law distributions have a characteristic "elbow" --
    the inflection point where the curve transitions from the steep head to the long tail.
    Countries at this elbow (ranks {tipping - 3 if tipping and tipping > 3 else 1}--{tipping + 2 if tipping else 5})
    represent the highest return on investment: small funding increases can push them above critical
    thresholds and trigger self-sustaining growth.
  </div>
  <p style="line-height:1.7;margin-top:16px;">
    <strong>Three policy recommendations from the power-law analysis:</strong>
  </p>
  <ol style="margin:12px 0 0 24px;line-height:2;">
    <li><strong>Redistribute, don't just add:</strong> Simply adding more global trial funding
    will flow to existing hubs via preferential attachment. Active redistribution policies
    (site selection quotas, capacity-building mandates) are needed to break the cycle.</li>
    <li><strong>Target the elbow countries:</strong> Mid-ranked countries (e.g., Senegal, Ghana,
    Cameroon) are closest to self-sustaining thresholds. Investment here produces
    disproportionate returns compared to either the hubs (diminishing returns) or the
    void states (insufficient absorptive capacity).</li>
    <li><strong>Build south-south networks:</strong> Hubs like South Africa and Egypt should
    serve as regional mentors, not just competitors. Co-PI arrangements and shared
    regulatory pathways can transfer the network effects that drive preferential attachment.</li>
  </ol>
</div>

<!-- ============================================================ -->
<!-- METHOD -->
<!-- ============================================================ -->
<div class="method">
  <strong>Method:</strong> ClinicalTrials.gov API v2 queried for all interventional studies
  (AREA[StudyType]INTERVENTIONAL) by country location across 30 African, 15 Latin American,
  and 15 Asian countries. Power-law exponent fitted via OLS on log-log rank-frequency.
  Gini coefficient computed from trial count distribution. HHI computed as sum of squared
  shares. Shannon entropy and evenness (H/H_max) computed for diversity comparison.
  Population denominators from UN 2025 estimates. Income Gini values from World Bank 2023 estimates.
  All data cached locally with 24-hour TTL.
</div>

<div class="footer">
  Project 39 of the Africa RCT Audit Series |
  Data: ClinicalTrials.gov | Analysis: Python |
  Theoretical framework: Merton (1968), Barabasi & Albert (1999), Zipf (1949)
</div>

</div>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Project 39: The Power Law of Research")
    print("=" * 60)

    data = fetch_all_data()
    results = analyze_data(data)
    html = generate_html(data, results)

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nGenerated {OUTPUT_HTML}")
    print(f"  Africa Gini:        {results['africa']['gini']:.3f}")
    print(f"  Africa HHI:         {results['africa']['hhi']:.4f}")
    print(f"  Power-law exponent: {abs(results['africa']['power_law_beta']) if results['africa']['power_law_beta'] is not None else 'N/A'}")
    print(f"  Tipping point rank: {results['africa'].get('tipping_rank', 'N/A')}")
    print(f"  Countries < 1/M:    {len(results['below_threshold'])}")
    if results['matthew_effect']:
        m = results['matthew_effect']
        print(f"  Top 2 share:        {m['top2_share_pct']}% ({m['top2_countries'][0]}, {m['top2_countries'][1]})")
    print("\nDone.")


if __name__ == "__main__":
    main()
