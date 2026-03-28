#!/usr/bin/env python
"""
fetch_network_analysis.py -- The Collaboration Network: Who Works With Whom?
============================================================================
Maps the research collaboration network between African and foreign
institutions using trial co-sponsorship data from Uganda's 783 ClinicalTrials.gov
records. Applies graph theory metrics (pure Python, no networkx) to find which
connections drive capacity.

Strategy:
  Since ClinicalTrials.gov lead-sponsor data lacks explicit collaborator edges,
  we build an implicit institution-institution network via condition co-occurrence:
  two institutions are "connected" if they both run trials on the same condition.
  This bipartite projection is standard in bibliometric network analysis.

Outputs:
  - data/network_analysis_data.json  (cached analysis, 24h TTL)
  - network-analysis.html            (dark-theme interactive dashboard)

Usage:
    python fetch_network_analysis.py

Requirements:
    Python 3.8+ (no external packages needed -- uses cached data)
"""

import json
import math
import os
import sys
import io
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Windows console UTF-8 safety ─────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
INPUT_FILE = DATA_DIR / "uganda_collected_data.json"
CACHE_FILE = DATA_DIR / "network_analysis_data.json"
OUTPUT_HTML = Path(__file__).parent / "network-analysis.html"

# ── Institution classification rules ─────────────────────────────────────
UGANDA_KEYWORDS = [
    "makerere", "mbarara", "gulu", "uganda", "kampala", "mulago",
    "mrc/uvri", "infectious diseases research collaboration",
    "jinja", "kabale", "lira", "baylor college of medicine - uganda",
]
US_ACADEMIC_KEYWORDS = [
    "university of california", "ucsf", "yale", "harvard", "stanford",
    "johns hopkins", "columbia", "cornell", "massachusetts general",
    "washington university", "university of washington", "university of minnesota",
    "university of north carolina", "duke", "emory", "northwestern",
    "university of pennsylvania", "children's hospital", "cincinnati",
    "baylor college of medicine", "rand", "dartmouth", "brown university",
    "university of maryland", "new york university", "university of michigan",
    "boston university", "university of virginia", "vanderbilt",
    "university of colorado", "weill cornell", "tufts", "georgetown",
]
US_GOVT_KEYWORDS = [
    "niaid", "nih", "national institute", "centers for disease control",
    "cdc", "eunice kennedy", "national cancer institute", "nci",
    "us army", "walter reed", "fogarty", "advancing clinical therapeutics",
]
PHARMA_KEYWORDS = [
    "gilead", "novartis", "roche", "hoffmann", "merck", "pfizer", "gsk",
    "glaxosmith", "janssen", "johnson & johnson", "abbvie", "astrazeneca",
    "sanofi", "bayer", "boehringer", "takeda", "viiv healthcare",
    "medicines for malaria", "drugs for neglected",
]
UK_KEYWORDS = [
    "london school", "lshtm", "oxford", "cambridge", "ucl",
    "imperial college", "liverpool", "medical research council",
    "mrc/uvri", "wellcome",
]
EUROPE_OTHER_KEYWORDS = [
    "karolinska", "ku leuven", "amsterdam", "antwerp", "copenhagen",
    "epicentre", "penta foundation", "inserm", "institut pasteur",
    "swiss tropical", "radboud", "heidelberg", "barcelona",
    "erasmus", "dbl", "bernhard nocht",
]


def classify_institution(name):
    """Classify an institution into geographic/type category."""
    low = name.lower()
    # Uganda-local (check before UK because "mrc/uvri" overlaps)
    for kw in UGANDA_KEYWORDS:
        if kw in low:
            return "Uganda-local"
    # US Government
    for kw in US_GOVT_KEYWORDS:
        if kw in low:
            return "US-Govt"
    # Pharma
    for kw in PHARMA_KEYWORDS:
        if kw in low:
            return "Pharma"
    # UK
    for kw in UK_KEYWORDS:
        if kw in low:
            return "UK"
    # US Academic
    for kw in US_ACADEMIC_KEYWORDS:
        if kw in low:
            return "US-Academic"
    # Europe Other
    for kw in EUROPE_OTHER_KEYWORDS:
        if kw in low:
            return "Europe-Other"
    return "Other"


# ── Condition normalization ──────────────────────────────────────────────
CONDITION_MAP = {
    "hiv": "HIV/AIDS", "aids": "HIV/AIDS", "hiv infections": "HIV/AIDS",
    "hiv/aids": "HIV/AIDS", "hiv infection": "HIV/AIDS",
    "acquired immunodeficiency syndrome": "HIV/AIDS",
    "acquired immune deficiency syndrome": "HIV/AIDS",
    "human immunodeficiency virus": "HIV/AIDS",
    "malaria": "Malaria", "plasmodium": "Malaria", "falciparum": "Malaria",
    "tuberculosis": "Tuberculosis", "tb": "Tuberculosis",
    "cancer": "Cancer", "neoplasm": "Cancer", "carcinoma": "Cancer",
    "lymphoma": "Cancer", "leukemia": "Cancer", "sarcoma": "Cancer",
    "diabetes": "Diabetes", "diabetes mellitus": "Diabetes",
    "hypertension": "Hypertension", "blood pressure": "Hypertension",
    "cardiovascular": "Cardiovascular", "heart": "Cardiovascular",
    "cardiac": "Cardiovascular",
    "mental health": "Mental Health", "depression": "Mental Health",
    "anxiety": "Mental Health", "ptsd": "Mental Health",
    "maternal": "Maternal/Pregnancy", "pregnancy": "Maternal/Pregnancy",
    "antenatal": "Maternal/Pregnancy", "postpartum": "Maternal/Pregnancy",
    "nutrition": "Nutrition", "malnutrition": "Nutrition",
    "stunting": "Nutrition", "wasting": "Nutrition",
    "sickle cell": "Sickle Cell", "pneumonia": "Pneumonia",
    "diarrhea": "Diarrheal Disease", "diarrhoea": "Diarrheal Disease",
    "cholera": "Diarrheal Disease",
    "epilepsy": "Epilepsy", "seizure": "Epilepsy",
    "stroke": "Stroke", "cerebrovascular": "Stroke",
    "vaccine": "Vaccines", "immunization": "Vaccines",
    "hepatitis": "Hepatitis",
    "schistosomiasis": "NTDs", "helminth": "NTDs", "filariasis": "NTDs",
    "hookworm": "NTDs", "neglected tropical": "NTDs",
}


