import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_granular_metadata(location, count=300):
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

def run_deep_audit():
    results = {}
    for reg in REGIONS:
        studies = fetch_granular_metadata(reg)
        if not studies: continue
        
        outcome_counts = []
        secondary_counts = []
        eligibility_lengths = []
        dmc_count = 0
        fda_oversight = 0
        arm_counts = []
        
        for s in studies:
            proto = s.get("protocolSection", {})
            outcomes = proto.get("outcomesModule", {})
            eligibility = proto.get("eligibilityModule", {})
            oversight = proto.get("oversightModule", {})
            design = proto.get("designModule", {})
            
            # 1. Endpoint Density
            primary = outcomes.get("primaryOutcomes", [])
            secondary = outcomes.get("secondaryOutcomes", [])
            outcome_counts.append(len(primary))
            secondary_counts.append(len(secondary))
            
            # 2. Eligibility Complexity (Character count as proxy for stringency)
            inc = eligibility.get("eligibilityCriteria", "")
            eligibility_lengths.append(len(inc))
            
            # 3. Regulatory Oversight
            if oversight.get("hasDmc"): dmc_count += 1
            if oversight.get("isFdaRegulatedDrug") or oversight.get("isFdaRegulatedDevice"):
                fda_oversight += 1
                
            # 4. Design Complexity (Arms)
            arms = design.get("armsInterventionsModule", {}).get("armGroups", [])
            arm_counts.append(len(arms))

        results[reg] = {
            "avg_primary_outcomes": round(sum(outcome_counts) / len(outcome_counts), 1) if outcome_counts else 0,
            "avg_secondary_outcomes": round(sum(secondary_counts) / len(secondary_counts), 1) if secondary_counts else 0,
            "avg_eligibility_chars": round(sum(eligibility_lengths) / len(eligibility_lengths), 0) if eligibility_lengths else 0,
            "dmc_rate": round((dmc_count / len(studies)) * 100, 1),
            "fda_oversight_rate": round((fda_oversight / len(studies)) * 100, 1),
            "avg_arms": round(sum(arm_counts) / len(arm_counts), 1) if arm_counts else 0,
            "sample_size": len(studies)
        }

    print(json.dumps(results, indent=2))
    with open("C:/AfricaRCT/data/deep_granularity_audit.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_deep_audit()
