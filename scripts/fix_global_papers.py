import os
from pathlib import Path

E156_DIR = Path("C:/AfricaRCT/E156")

PAPERS = [
    {
        "id": "intra-african-disparity",
        "title": "Intra-African Disparity and Regional Fractures",
        "body": "In the mapping of African clinical research, does the sub-regional distribution of trials indicate a significant divide in local infrastructure and capacity? This audit evaluated trial volumes across five African regions using the ClinicalTrials.gov API v2 database and geographic filters through March 2026. Investigators applied a regional-disparity model and reported the intra-continental concentration ratio as the primary comparative estimand for research decentralization and internal equity. The result showed that North Africa hosts over thirteen thousand trials, completely dwarfing the Central African region which hosts fewer than three hundred studies. Egypt and South Africa maintain absolute dominance as primary hubs, while populations in West and Central Africa remain functionally excluded from clinical innovation. These findings reveal a severe internal monopoly where the continent operates as a fractured landscape of isolated centers rather than a unified network. Interpretation is limited by the reliance on national borders which may obscure the true density of research networks operating across regions.",
        "note": "- DOI: 10.156/intra-african-disparity\n- Date: 2026-03-28\n- Type: research\n- Mode: regional-audit"
    },
    {
        "id": "ethnicity-void",
        "title": "The Demographic Void and Genomic Diversity",
        "body": "In the evaluation of global clinical populations, does the reporting of race and ethnicity indicate a demographic void in the global clinical research ecosystem? This demographic audit evaluated twenty thousand African trials and one hundred fifty thousand United States trials using eligibility metadata from the ClinicalTrials.gov API v2 database. Researchers applied a demographic-resolution model and reported the ethnic-stratification reporting rate as the primary comparative estimand for genomic inclusivity and diversity tracking. The primary result revealed an absolute reporting void, with only thirty-nine African trials tracking race or ethnicity despite the continent possessing the greatest human genetic diversity. The United States also showed severe underreporting, capturing ethnic data in less than two percent of interventional research protocols. These findings suggest that precision medicine is being built on an incomplete foundation that systematically erases the vast genetic complexities of the human population. Interpretation is limited by the reliance on English-language search terms to detect ethnic stratification variables today.",
        "note": "- DOI: 10.156/ethnicity-void\n- Date: 2026-03-28\n- Type: research\n- Mode: demographic-audit"
    }
]

for p in PAPERS:
    words = p['body'].split()
    count = len(words)
    print(f"Paper {p['id']}: {count} words")
    
    if count != 156:
        print(f"  -> ERROR: {count} words. Must be exactly 156.")
    
    md_path = E156_DIR / f"{p['id']}_e156.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# {p['title'].upper()}\n\n{p['body']}\n\n## Note Block\n\n{p['note']}")
        
    try:
        with open("C:/Users/user/E156-framework/templates/e156_interactive_template.html", 'r', encoding='utf-8') as f:
            tpl = f.read()
        final_html = tpl.replace("E156 Interactive Bundle", p['title'] + " Dashboard")
        final_html = final_html.replace("Sentence 1: In [population or condition]...", p['body'])
        with open(E156_DIR / f"{p['id']}_dashboard.html", 'w', encoding='utf-8') as f:
            f.write(final_html)
    except:
        pass
