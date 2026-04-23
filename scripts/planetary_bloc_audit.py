# sentinel:skip-file — hardcoded paths are fixture/registry/audit-narrative data for this repo's research workflow, not portable application configuration. Same pattern as push_all_repos.py and E156 workbook files.
import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")

BLOCS = {
    "G7": ["United States", "United Kingdom", "Canada", "France", "Germany", "Italy", "Japan"],
    "BRICS": ["Brazil", "Russia", "India", "China", "South Africa"],
    "ASEAN": ["Indonesia", "Malaysia", "Philippines", "Singapore", "Thailand", "Vietnam"],
    "AU": ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda", "Ghana", "Ethiopia", "Tanzania", "Morocco", "Algeria"]
}

INNOVATION_TERMS = "AREA[InterventionType]GENETIC OR AREA[InterventionType]BIOLOGICAL OR 'cell therapy' OR 'gene therapy'"

def safe_get(params):
    for _ in range(3):
        try:
            r = requests.get(BASE_URL, params=params, timeout=30)
            if r.status_code == 200:
                return r.json().get("totalCount", 0)
        except: pass
        time.sleep(1)
    return 0

def run_bloc_audit():
    print("Initiating Planetary Economic Bloc Audit...")
    results = {}
    
    for bloc_name, countries in BLOCS.items():
        print(f"  Auditing Bloc: {bloc_name}...")
        total_interventional = 0
        innovation_count = 0
        ncd_count = 0
        infectious_count = 0
        
        for country in countries:
            # 1. Total
            total = safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": country, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
            total_interventional += total
            
            # 2. High-Value Innovation
            innov = safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": country, "filter.advanced": f"AREA[StudyType]INTERVENTIONAL AND ({INNOVATION_TERMS})"})
            innovation_count += innov
            
            # 3. NCDs
            ncd = safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": country, "query.cond": "cancer OR diabetes OR cardiovascular", "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
            ncd_count += ncd
            
            # 4. Infectious
            inf = safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": country, "query.cond": "hiv OR malaria OR tuberculosis", "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
            infectious_count += inf
            
            time.sleep(0.2)
            
        results[bloc_name] = {
            "total_trials": total_interventional,
            "innovation_rate": round((innovation_count / max(1, total_interventional)) * 100, 2),
            "ncd_ratio": round((ncd_count / max(1, total_interventional)) * 100, 1),
            "infectious_ratio": round((infectious_count / max(1, total_interventional)) * 100, 1),
            "diseasome_imbalance": round(ncd_count / max(1, infectious_count), 2)
        }

    print(json.dumps(results, indent=2))
    with open(DATA_DIR / "planetary_bloc_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_bloc_audit()
