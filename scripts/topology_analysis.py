import json
import requests
import time

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def analyze_topology(location, count=200):
    print(f"  Mapping topology for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45)
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
    except: return {}

    # Topology metrics
    # 1. Clustering proxy: avg collaborators per trial
    # 2. Network type: Multi-continental vs Regional
    # 3. Hub Power: Lead sponsor class diversity
    
    collab_counts = []
    classes = {}
    
    for s in studies:
        proto = s.get("protocolSection", {})
        sponsors = proto.get("sponsorCollaboratorsModule", {})
        lead = sponsors.get("leadSponsor", {})
        collabs = sponsors.get("collaborators", [])
        
        collab_counts.append(len(collabs))
        s_class = lead.get("class", "OTHER")
        classes[s_class] = classes.get(s_class, 0) + 1
        
    return {
        "avg_degree": round(sum(collab_counts) / len(collab_counts), 2) if collab_counts else 0,
        "max_degree": max(collab_counts) if collab_counts else 0,
        "sponsor_diversity": len(classes),
        "industry_led_pct": round((classes.get("INDUSTRY", 0) / len(studies)) * 100, 1) if studies else 0
    }

results = {reg: analyze_topology(reg) for reg in REGIONS}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/topology_data.json", "w") as f:
    json.dump(results, f, indent=2)
