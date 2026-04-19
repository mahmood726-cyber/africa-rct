import json
import requests
import time
import math
import os
from collections import Counter

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

def get_first_digit(n):
    if n is None or n <= 0:
        return None
    s = str(n).replace('.', '').lstrip('0')
    return int(s[0]) if s else None

def calculate_benford_probs():
    return {d: math.log10(1 + 1/d) for d in range(1, 10)}

def analyze_benford(data_list):
    digits = [get_first_digit(x) for x in data_list]
    digits = [d for d in digits if d is not None]
    if not digits: return None
    counts = Counter(digits)
    total = len(digits)
    observed = {d: counts.get(d, 0) / total for d in range(1, 10)}
    expected = calculate_benford_probs()
    mad = sum(abs(observed[d] - expected[d]) for d in range(1, 10)) / 9
    status = "Close Conformity"
    if mad > 0.015: status = "Non-Conformity (CRITICAL)"
    elif mad > 0.012: status = "Marginally Acceptable"
    elif mad > 0.006: status = "Acceptable Conformity"
    return {"total": total, "mad": mad, "status": status, "dist": observed}

def fetch_trials(location, limit=500):
    params = {
        "format": "json",
        "pageSize": 100,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    enrollments = []
    locations = []
    next_token = None
    
    fetched = 0
    while fetched < limit:
        if next_token: params["pageToken"] = next_token
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            studies = data.get("studies", [])
            if not studies: break
            
            for s in studies:
                proto = s.get("protocolSection", {})
                design = proto.get("designModule", {})
                enrollment_info = design.get("enrollmentInfo", {})
                enrollment = enrollment_info.get("count")
                
                contacts_loc = proto.get("contactsLocationsModule", {})
                loc_list = contacts_loc.get("locations", [])
                
                if enrollment is not None: enrollments.append(enrollment)
                if loc_list: locations.append(len(loc_list))
            
            fetched += len(studies)
            next_token = data.get("nextPageToken")
            if not next_token: break
            time.sleep(RATE_LIMIT_DELAY)
        except Exception as e:
            print(f"Error fetching {location}: {e}")
            break
            
    return enrollments, locations

# Add RATE_LIMIT_DELAY at the top if not present
RATE_LIMIT_DELAY = 0.4

def main():
    # Sample Europe (Germany) vs Africa (already have Africa, but let's fetch a fresh sample or use Mizan)
    print("Fetching European Sample (Germany)...")
    eu_enroll, eu_locs = fetch_trials("Germany", limit=1000)
    
    eu_enroll_audit = analyze_benford(eu_enroll)
    eu_locs_audit = analyze_benford(eu_locs)
    
    # Load Africa results for comparison
    africa_results_path = "C:/AfricaRCT/sentinel_audit_results.json"
    with open(africa_results_path, 'r') as f:
        africa_results = json.load(f)
        
    comparison = {
        "timestamp": "2026-04-19",
        "europe_germany": {
            "enrollment": eu_enroll_audit,
            "locations_count": eu_locs_audit
        },
        "africa": africa_results["metrics"]
    }
    
    with open("C:/AfricaRCT/sentinel_comparison_results.json", "w") as f:
        json.dump(comparison, f, indent=2)
        
    print("Comparison complete.")
    
    # TruthCert Summary Update
    summary_path = "C:/AfricaRCT/SENTINEL_COMPARISON_SUMMARY.md"
    with open(summary_path, 'w') as f:
        f.write("# Sentinel Cross-Regional Forensic Audit\n\n")
        f.write("Comparing Benford's Law conformity between Africa and Europe (Germany).\n\n")
        
        f.write("## 1. Enrollment Count (Data Entry Integrity)\n")
        f.write(f"- **Africa**: {comparison['africa']['enrollment']['status']} (MAD: {comparison['africa']['enrollment']['mad']:.6f})\n")
        f.write(f"- **Europe**: {comparison['europe_germany']['enrollment']['status']} (MAD: {comparison['europe_germany']['enrollment']['mad']:.6f})\n\n")
        
        f.write("## 2. Locations Count (Structural Clustering)\n")
        f.write(f"- **Africa**: {comparison['africa']['locations_count']['status']} (MAD: {comparison['africa']['locations_count']['mad']:.6f})\n")
        f.write(f"- **Europe**: {comparison['europe_germany']['locations_count']['status']} (MAD: {comparison['europe_germany']['locations_count']['mad']:.6f})\n\n")
        
        f.write("### Insight\n")
        if comparison['africa']['locations_count']['mad'] > comparison['europe_germany']['locations_count']['mad'] * 2:
            f.write("CRITICAL: African trial locations show massive non-conformity compared to European standards. This indicates extreme geographic hub-clustering or 'unnatural' location reporting distributions in the African dataset.\n")
        else:
            f.write("Both regions show similar distribution patterns for locations.\n")

if __name__ == "__main__":
    main()
