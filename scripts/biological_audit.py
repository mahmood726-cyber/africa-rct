import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

# Keywords for biological extraction
EXTRACT_KEYWORDS = ["biobank", "genetic", "dna", "sample", "export", "biomarker", "blood", "frozen", "sequencing"]

def fetch_extraction_data(location):
    print(f"  Probing biological sovereignty for {location}...")
    base_params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location}
    
    # 1. Total Interventional
    total = requests.get(BASE_URL, params={**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}).json().get("totalCount", 1)
    
    # 2. Biological Material Mentions
    extract_params = {**base_params, "query.term": " OR ".join(EXTRACT_KEYWORDS), "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    extract_count = requests.get(BASE_URL, params=extract_params).json().get("totalCount", 0)
    
    # 3. Device trials (Innovation proxy)
    device_params = {**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[InterventionType]DEVICE"}
    device_count = requests.get(BASE_URL, params=device_params).json().get("totalCount", 0)
    
    # 4. Drug trials (Validation proxy)
    drug_params = {**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[InterventionType]DRUG"}
    drug_count = requests.get(BASE_URL, params=drug_params).json().get("totalCount", 0)

    return {
        "total": total,
        "extraction_rate": round((extract_count / total) * 100, 1),
        "device_ratio": round((device_count / total) * 100, 1),
        "drug_ratio": round((drug_count / total) * 100, 1),
        "innovation_symmetry": round(device_count / max(1, drug_count), 2)
    }

results = {reg: fetch_extraction_data(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/biological_sovereignty_data.json", "w") as f:
    json.dump(results, f, indent=2)
