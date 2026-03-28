import json
import requests
import time

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

NEW_REGIONS = {
    "South America": "South America",
    "India": "India",
    "Brazil": "Brazil",
    "China": "China",
    "South Korea": "South Korea",
    "Japan": "Japan",
    "Eastern Europe": "Eastern Europe",
    "Poland": "Poland",
    "Hungary": "Hungary"
}

def get_total(location):
    # Using query.locn as it's more robust than AREA[LocationRegion] for total counts
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
        return 0

results = {}
for name, loc in NEW_REGIONS.items():
    print(f"Querying {name}...")
    results[name] = get_total(loc)
    time.sleep(0.5)

print("\n--- Global RCT Volume (Interventional) ---")
for reg, count in sorted(results.items(), key=lambda x: x[1], reverse=True):
    print(f"{reg}: {count:,}")
