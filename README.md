# Africa RCT Research Programme

**60 projects exposing clinical trial inequity across Africa — the most comprehensive registry-based analysis ever conducted.**

Live dashboards rebuild weekly from [ClinicalTrials.gov](https://clinicaltrials.gov) data via GitHub Actions.

## Key Findings

| Finding | Metric | Impact |
|---------|--------|--------|
| Childhood cancer | CCI 553x | 3 trials, 20% vs 80% survival |
| Maternal mortality | CCI ~82x | 9 trials, 66% of global deaths |
| Genomics | ZERO trials | Most genetic diversity, zero precision medicine |
| Air pollution | ZERO trials | 600K deaths/yr from cooking fires |
| Sickle cell | CCI 48.6x | 75% burden, 1.5% trials |
| Traditional medicine | 2 vs 334 | 167x gap vs China |
| Francophone penalty | 5.5x | Language as barrier |
| J&J Africa ratio | 0.8% | Worst of 10 pharma companies |
| Nigeria paradox | 1.5/million | Worst large-country ratio |
| PEPFAR spillover | 0.11 | Zimbabwe most dependent |

## The 60 Projects

### Layer 1: Foundation (1-14)
Continent landscape, Uganda deep-dive, extraction metrics, SCD, ghost enrollment, NCD gap, pharma map, termination cascade, COVID impact, francophone, conflict zones, vaccine colony, desert map, per-capita league

### Layer 2: Comparisons (15-24)
PEPFAR trap, decolonization scorecard, Rwanda model, Nigeria paradox, global south, LatAm mirror, surgical desert, traditional medicine, trauma gap, China leapfrog

### Layer 3: Disease Deep-Dives (25-35)
Forgotten diseases, cervical cancer, palliative care, AMR, maternal mortality, childhood cancer, genomics, heart failure, digital health, RHD, air pollution

### Layer 4: Cross-Disciplinary Frameworks (36-42)
Mizan Index (Quranic justice), Dutch Disease (economics), Terms of Trade (Prebisch-Singer), Power Law (physics), Phase Transition (thermodynamics), Principal-Agent (org theory), Free Rider Genome (game theory)

### Layer 5: Advanced Statistics (43-48)
Placebo ethics audit, design quality, Bayesian/Bootstrap/KM/Monte Carlo, regression model, network analysis, PEPFAR causal inference (ITS + DiD + synthetic control)

### Layer 6: Stakeholder Voices (49-60)
Patient, child, mother, elder, doctor, administrator, government, nurse, community, funder, researcher, future (2035 projections)

## Run Locally

```bash
pip install requests
python run_all.py              # All 60 projects (~45-60 min)
python run_all.py --quick      # 9 fastest projects (~25 sec)
python run_all.py --layer 1    # Foundation layer only
python run_all.py --project 46 # Single project (regression model)
python run_all.py --fresh      # Delete caches, force fresh data
```

Then open `index.html` in a browser.

## How It Works

Each project is a self-contained Python script that:
1. Queries ClinicalTrials.gov API v2 (public, no key needed)
2. Caches results locally (24h TTL)
3. Generates an interactive HTML dashboard

GitHub Actions runs all 60 scripts weekly and deploys to GitHub Pages.

## Data Source

[ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api) — public, no API key required.

## AI Transparency

LLM assistance was used for drafting and language editing. The author reviewed and edited the manuscript and takes responsibility for the final content.

## Files

- 60 core Python scripts (one per project)
- 60+ HTML dashboards (generated from live data)
- 84+ E156 micro-publications (papers + protocols)
- `run_all.py` — living dashboard runner
- `index.html` — master navigation hub
- `.github/workflows/build-dashboards.yml` — weekly auto-rebuild

## License

Code: MIT. Data: ClinicalTrials.gov (public domain).
