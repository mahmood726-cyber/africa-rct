import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe"]

def fetch_sovereignty_metadata(location, count=400):
    print(f"  Probing Data Sovereignty for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def run_sovereignty_audit():
    results = {}
    for reg in REGIONS:
        studies = fetch_sovereignty_metadata(reg)
        if not studies: continue
        
        ipd_yes = 0
        ipd_no = 0
        ipd_undecided = 0
        has_protocol_link = 0
        has_sap_link = 0 # Statistical Analysis Plan
        total = len(studies)
        
        for s in studies:
            proto = s.get("protocolSection", {})
            ipd_mod = proto.get("ipdSharingModule", {})
            sharing = ipd_mod.get("ipdSharing", "Not Provided").upper()
            
            if sharing == "YES": ipd_yes += 1
            elif sharing == "NO": ipd_no += 1
            else: ipd_undecided += 1
            
            # Transparency Artifacts
            info_docs = ipd_mod.get("infoTypes", [])
            if any("PROTOCOL" in d.upper() for d in info_docs): has_protocol_link += 1
            if any("SAP" in d.upper() for d in info_docs): has_sap_link += 1

        results[reg] = {
            "ipd_sharing_rate": round((ipd_yes / total) * 100, 1),
            "data_withholding_rate": round((ipd_no / total) * 100, 1),
            "protocol_sharing_rate": round((has_protocol_link / total) * 100, 1),
            "sap_sharing_rate": round((has_sap_link / total) * 100, 1),
            "total": total
        }

    print(json.dumps(results, indent=2))
    with open("C:/AfricaRCT/data/data_sovereignty_audit.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_sovereignty_audit()
