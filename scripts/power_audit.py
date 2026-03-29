import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")

def fetch_power_metadata(location, count=500):
    print(f"  Auditing Institutional Power for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def run_power_audit():
    results = {}
    REGIONS = ["Africa", "Europe", "China", "India"]
    
    for reg in REGIONS:
        studies = fetch_power_metadata(reg)
        if not studies: continue
        
        sponsors = {}
        total = len(studies)
        phase_1 = 0
        phase_3 = 0
        
        for s in studies:
            proto = s.get("protocolSection", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            design = proto.get("designModule", {})
            
            lead = sponsor_mod.get("leadSponsor", {}).get("name", "Unknown")
            sponsors[lead] = sponsors.get(lead, 0) + 1
            
            phases = design.get("phases", [])
            if "PHASE1" in phases: phase_1 += 1
            if "PHASE3" in phases: phase_3 += 1

        # Sort sponsors by count
        sorted_sponsors = sorted(sponsors.items(), key=lambda x: x[1], reverse=True)
        top_sponsor_share = (sorted_sponsors[0][1] / total) * 100 if sorted_sponsors else 0
        
        results[reg] = {
            "top_sponsor": sorted_sponsors[0][0] if sorted_sponsors else "None",
            "top_sponsor_share_pct": round(top_sponsor_share, 1),
            "super_sponsor_count": sum(1 for name, count in sorted_sponsors if (count/total) > 0.05), # >5% share
            "innovation_delta": round(phase_1 / max(1, phase_3), 2),
            "total_sponsors": len(sponsors)
        }

    print(json.dumps(results, indent=2))
    with open(DATA_DIR / "institutional_power_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_power_audit()
