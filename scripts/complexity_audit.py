import json
import requests
import time
import numpy as np
import networkx as nx
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India"]

def fetch_network_metadata(location, count=250):
    print(f"  Performing topological probe for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        return resp.json().get("studies", [])
    except: return []

def run_complexity_audit():
    print("Initiating High-Complexity Quantum Audit...")
    
    results = {}
    for reg in REGIONS:
        studies = fetch_network_metadata(reg)
        if not studies: continue
        
        # 1. Build Collaboration Graph
        G = nx.Graph()
        for s in studies:
            proto = s.get("protocolSection", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            lead = sponsor_mod.get("leadSponsor", {}).get("name", "Unknown")
            collabs = [c.get("name", "Unknown") for c in sponsor_mod.get("collaborators", [])]
            
            for c in collabs:
                G.add_edge(lead, c)

        # 2. Graph Metrics
        # Entropy of the degree distribution
        degrees = [d for n, d in G.degree()]
        if not degrees:
            entropy = 0
            centrality = 0
        else:
            probs = np.bincount(degrees) / len(degrees)
            probs = probs[probs > 0]
            entropy = -np.sum(probs * np.log2(probs))
            
            try:
                centrality_dict = nx.eigenvector_centrality_numpy(G)
                centrality = np.mean(list(centrality_dict.values()))
            except:
                centrality = 0

        # 3. Clustering Coefficient (Global)
        clustering = nx.average_clustering(G) if len(G) > 0 else 0

        results[reg] = {
            "network_entropy": round(float(entropy), 3),
            "eigenvector_centrality": round(float(centrality), 4),
            "clustering_coefficient": round(float(clustering), 3),
            "node_count": len(G.nodes()),
            "edge_count": len(G.edges())
        }

    print(json.dumps(results, indent=2))
    with open("C:/AfricaRCT/data/quantum_complexity_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_complexity_audit()
