# sentinel:skip-file — hardcoded paths are fixture/registry/audit-narrative data for this repo's research workflow, not portable application configuration. Same pattern as push_all_repos.py and E156 workbook files.
import json
import requests
import time

from repo_paths import data_file

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

BIG_PHARMA = ["Pfizer", "Roche", "Novartis", "Merck", "GlaxoSmithKline", "Johnson & Johnson", "AstraZeneca", "Sanofi", "AbbVie", "Bayer"]
WESTERN_HUBS = ["Harvard", "Oxford", "Johns Hopkins", "Stanford", "Yale", "Cambridge", "MIT", "Imperial College", "UCSF", "NIH"]

def audit_hegemony(location):
    print(f"  Tracing Global Influence in {location}...")
    base_params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    
    # 1. Big Pharma Presence
    pharma_counts = {}
    for pharma in BIG_PHARMA:
        p_params = base_params.copy()
        p_params["query.term"] = pharma
        pharma_counts[pharma] = requests.get(BASE_URL, params=p_params).json().get("totalCount", 0)
        time.sleep(0.2)
        
    # 2. Western Academic Presence
    hub_counts = {}
    for hub in WESTERN_HUBS:
        h_params = base_params.copy()
        h_params["query.term"] = hub
        hub_counts[hub] = requests.get(BASE_URL, params=h_params).json().get("totalCount", 0)
        time.sleep(0.2)

    # 3. Total Interventional for normalization
    total = requests.get(BASE_URL, params=base_params).json().get("totalCount", 1)

    return {
        "total": total,
        "big_pharma_sum": sum(pharma_counts.values()),
        "western_hub_sum": sum(hub_counts.values()),
        "pharma_penetration_rate": round((sum(pharma_counts.values()) / total) * 100, 1),
        "academic_influence_rate": round((sum(hub_counts.values()) / total) * 100, 1),
        "pharma_details": pharma_counts,
        "academic_details": hub_counts
    }

results = {
    "Africa": audit_hegemony("Africa"),
    "India": audit_hegemony("India"),
    "South America": audit_hegemony("South America")
}

print(json.dumps(results, indent=2))
with data_file("global_hegemony_audit.json").open("w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
