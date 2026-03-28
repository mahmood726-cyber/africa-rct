import json
import requests
import time

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
HUBS = {
    "China": "China",
    "India": "India",
    "Brazil": "Brazil",
    "Poland": "Poland"
}

CONDITIONS = ["cancer", "diabetes", "hiv", "malaria"]
PHASES = ["PHASE1", "PHASE2", "PHASE3"]

def get_count(location, cond=None, phase=None, s_class=None):
    params = {"format": "json", "pageSize": 1, "countTotal": "true", "query.locn": location}
    filters = ["AREA[StudyType]INTERVENTIONAL"]
    if phase: filters.append(f"AREA[Phase]{phase.replace('PHASE', 'Phase ')}")
    if s_class: filters.append(f"AREA[LeadSponsorClass]{s_class}")
    params["filter.advanced"] = " AND ".join(filters)
    if cond: params["query.cond"] = cond
    
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        return resp.json().get("totalCount", 0)
    except: return 0

results = {}
for name, loc in HUBS.items():
    print(f"Auditing {name}...")
    data = {"total": get_count(loc)}
    data["conditions"] = {c: get_count(loc, cond=c) for c in CONDITIONS}
    data["phases"] = {p: get_count(loc, phase=p) for p in PHASES}
    data["industry"] = get_count(loc, s_class="INDUSTRY")
    results[name] = data
    time.sleep(0.5)

print(json.dumps(results, indent=2))
with open("C:/AfricaRCT/data/expanded_global_audit.json", "w") as f:
    json.dump(results, f, indent=2)
