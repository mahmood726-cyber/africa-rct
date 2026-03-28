import json
import requests
import time

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

def audit_sharing_sovereignty(location):
    print(f"  Auditing Sharing Sovereignty for {location}...")
    # Fetch a sample of trials where IPD Sharing is YES
    params = {
        "format": "json", "pageSize": 100,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL AND AREA[IPDSharing]Yes"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45).json()
        studies = resp.get("studies", [])
    except: return {}

    foreign_mandated = 0
    local_voluntary = 0
    
    # We'll check the Lead Sponsor Class and Name
    # Heuristic for African hub lead
    AFRICAN_KEYWORDS = ["egypt", "kenya", "nigeria", "uganda", "south africa", "cairo", "nairobi", "ibadan", "makerere"]

    for s in studies:
        proto = s.get("protocolSection", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        lead = sponsor_mod.get("leadSponsor", {})
        name = lead.get("name", "").lower()
        s_class = lead.get("class", "")
        
        if s_class in ["INDUSTRY", "NIH", "FED"]:
            foreign_mandated += 1
        elif any(kw in name for kw in AFRICAN_KEYWORDS):
            local_voluntary += 1
        else:
            # Often other foreign universities
            foreign_mandated += 1

    return {
        "total_sharing": len(studies),
        "foreign_mandated_sharing": foreign_mandated,
        "local_sovereign_sharing": local_voluntary
    }

results = {
    "Africa": audit_sharing_sovereignty("Africa"),
    "Europe": audit_sharing_sovereignty("Europe")
}
print(json.dumps(results, indent=2))

with open("C:/AfricaRCT/data/sharing_sovereignty_data.json", "w") as f:
    json.dump(results, f, indent=2)
