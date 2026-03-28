import json
import requests
import time
import math
import numpy as np
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_granular_data(location, count=500):
    print(f"  Performing deep-space probe for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def benford_analysis(numbers):
    """Measures adherence to Benford's Law (First Digit Law)."""
    if not numbers: return 0
    first_digits = [int(str(abs(int(n)))[0]) for n in numbers if n and abs(int(n)) > 0]
    if not first_digits: return 0
    counts = np.bincount(first_digits, minlength=10)[1:10]
    total = sum(counts)
    if total == 0: return 0
    observed = counts / total
    expected = np.array([math.log10(1 + 1/d) for d in range(1, 10)])
    # Return Mean Absolute Deviation (MAD)
    return np.mean(np.abs(observed - expected))

def kl_divergence(p_counts, q_counts):
    """Calculates Information Distance (KLD) between two distributions."""
    def normalize(counts):
        s = sum(counts)
        if s == 0: return np.zeros(len(counts))
        return np.array(counts) / s
    
    p = normalize(p_counts)
    q = normalize(q_counts)
    # Add epsilon to avoid log(0)
    p = p + 1e-10
    q = q + 1e-10
    return float(np.sum(p * np.log2(p / q)))

def run_topology_audit():
    print("Initiating Advanced Information Topology Audit...")
    
    raw_data = {reg: fetch_granular_data(reg) for reg in REGIONS}
    
    results = {}
    for reg in REGIONS:
        studies = raw_data[reg]
        if not studies: continue
        
        # 1. Benford MAD (Enrollment Numbers)
        enrollments = []
        condition_vec = {}
        for s in studies:
            proto = s.get("protocolSection", {})
            enroll = proto.get("designModule", {}).get("enrollmentInfo", {}).get("count", 0)
            enrollments.append(enroll)
            
            # For KLD (Thematic distribution)
            conds = proto.get("conditionsModule", {}).get("conditions", [])
            for c in conds:
                condition_vec[c.lower()] = condition_vec.get(c.lower(), 0) + 1
        
        mad = benford_analysis(enrollments)
        
        # 2. Pareto Ratio (Power Law)
        enroll_sorted = sorted(enrollments, reverse=True)
        top_20_pct = int(len(enroll_sorted) * 0.2)
        total_enroll = sum(enroll_sorted)
        pareto = sum(enroll_sorted[:top_20_pct]) / max(1, total_enroll) if total_enroll else 0
        
        results[reg] = {
            "benford_mad": round(mad, 4),
            "pareto_ratio": round(pareto, 2),
            "condition_vec": condition_vec,
            "sample_size": len(studies)
        }

    # 3. Informational Distance (KLD) - How different is Africa from Europe?
    # Intersection of conditions
    all_conds = set(results["Africa"]["condition_vec"].keys()).union(set(results["Europe"]["condition_vec"].keys()))
    af_vec = [results["Africa"]["condition_vec"].get(c, 0) for c in all_conds]
    eu_vec = [results["Europe"]["condition_vec"].get(c, 0) for c in all_conds]
    
    results["Africa_Europe_KLD"] = round(kl_divergence(af_vec, eu_vec), 3)

    print(f"\nAudit Results:\nKLD (Africa vs Europe): {results['Africa_Europe_KLD']}")
    for reg in REGIONS:
        print(f"{reg} - Benford MAD: {results[reg]['benford_mad']}, Pareto: {results[reg]['pareto_ratio']}")

    with open("C:/AfricaRCT/data/information_topology_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_topology_audit()
