import json
import requests
import time

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def safe_fetch(params):
    for i in range(3):
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get("totalCount", 0)
            print(f"  Attempt {i+1} status: {resp.status_code}")
        except Exception as e:
            print(f"  Attempt {i+1} error: {e}")
        time.sleep(1)
    return 0

def fetch_quan_metrics(location):
    print(f"  Probing the totality for {location}...")
    base_params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location}
    
    # 1. Total Interventional
    total = safe_fetch({**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})
    if total == 0: total = 1
    
    # 2. Gender Focus (Female only or Female inclusive)
    female = safe_fetch({**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[EligibilityModule]FEMALE"})
    
    # 3. Demographic extremes (Child/Older)
    child = safe_fetch({**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[EligibilityModule]Child"})
    older = safe_fetch({**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[EligibilityModule]OlderAdult"})
    
    # 4. Rigor (Randomized/Masked)
    random = safe_fetch({**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[DesignAllocation]RANDOMIZED"})
    masked = safe_fetch({**base_params, "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND (AREA[DesignMasking]DOUBLE OR AREA[DesignMasking]TRIPLE OR AREA[DesignMasking]QUADRUPLE)"})

    return {
        "total": total,
        "female_ratio": round(female/total*100, 1),
        "child_ratio": round(child/total*100, 1),
        "older_ratio": round(older/total*100, 1),
        "random_ratio": round(random/total*100, 1),
        "masked_ratio": round(masked/total*100, 1)
    }

results = {reg: fetch_quan_metrics(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/quan_totality_data.json", "w") as f:
    json.dump(results, f, indent=2)