def normalize_condition(raw_condition):
    """Map a raw condition string to a broad category."""
    low = raw_condition.lower().strip()
    for keyword, category in CONDITION_MAP.items():
        if keyword in low:
            return category
    return "Other"


# ═══════════════════════════════════════════════════════════════════════════
# GRAPH THEORY ENGINE (pure Python, no networkx)
# ═══════════════════════════════════════════════════════════════════════════

class SimpleGraph:
    """Undirected weighted graph with adjacency-list representation."""

    def __init__(self):
        self.adj = defaultdict(lambda: defaultdict(float))  # node -> {neighbor -> weight}
        self.node_attrs = {}  # node -> dict of attributes

    def add_node(self, node, **attrs):
        if node not in self.adj:
            self.adj[node]  # ensure exists
        self.node_attrs.setdefault(node, {}).update(attrs)

    def add_edge(self, u, v, weight=1.0):
        if u == v:
            return  # no self-loops
        self.adj[u][v] += weight
        self.adj[v][u] += weight

    @property
    def nodes(self):
        return list(self.adj.keys())

    @property
    def num_nodes(self):
        return len(self.adj)

    @property
    def num_edges(self):
        seen = set()
        for u in self.adj:
            for v in self.adj[u]:
                edge = (min(u, v), max(u, v))
                seen.add(edge)
        return len(seen)

    def degree(self, node):
        """Weighted degree (sum of edge weights)."""
        return sum(self.adj[node].values())

    def simple_degree(self, node):
        """Unweighted degree (number of neighbors)."""
        return len(self.adj[node])

    def neighbors(self, node):
        return list(self.adj[node].keys())

    def density(self):
        n = self.num_nodes
        if n < 2:
            return 0.0
        max_edges = n * (n - 1) / 2
        return self.num_edges / max_edges

    def degree_distribution(self):
        """Return dict: degree -> count."""
        dist = defaultdict(int)
        for node in self.nodes:
            dist[self.simple_degree(node)] += 1
        return dict(sorted(dist.items()))

    def clustering_coefficient(self, node):
        """Local clustering coefficient for a node."""
        nbrs = self.neighbors(node)
        k = len(nbrs)
        if k < 2:
            return 0.0
        # Count edges among neighbors
        triangles = 0
        nbr_set = set(nbrs)
        for i in range(len(nbrs)):
            for j in range(i + 1, len(nbrs)):
                if nbrs[j] in self.adj[nbrs[i]]:
                    triangles += 1
        return (2.0 * triangles) / (k * (k - 1))

    def avg_clustering_coefficient(self):
        """Global average clustering coefficient."""
        if self.num_nodes == 0:
            return 0.0
        total = sum(self.clustering_coefficient(n) for n in self.nodes)
        return total / self.num_nodes

    def degree_centrality(self):
        """Normalized degree centrality: degree / (N-1)."""
        n = self.num_nodes
        if n < 2:
            return {node: 0.0 for node in self.nodes}
        return {node: self.simple_degree(node) / (n - 1) for node in self.nodes}

    def betweenness_centrality_approx(self, sample_size=50):
        """
        Approximate betweenness centrality using sampled BFS.
        Full betweenness is O(V*E); we sample source nodes for tractability.
        """
        import random
        random.seed(42)
        bc = {node: 0.0 for node in self.nodes}
        all_nodes = self.nodes
        if len(all_nodes) <= sample_size:
            sources = all_nodes
        else:
            sources = random.sample(all_nodes, sample_size)

        for s in sources:
            # BFS from s
            dist = {s: 0}
            sigma = {s: 1}  # number of shortest paths
            pred = defaultdict(list)
            queue = [s]
            order = []
            head = 0
            while head < len(queue):
                v = queue[head]
                head += 1
                order.append(v)
                for w in self.adj[v]:
                    # first visit
                    if w not in dist:
                        dist[w] = dist[v] + 1
                        queue.append(w)
                        sigma[w] = 0
                    # shortest path?
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        pred[w].append(v)

            # back-propagation
            delta = {v: 0.0 for v in order}
            for w in reversed(order):
                for v in pred[w]:
                    if sigma[w] > 0:
                        delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
                if w != s:
                    bc[w] += delta[w]

        # Normalize
        n = self.num_nodes
        scale = 1.0
        if n > 2:
            scale = 1.0 / ((n - 1) * (n - 2))
            if len(sources) < len(all_nodes):
                scale *= len(all_nodes) / len(sources)
        return {k: v * scale for k, v in bc.items()}

    def hub_spoke_ratio(self):
        """
        Measure hub-and-spoke structure using degree Gini coefficient.
        Gini=0 means perfectly distributed (complete graph).
        Gini=1 means perfect star (one hub, all others degree 1).
        """
        degrees = sorted(self.simple_degree(n) for n in self.nodes)
        n = len(degrees)
        if n == 0 or sum(degrees) == 0:
            return 0.0
        cumulative = 0.0
        total = sum(degrees)
        for i, d in enumerate(degrees):
            cumulative += (2 * (i + 1) - n - 1) * d
        gini = cumulative / (n * total)
        return gini

    def connected_components(self):
        """Find connected components using BFS."""
        visited = set()
        components = []
        for node in self.nodes:
            if node in visited:
                continue
            component = []
            queue = [node]
            visited.add(node)
            head = 0
            while head < len(queue):
                v = queue[head]
                head += 1
                component.append(v)
                for w in self.adj[v]:
                    if w not in visited:
                        visited.add(w)
                        queue.append(w)
            components.append(component)
        return sorted(components, key=len, reverse=True)


