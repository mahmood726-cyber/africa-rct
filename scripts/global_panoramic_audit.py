import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")

GLOBAL_POWERS = ["United States", "United Kingdom", "Germany", "France", "Japan", "Canada", "Australia", "China", "India", "Brazil", "Russia", "South Korea"]
AFRICA_REGIONS = {
    "North": ["Egypt", "Morocco", "Tunisia", "Algeria"],
    "South": ["South Africa", "Zimbabwe", "Zambia", "Malawi"],
    "East": ["Kenya", "Uganda", "Tanzania", "Ethiopia", "Rwanda"],
    "West": ["Nigeria", "Ghana", "Senegal", "Mali", "Burkina Faso"],
    "Central": ["Democratic Republic of Congo", "Cameroon", "Gabon"]
}

def safe_get(params):
    for _ in range(3):
        try:
            r = requests.get(BASE_URL, params=params, timeout=30)
            if r.status_code == 200:
                return r.json().get("totalCount", 0)
        except: pass
        time.sleep(1)
    return 0

def run_panoramic_audit():
    print("Initiating Global Panoramic & Demographic Audit...")
    results = {"global": {}, "africa_regions": {}, "demographics": {}}
    
    # 1. Global Hegemony
    for country in GLOBAL_POWERS:
        print(f"  Fetching {country}...")
        results["global"][country] = safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": country, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
        
    # 2. Intra-African Disparity
    for region, countries in AFRICA_REGIONS.items():
        print(f"  Fetching Africa - {region}...")
        total = 0
        for c in countries:
            total += safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": c, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
        results["africa_regions"][region] = total
        
    # 3. Demographic/Ethnicity Void
    print("  Fetching Demographic/Ethnicity markers...")
    eth_term = "race OR ethnicity OR indigenous OR ancestry"
    results["demographics"]["US_total"] = results["global"]["United States"]
    results["demographics"]["US_eth"] = safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": "United States", "query.term": eth_term, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
    
    africa_total = sum(results["africa_regions"].values())
    results["demographics"]["Africa_total"] = africa_total
    results["demographics"]["Africa_eth"] = safe_get({"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": "Africa", "query.term": eth_term, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
    
    print(json.dumps(results, indent=2))
    with open(DATA_DIR / "global_panoramic_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_panoramic_audit()
