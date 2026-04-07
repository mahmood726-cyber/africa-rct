import json
import requests
import time
from pathlib import Path
from collections import Counter

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")

def fetch_investigator_data(location, count=250):
    print(f"  Tracing Investigators and Sponsors for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def run_pi_audit():
    REGIONS = ["Africa", "Europe"]
    results = {}
    
    for reg in REGIONS:
        studies = fetch_investigator_data(reg)
        if not studies: continue
        
        sponsors = []
        investigators = []
        affiliations = []
        total = len(studies)
        
        for s in studies:
            proto = s.get("protocolSection", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            contacts = proto.get("contactsLocationsModule", {})
            
            # Sponsor
            sponsors.append(sponsor_mod.get("leadSponsor", {}).get("name", "Unknown"))
            
            # PI (Official)
            officials = contacts.get("overallOfficials", [])
            for off in officials:
                name = off.get("name", "Unknown")
                affil = off.get("affiliation", "Unknown")
                if name != "Unknown": investigators.append(name)
                if affil != "Unknown": affiliations.append(affil)

        # Analysis
        sponsor_counts = Counter(sponsors)
        pi_counts = Counter(investigators)
        affil_counts = Counter(affiliations)
        
        results[reg] = {
            "top_5_sponsors": sponsor_counts.most_common(5),
            "top_5_investigators": pi_counts.most_common(5),
            "top_5_affiliations": affil_counts.most_common(5),
            "corporate_consolidation_index": round((sum(count for name, count in sponsor_counts.most_common(5)) / total) * 100, 1),
            "investigator_monopoly_index": round((sum(count for name, count in pi_counts.most_common(5)) / max(1, len(investigators))) * 100, 1) if investigators else 0,
            "total_trials": total
        }

    print(json.dumps(results, indent=2))
    with open(DATA_DIR / "pi_consolidated_audit.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_pi_audit()
