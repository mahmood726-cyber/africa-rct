import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe"]

def fetch_intellectual_metadata(location, count=300):
    print(f"  Probing Intellectual Capital for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def run_value_chain_audit():
    results = {}
    for reg in REGIONS:
        studies = fetch_intellectual_metadata(reg)
        if not studies: continue
        
        has_official = 0
        local_official = 0
        foreign_official = 0
        total_enrollment = 0
        
        # Heuristics for "Local" based on hub keywords
        AFRICAN_HUBS = ["Egypt", "South Africa", "Nigeria", "Kenya", "Uganda", "Ghana", "Ethiopia", "Tanzania", "Cairo", "Nairobi", "Lagos", "Ibadan", "Makerere"]

        for s in studies:
            proto = s.get("protocolSection", {})
            contacts = proto.get("contactsLocationsModule", {})
            officials = contacts.get("overallOfficials", [])
            design = proto.get("designModule", {})
            enrollment = design.get("enrollmentInfo", {}).get("count", 0)
            total_enrollment += enrollment
            
            if officials:
                has_official += 1
                # Check affiliation/affiliation of the first official
                affil = officials[0].get("affiliation", "").lower()
                if any(hub.lower() in affil for hub in AFRICAN_HUBS):
                    local_official += 1
                else:
                    foreign_official += 1

        # Economic Value Transfer Calculation (Hypothetical Model)
        # Based on industry standards: Phase 3 trial cost per patient in HIC (~) vs LIC (~)
        # The 'Value Transfer' is the delta saved by the sponsor ( per participant)
        value_transfer = total_enrollment * 35000 

        results[reg] = {
            "official_coverage": round((has_official / len(studies)) * 100, 1),
            "local_leadership_rate": round((local_official / max(1, has_official)) * 100, 1),
            "foreign_governance_rate": round((foreign_official / max(1, has_official)) * 100, 1),
            "total_participants_sampled": total_enrollment,
            "estimated_value_transfer_usd": value_transfer,
            "sample_size": len(studies)
        }

    print(json.dumps(results, indent=2))
    with open("C:/AfricaRCT/data/value_chain_audit_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_value_chain_audit()
