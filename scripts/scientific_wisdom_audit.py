import json
import requests
import time

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_wisdom_metrics(location):
    print(f"  Probing for Scientific Wisdom in {location}...")
    base_params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location}
    
    def safe_get(filter_str):
        p = {**base_params, "filter.advanced": filter_str}
        try:
            r = requests.get(BASE_URL, params=p, timeout=30)
            if r.status_code == 200:
                return r.json().get("totalCount", 0)
        except: pass
        return 0

    total = safe_get("AREA[StudyType]INTERVENTIONAL") or 1
    
    # 2. Public Health Focus
    public_health = safe_get("AREA[StudyType]INTERVENTIONAL AND (AREA[PrimaryPurpose]Prevention OR AREA[PrimaryPurpose]Health Services Research)")
    
    # 3. Open Knowledge Signal (Heuristic: Completed + Has Results is preferred, but let's check Completed as proxy if direct flag fails)
    completed = safe_get("AREA[StudyType]INTERVENTIONAL AND AREA[OverallStatus]COMPLETED")
    
    # 4. Universal Need (Rare)
    rare = safe_get("AREA[StudyType]INTERVENTIONAL AND AREA[Condition]Rare")

    return {
        "total": total,
        "public_health_ratio": round((public_health / total) * 100, 1),
        "transparency_potential": round((completed / total) * 100, 1),
        "universal_service_ratio": round((rare / total) * 100, 2)
    }

results = {reg: fetch_wisdom_metrics(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/scientific_wisdom_data.json", "w") as f:
    json.dump(results, f, indent=2)
