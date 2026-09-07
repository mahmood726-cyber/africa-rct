import json

from repo_paths import repo_file

def create_viz():
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise SystemExit(
            "plotly is required for visualize_research_deserts.py. Install it with `python -m pip install plotly`."
        ) from exc

    # Load data
    with repo_file("sentinel_comparison_results.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    
    digits = [str(d) for d in range(1, 10)]
    expected = [data["africa"]["enrollment"]["expected_dist"][d] for d in digits]
    africa_obs = [data["africa"]["locations_count"]["observed_dist"][d] for d in digits]
    europe_obs = [data["europe_germany"]["locations_count"]["dist"][d] for d in digits]
    
    # Create figure
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Africa: Location Count vs Benford", "Europe (Germany): Location Count vs Benford"))
    
    # Africa
    fig.add_trace(go.Bar(x=digits, y=africa_obs, name="Africa Observed", marker_color='firebrick'), row=1, col=1)
    fig.add_trace(go.Scatter(x=digits, y=expected, name="Benford Expected", line=dict(color='black', dash='dash')), row=1, col=1)
    
    # Europe
    fig.add_trace(go.Bar(x=digits, y=europe_obs, name="Europe Observed", marker_color='royalblue'), row=1, col=2)
    fig.add_trace(go.Scatter(x=digits, y=expected, name="Benford Expected", line=dict(color='black', dash='dash'), showlegend=False), row=1, col=2)
    
    fig.update_layout(
        title_text="Forensic Audit: Geographic Clustering (Research Deserts)",
        template="plotly_white",
        height=500,
        showlegend=True
    )
    
    output_path = repo_file("research_deserts_viz.html")
    fig.write_html(str(output_path))
    print(f"Visualization saved to {output_path}")

if __name__ == "__main__":
    create_viz()
