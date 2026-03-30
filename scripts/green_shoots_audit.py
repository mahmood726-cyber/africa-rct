import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")

def fetch_green_shoots(location):
    print(f"  Searching for Green Shoots in {location}...")
    base_params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location}
    
    # 1. Intellectual Sovereignty (Local Overall Official)
    # Heuristic: Lead Sponsor is Local + Overall Official is Local
    AFRICAN_HUBS = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda", "Ghana", "Ethiopia", "Tanzania"]
    
    # 2. Methodological Leapfrogging (Adaptive/Platform Trials)
    adaptive_params = {**base_params, "query.term": "adaptive design OR platform trial OR basket trial OR umbrella trial", "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    adaptive_count = requests.get(BASE_URL, params=adaptive_params).json().get("totalCount", 0)
    time.sleep(0.3)
    
    # 3. Community Engagement (Validated Method)
    community_params = {**base_params, "query.term": "community engagement OR community advisory OR participatory", "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    community_count = requests.get(BASE_URL, params=community_params).json().get("totalCount", 0)
    time.sleep(0.3)

    # 4. South-South Bilateralism (Partnering hubs)
    # Example: Nigeria-India or Egypt-China
    ss_params = {**base_params, "query.term": "India OR China OR Brazil", "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}
    ss_count = requests.get(BASE_URL, params=ss_params).json().get("totalCount", 0)

    # Get total for normalization
    total = requests.get(BASE_URL, params={**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"}).json().get("totalCount", 1)

    return {
        "total": total,
        "adaptive_rate": round((adaptive_count / total) * 100, 2),
        "community_engagement_rate": round((community_count / total) * 100, 2),
        "south_south_rate": round((ss_count / total) * 100, 2),
        "adaptive_count": adaptive_count,
        "community_count": community_count,
        "south_south_count": ss_count
    }

results = {"Africa": fetch_green_shoots("Africa")}
print(json.dumps(results, indent=2))

with open(DATA_DIR / "green_shoots_data.json", "w") as f:
    json.dump(results, f, indent=2)
