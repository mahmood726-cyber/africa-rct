import json
import requests
import time
import numpy as np
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe"]

def fetch_genomic_metadata(location, count=300):
    print(f"  Probing Genomic Resilience for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def run_genomic_audit():
    results = {}
    for reg in REGIONS:
        studies = fetch_genomic_metadata(reg)
        if not studies: continue
        
        genomic_mentions = 0
        bio_repository_mentions = 0
        interconnected_sites = 0
        total_sites = 0
        
        GENOMIC_KEYWORDS = ["genomic", "sequencing", "rna", "dna", "variant", "mutation", "snp", "genotype"]
        BIO_KEYWORDS = ["biobank", "repository", "frozen", "storage", "plasma", "specimen"]

        for s in studies:
            proto = s.get("protocolSection", {})
            title = proto.get("identificationModule", {}).get("briefTitle", "").lower()
            summary = proto.get("descriptionModule", {}).get("briefSummary", "").lower()
            locs = proto.get("contactsLocationsModule", {}).get("locations", [])
            
            full_text = title + " " + summary
            if any(kw in full_text for kw in GENOMIC_KEYWORDS): genomic_mentions += 1
            if any(kw in full_text for kw in BIO_KEYWORDS): bio_repository_mentions += 1
            
            total_sites += len(locs)
            # Heuristic for "Interconnected" (Multi-country sites in one trial)
            countries = set([l.get("country", "") for l in locs])
            if len(countries) > 1:
                interconnected_sites += 1

        results[reg] = {
            "genomic_intensity": round((genomic_mentions / len(studies)) * 100, 1),
            "bio_repository_rate": round((bio_repository_mentions / len(studies)) * 100, 1),
            "interconnectivity_index": round((interconnected_sites / len(studies)) * 100, 1),
            "avg_sites_per_trial": round(total_sites / len(studies), 1),
            "sample_size": len(studies)
        }

    print(json.dumps(results, indent=2))
    with open("C:/AfricaRCT/data/genomic_resilience_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_genomic_audit()
