import json
import requests
import time

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

SOUTH_HUBS = ["Africa", "India", "China", "Brazil"]
NORTH_HUBS = ["United States", "United Kingdom", "France", "Germany", "Japan"]

def analyze_axis(location, count=300):
    print(f"  Analyzing collaboration axis for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45)
        studies = resp.json().get("studies", [])
    except: return {}

    axis_stats = {
        "total": len(studies),
        "south_south": 0,
        "south_north": 0,
        "pure_local": 0
    }

    for s in studies:
        proto = s.get("protocolSection", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        collabs = sponsor_mod.get("collaborators", [])
        
        if not collabs:
            axis_stats["pure_local"] += 1
            continue
            
        is_south = False
        is_north = False
        
        for c in collabs:
            name = c.get("name", "").lower()
            # Heuristic for South-South
            if any(h.lower() in name for h in SOUTH_HUBS):
                is_south = True
            # Heuristic for South-North
            if any(h.lower() in name for h in NORTH_HUBS):
                is_north = True
                
        if is_south and not is_north: axis_stats["south_south"] += 1
        if is_north: axis_stats["south_north"] += 1

    return axis_stats

results = {hub: analyze_axis(hub) for hub in SOUTH_HUBS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/south_south_axis_data.json", "w") as f:
    json.dump(results, f, indent=2)
