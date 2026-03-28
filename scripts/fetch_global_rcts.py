import json
import requests
import time
from datetime import datetime

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

NEW_REGIONS = {
    "South America": "South America",
    "India": "India",
    "East Asia": "East Asia",
    "Southeast Asia": "South East Asia",
    "Eastern Europe": "Eastern Europe"
}

def get_total(location):
    params = {
        "format": "json",
        "pageSize": 1,
        "countTotal": "true",
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("totalCount", 0)
    except Exception as e:
        print(f"Error for {location}: {e}")
        return 0

results = {}
for name, loc in NEW_REGIONS.items():
    print(f"Querying {name}...")
    results[name] = get_total(loc)
    time.sleep(0.5)

# Also get Africa and Europe for context
results["Africa"] = get_total("Africa")
results["Europe"] = get_total("Europe")

print("\n--- Global RCT Volume (Interventional) ---")
for reg, count in sorted(results.items(), key=lambda x: x[1], reverse=True):
    print(f"{reg}: {count:,}")

with open("C:/AfricaRCT/data/global_comparison_data.json", "w") as f:
    json.dump(results, f, indent=2)
