import json
import requests
import time
import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from pathlib import Path

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
REGIONS = ["Africa", "Europe", "China", "India", "South America"]

def fetch_multidimensional_data(location, count=200):
    print(f"  Fetching high-dimensional data for {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=60)
        studies = resp.json().get("studies", [])
    except: return []

    data_points = []
    for s in studies:
        proto = s.get("protocolSection", {})
        design = proto.get("designModule", {})
        outcomes = proto.get("outcomesModule", {})
        elig = proto.get("eligibilityModule", {})
        
        # Features for Clustering
        f1 = design.get("enrollmentInfo", {}).get("count", 0) # Enrollment
        f2 = len(outcomes.get("primaryOutcomes", [])) + len(outcomes.get("secondaryOutcomes", [])) # Endpoints
        f3 = len(elig.get("eligibilityCriteria", "")) # Eligibility Complexity
        f4 = 1 if design.get("designInfo", {}).get("allocation") == "RANDOMIZED" else 0 # Rigor
        
        data_points.append([f1, f2, f3, f4])
        
    return data_points

def run_cluster_audit():
    print("Initiating High-Dimensional Cluster Audit...")
    
    all_data = []
    region_labels = []
    
    for reg in REGIONS:
        points = fetch_multidimensional_data(reg)
        all_data.extend(points)
        region_labels.extend([reg] * len(points))
        
    X = np.array(all_data)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 1. PCA: What drives the variance?
    pca = PCA(n_components=2)
    X_pca = pca.fit_transform(X_scaled)
    explained_variance = pca.explained_variance_ratio_
    
    # 2. KMeans: Archetype Discovery
    kmeans = KMeans(n_clusters=3, random_state=42)
    clusters = kmeans.fit_predict(X_scaled)
    
    # 3. Regional Archetype Mapping
    archetypes = {reg: [] for reg in REGIONS}
    for i, cluster in enumerate(clusters):
        archetypes[region_labels[i]].append(cluster)
        
    results = {
        "pca_variance": [round(v, 3) for v in explained_variance],
        "archetypes": {reg: np.bincount(archetypes[reg], minlength=3).tolist() for reg in REGIONS},
        "loadings": pca.components_.tolist()
    }
    
    print(json.dumps(results, indent=2))
    with open("C:/AfricaRCT/data/high_dimensional_cluster_data.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run_cluster_audit()
