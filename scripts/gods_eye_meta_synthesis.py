import json
import os
from pathlib import Path
import numpy as np

DATA_DIR = Path("C:/AfricaRCT/data")

def run_meta_synthesis():
    print("Initiating God's Eye View Meta-Synthesis...")
    
    # Load primary data blocks
    with open(DATA_DIR / "global_panoramic_data.json", 'r') as f: pano = json.load(f)
    with open(DATA_DIR / "quantum_complexity_data.json", 'r') as f: quantum = json.load(f)
    with open(DATA_DIR / "value_chain_audit_data.json", 'r') as f: value = json.load(f)
    with open(DATA_DIR / "biological_sovereignty_data.json", 'r') as f: bio = json.load(f)
    with open(DATA_DIR / "information_topology_data.json", 'r') as f: info = json.load(f)

    # 1. The Planetary Singularity (Concentration of Power)
    # Defined as (Global Volume / Global Nations) * Sovereignty
    us_vol = pano['global']['United States']
    global_vol = sum(pano['global'].values())
    singularity_index = (us_vol / global_vol) * 100
    
    # 2. The Cognitive Deficit (Genetic Diversity vs Genomic Research)
    # Africa has the highest genetic diversity but low genomic research intensity
    af_genomic = bio['Africa']['extraction_rate'] # Proxy for biological focus
    # We use the discrepancy between population and discovery leadership
    cognitive_deficit = 100 - value['Africa']['local_leadership_rate']
    
    # 3. Unified Inequity Score (UIS)
    # A composite of Volume, Leadership, and Innovation Gaps
    # Normalizing 0-100
    uis_africa = ( (1 - (pano['africa_regions']['North'] + pano['africa_regions']['South']) / us_vol) * 0.4 + 
                   (value['Africa']['foreign_governance_rate'] / 100) * 0.4 + 
                   (1 - bio['Africa']['innovation_symmetry']) * 0.2 ) * 100

    results = {
        "planetary_singularity": round(singularity_index, 1),
        "cognitive_deficit": round(cognitive_deficit, 1),
        "unified_inequity_score_africa": round(uis_africa, 1),
        "global_research_volume": global_vol,
        "informational_distance_meta": info['Africa_Europe_KLD']
    }
    
    print(json.dumps(results, indent=2))
    with open(DATA_DIR / "gods_eye_meta_synthesis.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_meta_synthesis()
