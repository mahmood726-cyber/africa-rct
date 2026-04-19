import json
import math
import os
import sys
from collections import Counter
from datetime import datetime

def get_first_digit(n):
    if n is None or n <= 0: return None
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
    status = "Pass" if mad <= 0.012 else "Fail" if mad > 0.015 else "Marginal"
    return {"total": total, "mad": mad, "status": status}

def audit_file(file_path):
    print(f"[*] Auditing: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    metrics = {"enrollment": [], "locations": []}
    sponsors = {"INDUSTRY": [], "ACADEMIC": []}

    # Handling nested structure (Mizan style)
    countries = data.get("countries", {})
    if not countries: # Try fallback for other schemas
        trials = data.get("sample_trials", []) or data.get("studies", [])
    else:
        trials = []
        for c in countries.values():
            trials.extend(c.get("sample_trials", []))

    for t in trials:
        # Enrollment
        e = t.get("enrollment") or t.get("protocolSection", {}).get("designModule", {}).get("enrollmentInfo", {}).get("count")
        if e: metrics["enrollment"].append(e)
        
        # Locations
        l = t.get("locations_count")
        if l is None:
            locs = t.get("protocolSection", {}).get("contactsLocationsModule", {}).get("locations", [])
            l = len(locs) if locs else None
        if l: metrics["locations"].append(l)
        
        # Sponsor
        s_class = t.get("sponsor_class") or t.get("protocolSection", {}).get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("class")
        if s_class == "INDUSTRY":
            if l: sponsors["INDUSTRY"].append(l)
        elif s_class:
            if l: sponsors["ACADEMIC"].append(l)

    results = {
        "timestamp": datetime.now().isoformat(),
        "file": file_path,
        "general": {
            "enrollment": analyze_benford(metrics["enrollment"]),
            "locations": analyze_benford(metrics["locations"])
        },
        "equity": {
            "industry": analyze_benford(sponsors["INDUSTRY"]),
            "academic": analyze_benford(sponsors["ACADEMIC"])
        }
    }
    
    output_fn = f"truthcert_{os.path.basename(file_path)}"
    with open(output_fn, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"[+] Audit Complete. Results in {output_fn}")
    return results

if __name__ == "__main__":
    if len(sys.argv) > 1:
        audit_file(sys.argv[1])
    else:
        print("Usage: python sentinel_master_auditor.py <path_to_json>")