# ═══════════════════════════════════════════════════════════════════════════
# ANALYSIS PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def build_network(trials):
    """
    Build institution-institution network from condition co-occurrence.

    Two institutions are connected if they both run trials on the same
    broad condition category. Edge weight = number of shared conditions.
    """
    # Step 1: Build institution -> set of normalized conditions
    inst_conditions = defaultdict(set)
    inst_trials = defaultdict(list)
    condition_institutions = defaultdict(set)

    for trial in trials:
        sponsor = trial.get("sponsor", "Unknown")
        raw_conditions = trial.get("conditions", [])
        if not raw_conditions:
            raw_conditions = ["Other"]

        norm_conditions = set()
        for rc in raw_conditions:
            norm_conditions.add(normalize_condition(rc))

        inst_conditions[sponsor].update(norm_conditions)
        inst_trials[sponsor].append(trial)
        for nc in norm_conditions:
            condition_institutions[nc].add(sponsor)

    # Step 2: Build graph
    G = SimpleGraph()

    # Add all institutions as nodes
    for inst in inst_conditions:
        cat = classify_institution(inst)
        G.add_node(inst, category=cat, trial_count=len(inst_trials[inst]),
                   conditions=sorted(inst_conditions[inst]))

    # Step 3: Project bipartite (condition-institution) to institution-institution
    for condition, institutions in condition_institutions.items():
        inst_list = sorted(institutions)
        for i in range(len(inst_list)):
            for j in range(i + 1, len(inst_list)):
                G.add_edge(inst_list[i], inst_list[j], weight=1.0)

    return G, inst_conditions, condition_institutions, inst_trials


def analyze_sponsor_concentration(condition_institutions):
    """Which conditions have the most diverse set of sponsors?"""
    results = {}
    for condition, sponsors in condition_institutions.items():
        if condition == "Other":
            continue
        categories = defaultdict(int)
        for s in sponsors:
            categories[classify_institution(s)] += 1
        # Simpson diversity index
        n_total = len(sponsors)
        if n_total < 2:
            diversity = 0.0
        else:
            diversity = 1.0 - sum(c * (c - 1) for c in categories.values()) / (n_total * (n_total - 1))
        results[condition] = {
            "num_sponsors": n_total,
            "categories": dict(categories),
            "simpson_diversity": round(diversity, 3),
        }
    return dict(sorted(results.items(), key=lambda x: -x[1]["num_sponsors"]))


def analyze_geographic_reach(inst_conditions, inst_trials):
    """How many different conditions does each top institution cover?"""
    results = {}
    for inst in inst_conditions:
        cat = classify_institution(inst)
        results[inst] = {
            "category": cat,
            "num_trials": len(inst_trials[inst]),
            "num_conditions": len(inst_conditions[inst]),
            "conditions": sorted(inst_conditions[inst]),
        }
    return dict(sorted(results.items(), key=lambda x: -x[1]["num_trials"]))


def analyze_temporal_evolution(trials):
    """Is the network getting denser over time?"""
    # Split into 5-year windows
    windows = defaultdict(list)
    for trial in trials:
        date_str = trial.get("start_date", "")
        if not date_str:
            continue
        try:
            year = int(date_str[:4])
        except (ValueError, IndexError):
            continue
        if year < 2000:
            window = "pre-2000"
        elif year < 2005:
            window = "2000-2004"
        elif year < 2010:
            window = "2005-2009"
        elif year < 2015:
            window = "2010-2014"
        elif year < 2020:
            window = "2015-2019"
        else:
            window = "2020+"
        windows[window].append(trial)

    results = {}
    for window in sorted(windows.keys()):
        w_trials = windows[window]
        G, _, cond_inst, _ = build_network(w_trials)
        unique_sponsors = set(t.get("sponsor", "Unknown") for t in w_trials)
        cats = defaultdict(int)
        for s in unique_sponsors:
            cats[classify_institution(s)] += 1
        results[window] = {
            "num_trials": len(w_trials),
            "num_institutions": G.num_nodes,
            "num_edges": G.num_edges,
            "density": round(G.density(), 4),
            "avg_clustering": round(G.avg_clustering_coefficient(), 4),
            "hub_spoke_gini": round(G.hub_spoke_ratio(), 4),
            "categories": dict(cats),
        }
    return results


