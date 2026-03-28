import json
import requests
import time
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe"]

# Semantic Lexicons
INNOVATION_KEYWORDS = ["novel", "first-in-human", "breakthrough", "pioneering", "discovery", "innovative", "new", "first"]
EVALUATION_KEYWORDS = ["evaluate", "assess", "efficacy", "safety", "standard", "comparison", "study", "trial"]

def fetch_narrative_data(location, count=100):
    print(f"  Performing semantic probe for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json().get("studies", [])
    except Exception as e:
        print(f"Error fetching {location}: {e}")
        return []

def run_semantic_audit():
    results = {}
    for reg in REGIONS:
        studies = fetch_narrative_data(reg)
        if not studies: continue
        
        innovation_score = 0
        evaluation_score = 0
        total_words = 0
        
        for s in studies:
            proto = s.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            title = ident.get("briefTitle", "").lower()
            summary = proto.get("descriptionModule", {}).get("briefSummary", "").lower()
            
            full_text = title + " " + summary
            
            innovation_score += sum(1 for kw in INNOVATION_KEYWORDS if kw in full_text)
            evaluation_score += sum(1 for kw in EVALUATION_KEYWORDS if kw in full_text)
            total_words += len(full_text.split())

        results[reg] = {
            "innovation_intensity": round((innovation_score / max(1, len(studies))) * 10, 2),
            "evaluation_intensity": round((evaluation_score / max(1, len(studies))) * 10, 2),
            "promise_ratio": round(innovation_score / max(1, evaluation_score), 2),
            "avg_description_length": round(total_words / max(1, len(studies)), 0),
            "sample_size": len(studies)
        }

    print(json.dumps(results, indent=2))
    with open("C:/AfricaRCT/data/semantic_promise_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_semantic_audit()