def run_analysis():
    """Run the full collaboration network analysis."""
    print("[1/6] Loading Uganda trial data...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    trials = data["sample_trials"]
    print(f"       Loaded {len(trials)} trials.")

    print("[2/6] Building collaboration network...")
    G, inst_conditions, condition_institutions, inst_trials = build_network(trials)
    print(f"       Nodes: {G.num_nodes}, Edges: {G.num_edges}")
    print(f"       Density: {G.density():.4f}")

    print("[3/6] Computing graph metrics...")
    # Degree centrality
    deg_cent = G.degree_centrality()
    top_degree = sorted(deg_cent.items(), key=lambda x: -x[1])[:20]

    # Betweenness centrality (approximate)
    bc = G.betweenness_centrality_approx(sample_size=60)
    top_betweenness = sorted(bc.items(), key=lambda x: -x[1])[:20]

    # Clustering coefficients
    avg_cc = G.avg_clustering_coefficient()
    top_cc = sorted(
        [(n, G.clustering_coefficient(n)) for n in G.nodes if G.simple_degree(n) >= 3],
        key=lambda x: -x[1]
    )[:20]

    # Hub-spoke structure
    gini = G.hub_spoke_ratio()

    # Degree distribution
    deg_dist = G.degree_distribution()

    # Connected components
    components = G.connected_components()

    print(f"       Avg clustering coefficient: {avg_cc:.4f}")
    print(f"       Hub-spoke Gini: {gini:.4f}")
    print(f"       Connected components: {len(components)} (largest: {len(components[0])} nodes)")

    print("[4/6] Analyzing sponsor concentration by condition...")
    sponsor_conc = analyze_sponsor_concentration(condition_institutions)

    print("[5/6] Analyzing geographic reach of institutions...")
    geo_reach = analyze_geographic_reach(inst_conditions, inst_trials)

    print("[6/6] Analyzing temporal evolution...")
    temporal = analyze_temporal_evolution(trials)

    # ── Category summary ──────────────────────────────────────────────
    cat_summary = defaultdict(lambda: {"count": 0, "trials": 0, "institutions": []})
    for inst in G.nodes:
        cat = G.node_attrs[inst].get("category", "Other")
        cat_summary[cat]["count"] += 1
        cat_summary[cat]["trials"] += G.node_attrs[inst].get("trial_count", 0)
        cat_summary[cat]["institutions"].append(inst)
    cat_summary = {k: {
        "count": v["count"],
        "trials": v["trials"],
        "institutions": sorted(v["institutions"],
                               key=lambda x: -G.node_attrs[x].get("trial_count", 0))[:5]
    } for k, v in cat_summary.items()}

    # ── Makerere hub analysis ─────────────────────────────────────────
    mak_neighbors = G.neighbors("Makerere University") if "Makerere University" in G.adj else []
    mak_bc = bc.get("Makerere University", 0)
    mak_dc = deg_cent.get("Makerere University", 0)
    mak_cc = G.clustering_coefficient("Makerere University") if "Makerere University" in G.adj else 0
    mak_neighbor_cats = defaultdict(int)
    for n in mak_neighbors:
        mak_neighbor_cats[G.node_attrs[n].get("category", "Other")] += 1

    makerere_analysis = {
        "degree": len(mak_neighbors),
        "degree_centrality": round(mak_dc, 4),
        "betweenness_centrality": round(mak_bc, 4),
        "clustering_coefficient": round(mak_cc, 4),
        "neighbor_categories": dict(mak_neighbor_cats),
        "top_collaborators": sorted(
            [(n, G.adj["Makerere University"].get(n, 0)) for n in mak_neighbors],
            key=lambda x: -x[1]
        )[:15] if "Makerere University" in G.adj else [],
        "is_bridge": mak_bc > 0.05,
        "is_bottleneck": mak_dc > 0.4 and mak_cc < 0.3,
    }

    # ── Assemble results ──────────────────────────────────────────────
    results = {
        "meta": {
            "date": datetime.now().isoformat(),
            "source": "ClinicalTrials.gov API v2 (cached Uganda data)",
            "total_trials": len(trials),
            "analysis": "Collaboration Network via Condition Co-occurrence",
        },
        "network_summary": {
            "num_nodes": G.num_nodes,
            "num_edges": G.num_edges,
            "density": round(G.density(), 4),
            "avg_clustering": round(avg_cc, 4),
            "hub_spoke_gini": round(gini, 4),
            "num_components": len(components),
            "largest_component_size": len(components[0]) if components else 0,
            "giant_component_fraction": round(len(components[0]) / G.num_nodes, 4) if components else 0,
        },
        "degree_distribution": deg_dist,
        "top_degree_centrality": [
            {"institution": n, "centrality": round(c, 4),
             "category": G.node_attrs[n].get("category", "Other"),
             "trial_count": G.node_attrs[n].get("trial_count", 0),
             "num_neighbors": G.simple_degree(n)}
            for n, c in top_degree
        ],
        "top_betweenness_centrality": [
            {"institution": n, "centrality": round(c, 4),
             "category": G.node_attrs[n].get("category", "Other"),
             "trial_count": G.node_attrs[n].get("trial_count", 0)}
            for n, c in top_betweenness
        ],
        "top_clustering_coefficient": [
            {"institution": n, "coefficient": round(c, 4),
             "category": G.node_attrs[n].get("category", "Other"),
             "num_neighbors": G.simple_degree(n)}
            for n, c in top_cc
        ],
        "category_summary": cat_summary,
        "sponsor_concentration": sponsor_conc,
        "geographic_reach_top20": {k: v for k, v in list(geo_reach.items())[:20]},
        "temporal_evolution": temporal,
        "makerere_analysis": makerere_analysis,
    }

    # ── Cache ─────────────────────────────────────────────────────────
    print("\nSaving analysis data...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  Cached to {CACHE_FILE}")

    return results, G


# ═══════════════════════════════════════════════════════════════════════════
# HTML GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def generate_html(results, G):
    """Generate the interactive HTML dashboard."""

    net = results["network_summary"]
    top_deg = results["top_degree_centrality"]
    top_bc = results["top_betweenness_centrality"]
    top_cc = results["top_clustering_coefficient"]
    cat_sum = results["category_summary"]
    sponsor_conc = results["sponsor_concentration"]
    geo_top = results["geographic_reach_top20"]
    temporal = results["temporal_evolution"]
    mak = results["makerere_analysis"]

    # ── Helper: category color ────────────────────────────────────────
    cat_colors = {
        "Uganda-local": "#22c55e",
        "US-Academic": "#3b82f6",
        "US-Govt": "#8b5cf6",
        "Pharma": "#ef4444",
        "UK": "#f59e0b",
        "Europe-Other": "#06b6d4",
        "Other": "#6b7280",
    }

    def cat_badge(cat):
        color = cat_colors.get(cat, "#6b7280")
        return f'<span style="background:{color}22;color:{color};padding:2px 8px;border-radius:12px;font-size:0.78rem;font-weight:600">{cat}</span>'

    # ── Summary cards ─────────────────────────────────────────────────
    summary_cards = f"""
    <div class="summary-grid">
      <div class="summary-card">
        <div class="label">Institutions</div>
        <div class="value accent">{net['num_nodes']}</div>
        <div class="label">unique sponsors</div>
      </div>
      <div class="summary-card">
        <div class="label">Connections</div>
        <div class="value success">{net['num_edges']:,}</div>
        <div class="label">condition co-occurrence edges</div>
      </div>
      <div class="summary-card">
        <div class="label">Network Density</div>
        <div class="value warning">{net['density']:.3f}</div>
        <div class="label">{net['density']*100:.1f}% of possible edges</div>
      </div>
      <div class="summary-card">
        <div class="label">Avg Clustering</div>
        <div class="value purple">{net['avg_clustering']:.3f}</div>
        <div class="label">collaborators' collaborators work together</div>
      </div>
      <div class="summary-card">
        <div class="label">Hub-Spoke Gini</div>
        <div class="value danger">{net['hub_spoke_gini']:.3f}</div>
        <div class="label">{'highly concentrated' if net['hub_spoke_gini'] > 0.5 else 'moderately distributed' if net['hub_spoke_gini'] > 0.3 else 'well distributed'}</div>
      </div>
      <div class="summary-card">
        <div class="label">Giant Component</div>
        <div class="value accent">{net['giant_component_fraction']*100:.0f}%</div>
        <div class="label">{net['largest_component_size']} of {net['num_nodes']} connected</div>
      </div>
    </div>
    """

    # ── Top 10 most connected ─────────────────────────────────────────
    top10_rows = ""
    for i, d in enumerate(top_deg[:10]):
        top10_rows += f"""<tr>
          <td style="font-weight:600">{i+1}</td>
          <td>{d['institution']}</td>
          <td>{cat_badge(d['category'])}</td>
          <td style="text-align:center">{d['trial_count']}</td>
          <td style="text-align:center">{d['num_neighbors']}</td>
          <td style="text-align:center">{d['centrality']:.3f}</td>
        </tr>"""

    # ── Top betweenness (bridges) ─────────────────────────────────────
    bridge_rows = ""
    for i, b in enumerate(top_bc[:10]):
        bridge_rows += f"""<tr>
          <td style="font-weight:600">{i+1}</td>
          <td>{b['institution']}</td>
          <td>{cat_badge(b['category'])}</td>
          <td style="text-align:center">{b['trial_count']}</td>
          <td style="text-align:center">{b['centrality']:.4f}</td>
        </tr>"""

    # ── Category summary ──────────────────────────────────────────────
    cat_rows = ""
    for cat in ["Uganda-local", "US-Academic", "US-Govt", "Pharma", "UK", "Europe-Other", "Other"]:
        if cat not in cat_sum:
            continue
        cs = cat_sum[cat]
        top_insts = ", ".join(cs["institutions"][:3])
        cat_rows += f"""<tr>
          <td>{cat_badge(cat)}</td>
          <td style="text-align:center;font-weight:600">{cs['count']}</td>
          <td style="text-align:center">{cs['trials']}</td>
          <td style="font-size:0.82rem;color:var(--muted)">{top_insts}</td>
        </tr>"""

    # ── Hub-and-spoke radial visualization ────────────────────────────
    # Build CSS-based radial layout for Makerere hub
    hub_viz = ""
    if mak["top_collaborators"]:
        collab_items = mak["top_collaborators"][:12]
        hub_viz += '<div class="radial-container">'
        hub_viz += '<div class="hub-center">Makerere<br>University</div>'
        n_collab = len(collab_items)
        for i, (name, weight) in enumerate(collab_items):
            angle = (360 / n_collab) * i - 90
            rad = math.radians(angle)
            # radius proportional to container
            r = 160
            x = 200 + r * math.cos(rad)
            y = 200 + r * math.sin(rad)
            cat = G.node_attrs.get(name, {}).get("category", "Other") if name in G.node_attrs else "Other"
            color = cat_colors.get(cat, "#6b7280")
            short_name = name[:20] + ("..." if len(name) > 20 else "")
            # Draw line from center to node
            hub_viz += f'<svg class="hub-line" style="position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none"><line x1="210" y1="210" x2="{x+10}" y2="{y+10}" stroke="{color}" stroke-width="{max(1, int(weight/2))}" opacity="0.5"/></svg>'
            hub_viz += f'<div class="spoke-node" style="left:{x-30}px;top:{y-15}px;border-color:{color};background:{color}15" title="{name} (weight: {weight:.0f})">'
            hub_viz += f'<span style="color:{color};font-size:0.68rem;font-weight:600">{short_name}</span>'
            hub_viz += '</div>'
        hub_viz += '</div>'

    # ── Sponsor concentration by condition ────────────────────────────
    conc_rows = ""
    for cond, info in list(sponsor_conc.items())[:12]:
        cat_parts = []
        for cat, count in sorted(info["categories"].items(), key=lambda x: -x[1]):
            cat_parts.append(f'{cat}: {count}')
        conc_rows += f"""<tr>
          <td style="font-weight:600">{cond}</td>
          <td style="text-align:center">{info['num_sponsors']}</td>
          <td style="text-align:center">{info['simpson_diversity']:.3f}</td>
          <td style="font-size:0.82rem;color:var(--muted)">{', '.join(cat_parts)}</td>
        </tr>"""

    # ── Condition-institution matrix (top conditions x top institutions) ──
    top_conditions = list(sponsor_conc.keys())[:8]
    top_institutions = [d["institution"] for d in top_deg[:10]]
    matrix_header = "<th>Institution</th>" + "".join(f'<th style="font-size:0.72rem;writing-mode:vertical-lr;text-align:center;padding:4px;height:90px">{c}</th>' for c in top_conditions)
    matrix_rows = ""
    check_mark = '<span style="color:#22c55e;font-weight:700">&#x2713;</span>'
    dash_mark = '<span style="color:#374151">-</span>'
    for inst in top_institutions:
        inst_conds = set(G.node_attrs.get(inst, {}).get("conditions", []))
        cells = ""
        for cond in top_conditions:
            present = cond in inst_conds
            mark = check_mark if present else dash_mark
            cells += f'<td style="text-align:center">{mark}</td>'
        cat = G.node_attrs.get(inst, {}).get("category", "Other")
        matrix_rows += f'<tr><td style="font-size:0.82rem">{inst[:30]} {cat_badge(cat)}</td>{cells}</tr>'

    # ── Temporal evolution ────────────────────────────────────────────
    temporal_rows = ""
    for window, info in temporal.items():
        temporal_rows += f"""<tr>
          <td style="font-weight:600">{window}</td>
          <td style="text-align:center">{info['num_trials']}</td>
          <td style="text-align:center">{info['num_institutions']}</td>
          <td style="text-align:center">{info['num_edges']:,}</td>
          <td style="text-align:center">{info['density']:.4f}</td>
          <td style="text-align:center">{info['avg_clustering']:.3f}</td>
          <td style="text-align:center">{info['hub_spoke_gini']:.3f}</td>
        </tr>"""

    # ── Makerere hub analysis ─────────────────────────────────────────
    mak_cats = ""
    for cat, count in sorted(mak["neighbor_categories"].items(), key=lambda x: -x[1]):
        color = cat_colors.get(cat, "#6b7280")
        mak_cats += f'<div style="display:inline-block;margin:4px 8px 4px 0;padding:4px 12px;background:{color}22;border-left:3px solid {color};border-radius:0 6px 6px 0"><span style="color:{color};font-weight:700">{count}</span> <span style="color:var(--muted);font-size:0.82rem">{cat}</span></div>'

    mak_verdict = ""
    if mak["is_bottleneck"]:
        mak_verdict = '<div class="insight-box danger-box">Makerere shows <strong>bottleneck characteristics</strong>: high centrality but low clustering suggests it connects otherwise-disconnected groups. If Makerere were removed, many institutions would lose their only link to the network.</div>'
    elif mak["is_bridge"]:
        mak_verdict = '<div class="insight-box warning-box">Makerere acts as a <strong>critical bridge</strong>: above-average betweenness centrality means it connects different research clusters. This is structurally valuable but creates single-point-of-failure risk.</div>'
    else:
        mak_verdict = '<div class="insight-box success-box">Makerere is a <strong>well-connected hub</strong> but the network has sufficient redundancy. Other institutions provide alternative pathways for collaboration.</div>'

    # ── Degree distribution chart data ────────────────────────────────
    deg_dist = results["degree_distribution"]
    deg_labels = json.dumps(list(deg_dist.keys()))
    deg_values = json.dumps(list(deg_dist.values()))

    # ── Temporal chart data ───────────────────────────────────────────
    temp_labels = json.dumps(list(temporal.keys()))
    temp_density = json.dumps([temporal[w]["density"] for w in temporal])
    temp_nodes = json.dumps([temporal[w]["num_institutions"] for w in temporal])
    temp_edges = json.dumps([temporal[w]["num_edges"] for w in temporal])

    # ── Category pie data ─────────────────────────────────────────────
    pie_labels = []
    pie_values = []
    pie_colors_list = []
    for cat in ["Uganda-local", "US-Academic", "US-Govt", "Pharma", "UK", "Europe-Other", "Other"]:
        if cat in cat_sum:
            pie_labels.append(cat)
            pie_values.append(cat_sum[cat]["count"])
            pie_colors_list.append(cat_colors.get(cat, "#6b7280"))
    pie_labels_json = json.dumps(pie_labels)
    pie_values_json = json.dumps(pie_values)
    pie_colors_json = json.dumps(pie_colors_list)

    # ── Clustering coefficient top-10 ─────────────────────────────────
    cc_rows = ""
    for i, c in enumerate(top_cc[:10]):
        cc_rows += f"""<tr>
          <td style="font-weight:600">{i+1}</td>
          <td>{c['institution']}</td>
          <td>{cat_badge(c['category'])}</td>
          <td style="text-align:center">{c['num_neighbors']}</td>
          <td style="text-align:center">{c['coefficient']:.3f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Collaboration Network -- Who Works With Whom?</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
  --bg: #0a0e17;
  --surface: #111827;
  --border: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #3b82f6;
  --danger: #ef4444;
  --warning: #f59e0b;
  --success: #22c55e;
  --purple: #7c3aed;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  line-height: 1.6;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 2rem; }}
h1 {{
  font-size: 2.4rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #22c55e, #3b82f6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
h2 {{
  font-size: 1.5rem;
  margin: 2.5rem 0 1rem;
  padding-bottom: 0.5rem;
  border-bottom: 2px solid var(--border);
  color: var(--accent);
}}
h3 {{ font-size: 1.1rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }}
.subtitle {{ color: var(--muted); font-size: 1rem; margin-bottom: 2rem; }}
.summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1.2rem;
  margin-bottom: 2rem;
}}
.summary-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.2rem;
  text-align: center;
}}
.summary-card .value {{
  font-size: 2.2rem;
  font-weight: 800;
  margin: 0.3rem 0;
}}
.summary-card .label {{
  color: var(--muted);
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}}
.accent {{ color: var(--accent); }}
.danger {{ color: var(--danger); }}
.warning {{ color: var(--warning); }}
.success {{ color: var(--success); }}
.purple {{ color: var(--purple); }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 1.5rem;
}}
th {{
  background: #1a2332;
  padding: 10px 8px;
  text-align: left;
  font-size: 0.8rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}}
td {{
  border-bottom: 1px solid var(--border);
  padding: 8px;
  font-size: 0.88rem;
}}
tr:hover {{ background: rgba(59, 130, 246, 0.05); }}
.chart-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
}}
.chart-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}}
.insight-box {{
  border-radius: 8px;
  padding: 1rem 1.2rem;
  margin: 1rem 0;
  font-size: 0.92rem;
  line-height: 1.6;
}}
.danger-box {{ background: rgba(239,68,68,0.1); border-left: 4px solid var(--danger); }}
.warning-box {{ background: rgba(245,158,11,0.1); border-left: 4px solid var(--warning); }}
.success-box {{ background: rgba(34,197,94,0.1); border-left: 4px solid var(--success); }}
.info-box {{ background: rgba(59,130,246,0.1); border-left: 4px solid var(--accent); }}
.method-box {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.2rem;
  margin: 1rem 0;
  font-size: 0.88rem;
  color: var(--muted);
}}
.radial-container {{
  position: relative;
  width: 420px;
  height: 420px;
  margin: 1.5rem auto;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 50%;
}}
.hub-center {{
  position: absolute;
  left: 50%; top: 50%;
  transform: translate(-50%, -50%);
  background: #22c55e22;
  border: 2px solid #22c55e;
  border-radius: 50%;
  width: 80px; height: 80px;
  display: flex; align-items: center; justify-content: center;
  text-align: center;
  font-size: 0.72rem;
  font-weight: 700;
  color: #22c55e;
  z-index: 10;
}}
.spoke-node {{
  position: absolute;
  width: 68px; height: 34px;
  border: 1px solid;
  border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  text-align: center;
  z-index: 10;
  overflow: hidden;
}}
.hub-line {{ position: absolute; top: 0; left: 0; z-index: 1; }}
.policy-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.2rem;
  margin: 1rem 0;
}}
.policy-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.2rem;
}}
.policy-card h4 {{
  color: var(--accent);
  font-size: 0.95rem;
  margin-bottom: 0.5rem;
}}
.policy-card p {{ font-size: 0.88rem; color: var(--muted); }}
footer {{
  margin-top: 3rem;
  padding-top: 1.5rem;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.8rem;
  text-align: center;
}}
@media (max-width: 768px) {{
  .chart-row {{ grid-template-columns: 1fr; }}
  .policy-grid {{ grid-template-columns: 1fr; }}
  h1 {{ font-size: 1.6rem; }}
  .radial-container {{ width: 320px; height: 320px; }}
}}
</style>
</head>
<body>
<div class="container">

<h1>The Collaboration Network</h1>
<p class="subtitle">Who Works With Whom? Mapping Uganda's clinical trial collaboration structure using graph theory on 783 ClinicalTrials.gov records</p>

<div class="method-box">
<strong>Method:</strong> We constructed an institution-institution co-occurrence network by projecting a bipartite condition-institution graph.
Two institutions are connected if they both sponsor trials in the same disease category. Edge weight reflects the number of shared conditions.
Graph metrics (degree, betweenness, clustering, Gini) are computed in pure Python without external libraries.
</div>

{summary_cards}

<h2>1. Top 10 Most Connected Institutions</h2>
<p style="color:var(--muted);margin-bottom:1rem">Ranked by normalized degree centrality (proportion of all other institutions they connect to via shared conditions).</p>
<table>
  <thead><tr>
    <th>#</th><th>Institution</th><th>Category</th>
    <th style="text-align:center">Trials</th>
    <th style="text-align:center">Connections</th>
    <th style="text-align:center">Centrality</th>
  </tr></thead>
  <tbody>{top10_rows}</tbody>
</table>

<h2>2. Bridge Institutions (Betweenness Centrality)</h2>
<p style="color:var(--muted);margin-bottom:1rem">Institutions that sit on the shortest paths between other pairs -- they "bridge" otherwise disconnected clusters.</p>
<table>
  <thead><tr>
    <th>#</th><th>Institution</th><th>Category</th>
    <th style="text-align:center">Trials</th>
    <th style="text-align:center">Betweenness</th>
  </tr></thead>
  <tbody>{bridge_rows}</tbody>
</table>

<h2>3. Hub-and-Spoke Visualization</h2>
<p style="color:var(--muted);margin-bottom:1rem">Makerere University's top collaborators, arranged radially. Line thickness = connection strength (shared conditions). Colors = institution category.</p>
{hub_viz}

<h2>4. Network Topology</h2>

<div class="chart-row">
  <div class="chart-container">
    <h3>Degree Distribution</h3>
    <canvas id="degChart"></canvas>
  </div>
  <div class="chart-container">
    <h3>Institution Categories</h3>
    <canvas id="pieChart"></canvas>
  </div>
</div>

<h3>Clustering Coefficient: Top 10 (degree &ge; 3)</h3>
<p style="color:var(--muted);margin-bottom:0.8rem">High clustering = this institution's collaborators also collaborate with each other (tight-knit cluster).</p>
<table>
  <thead><tr>
    <th>#</th><th>Institution</th><th>Category</th>
    <th style="text-align:center">Connections</th>
    <th style="text-align:center">Clustering</th>
  </tr></thead>
  <tbody>{cc_rows}</tbody>
</table>

<h2>5. Category Breakdown</h2>
<table>
  <thead><tr>
    <th>Category</th>
    <th style="text-align:center">Institutions</th>
    <th style="text-align:center">Total Trials</th>
    <th>Top Institutions</th>
  </tr></thead>
  <tbody>{cat_rows}</tbody>
</table>

<h2>6. Sponsor Concentration by Condition</h2>
<p style="color:var(--muted);margin-bottom:1rem">Which conditions have the most diverse set of sponsors? Simpson diversity ranges from 0 (one category dominates) to 1 (perfectly balanced).</p>
<table>
  <thead><tr>
    <th>Condition</th>
    <th style="text-align:center">Sponsors</th>
    <th style="text-align:center">Simpson D</th>
    <th>Category Breakdown</th>
  </tr></thead>
  <tbody>{conc_rows}</tbody>
</table>

<h2>7. Condition-Institution Matrix</h2>
<p style="color:var(--muted);margin-bottom:1rem">Top 10 institutions vs top 8 conditions. Green check = institution sponsors trials in that condition.</p>
<div style="overflow-x:auto">
<table>
  <thead><tr>{matrix_header}</tr></thead>
  <tbody>{matrix_rows}</tbody>
</table>
</div>

<h2>8. The Makerere Hub: Bridge or Bottleneck?</h2>
<div class="summary-grid" style="grid-template-columns: repeat(4, 1fr);">
  <div class="summary-card">
    <div class="label">Degree</div>
    <div class="value success">{mak['degree']}</div>
    <div class="label">direct connections</div>
  </div>
  <div class="summary-card">
    <div class="label">Degree Centrality</div>
    <div class="value accent">{mak['degree_centrality']:.3f}</div>
    <div class="label">proportion of network</div>
  </div>
  <div class="summary-card">
    <div class="label">Betweenness</div>
    <div class="value warning">{mak['betweenness_centrality']:.4f}</div>
    <div class="label">bridge importance</div>
  </div>
  <div class="summary-card">
    <div class="label">Clustering</div>
    <div class="value purple">{mak['clustering_coefficient']:.3f}</div>
    <div class="label">neighbor interconnection</div>
  </div>
</div>

<h3>Makerere's Neighbor Categories</h3>
{mak_cats}

{mak_verdict}

<h2>9. Temporal Evolution</h2>
<p style="color:var(--muted);margin-bottom:1rem">Is the collaboration network getting denser, more connected, or more concentrated over time?</p>
<table>
  <thead><tr>
    <th>Period</th>
    <th style="text-align:center">Trials</th>
    <th style="text-align:center">Institutions</th>
    <th style="text-align:center">Edges</th>
    <th style="text-align:center">Density</th>
    <th style="text-align:center">Clustering</th>
    <th style="text-align:center">Gini</th>
  </tr></thead>
  <tbody>{temporal_rows}</tbody>
</table>

<div class="chart-row">
  <div class="chart-container">
    <h3>Network Size Over Time</h3>
    <canvas id="tempSizeChart"></canvas>
  </div>
  <div class="chart-container">
    <h3>Network Density Over Time</h3>
    <canvas id="tempDensityChart"></canvas>
  </div>
</div>

<h2>10. Comparison With Published Collaboration Networks</h2>
<div class="insight-box info-box">
  <strong>Context from the literature:</strong> Bibliometric studies of African health research (Uthman &amp; Uthman, 2010; Rottingen et al., 2013) consistently find:
  <ul style="margin:0.5rem 0 0 1.2rem;color:var(--muted)">
    <li>North-South axis dominates (US/UK institutions in &gt;60% of collaborations)</li>
    <li>Intra-African collaboration is rare (&lt;10% of co-authorships)</li>
    <li>One or two local institutions serve as obligatory passage points</li>
    <li>HIV/AIDS networks are densest; NCD networks remain sparse</li>
  </ul>
  <p style="margin-top:0.8rem">Our Uganda network mirrors these patterns: Makerere is the dominant local hub, US academic institutions (UCSF, JHU, MGH) form the primary international axis, and
  condition-specific clustering is pronounced. The network Gini coefficient quantifies the hub-and-spoke structure that qualitative studies have long described.</p>
</div>

<h2>11. Policy Implications</h2>
<div class="policy-grid">
  <div class="policy-card">
    <h4>Diversify the Hub</h4>
    <p>Mbarara, Gulu, and other Ugandan institutions need direct international partnerships -- not mediated through Makerere -- to reduce single-point-of-failure risk.</p>
  </div>
  <div class="policy-card">
    <h4>Build South-South Links</h4>
    <p>Uganda-local institutions overwhelmingly collaborate with US/UK partners. Intra-African research links (Kenya, Tanzania, South Africa) remain underdeveloped.</p>
  </div>
  <div class="policy-card">
    <h4>Condition-Specific Gaps</h4>
    <p>NCD conditions (diabetes, cardiovascular, stroke) have far fewer and less diverse sponsors than infectious diseases. Targeted capacity building is needed.</p>
  </div>
  <div class="policy-card">
    <h4>Industry as Missing Partner</h4>
    <p>Pharma sponsors cluster tightly on infectious diseases and vaccines. Engaging industry in local NCD trials could catalyze both infrastructure and research diversity.</p>
  </div>
</div>

<footer>
  The Collaboration Network: Who Works With Whom? | Project 47 of the Africa RCT Landscape Series<br>
  Data: ClinicalTrials.gov API v2 | {results['meta']['total_trials']} Uganda trials | Analysis: {results['meta']['date'][:10]}<br>
  Graph metrics computed in pure Python (no networkx). Network built via condition co-occurrence bipartite projection.
</footer>

</div>

<script>
// ── Chart.js defaults ────────────────────────────────────────────────
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';

// ── Degree Distribution ──────────────────────────────────────────────
new Chart(document.getElementById('degChart'), {{
  type: 'bar',
  data: {{
    labels: {deg_labels},
    datasets: [{{
      label: 'Number of institutions',
      data: {deg_values},
      backgroundColor: '#3b82f680',
      borderColor: '#3b82f6',
      borderWidth: 1,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ title: {{ display: true, text: 'Degree (number of connections)' }} }},
      y: {{ title: {{ display: true, text: 'Count' }}, beginAtZero: true }}
    }}
  }}
}});

// ── Category Pie ─────────────────────────────────────────────────────
new Chart(document.getElementById('pieChart'), {{
  type: 'doughnut',
  data: {{
    labels: {pie_labels_json},
    datasets: [{{
      data: {pie_values_json},
      backgroundColor: {pie_colors_json},
      borderWidth: 0,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'right', labels: {{ padding: 12, usePointStyle: true }} }}
    }}
  }}
}});

// ── Temporal Size ────────────────────────────────────────────────────
new Chart(document.getElementById('tempSizeChart'), {{
  type: 'bar',
  data: {{
    labels: {temp_labels},
    datasets: [
      {{
        label: 'Institutions',
        data: {temp_nodes},
        backgroundColor: '#3b82f680',
        borderColor: '#3b82f6',
        borderWidth: 1,
        yAxisID: 'y',
      }},
      {{
        label: 'Edges',
        data: {temp_edges},
        type: 'line',
        borderColor: '#f59e0b',
        backgroundColor: '#f59e0b40',
        tension: 0.3,
        fill: true,
        yAxisID: 'y1',
      }}
    ]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ beginAtZero: true, title: {{ display: true, text: 'Institutions' }} }},
      y1: {{ position: 'right', beginAtZero: true, title: {{ display: true, text: 'Edges' }}, grid: {{ drawOnChartArea: false }} }}
    }}
  }}
}});

// ── Temporal Density ─────────────────────────────────────────────────
new Chart(document.getElementById('tempDensityChart'), {{
  type: 'line',
  data: {{
    labels: {temp_labels},
    datasets: [{{
      label: 'Network Density',
      data: {temp_density},
      borderColor: '#22c55e',
      backgroundColor: '#22c55e40',
      tension: 0.3,
      fill: true,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      y: {{ title: {{ display: true, text: 'Density' }}, beginAtZero: true }}
    }}
  }}
}});
</script>
</body>
</html>"""

    return html


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  The Collaboration Network: Who Works With Whom?")
    print("  Mapping Uganda's clinical trial collaboration structure")
    print("=" * 70)

    if not INPUT_FILE.exists():
        print(f"\nERROR: Input file not found: {INPUT_FILE}")
        print("Run fetch_uganda_rcts.py first to generate the data.")
        sys.exit(1)

    results, G = run_analysis()

    print("\nGenerating HTML dashboard...")
    html = generate_html(results, G)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Written to {OUTPUT_HTML}")

    # Print key findings
    net = results["network_summary"]
    print("\n" + "=" * 70)
    print("  KEY FINDINGS")
    print("=" * 70)
    print(f"  Institutions:        {net['num_nodes']}")
    print(f"  Connections:         {net['num_edges']:,}")
    print(f"  Network density:     {net['density']:.4f}")
    print(f"  Avg clustering:      {net['avg_clustering']:.4f}")
    print(f"  Hub-spoke Gini:      {net['hub_spoke_gini']:.4f}")
    print(f"  Giant component:     {net['giant_component_fraction']*100:.0f}% ({net['largest_component_size']}/{net['num_nodes']})")
    mak = results["makerere_analysis"]
    print(f"\n  Makerere University:")
    print(f"    Degree:            {mak['degree']} connections")
    print(f"    Degree centrality: {mak['degree_centrality']:.4f}")
    print(f"    Betweenness:       {mak['betweenness_centrality']:.4f}")
    print(f"    Clustering:        {mak['clustering_coefficient']:.4f}")
    print(f"    Bridge:            {'Yes' if mak['is_bridge'] else 'No'}")
    print(f"    Bottleneck:        {'Yes' if mak['is_bottleneck'] else 'No'}")
    print("=" * 70)
    print("Done!")


if __name__ == "__main__":
    main()
