import os
from pathlib import Path
from datetime import datetime

OUTPUT_HTML = Path("C:/AfricaRCT/advanced_multi_persona_review.html")

html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Phase II: Advanced Multi-Persona Critical Review</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Merriweather:ital,wght@0,300;0,700;1,300&family=Roboto+Mono&display=swap');
        body {{ background: #faf9f6; color: #2c3e50; font-family: 'Merriweather', serif; padding: 50px; line-height: 1.8; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        h1 {{ font-size: 2.8em; border-bottom: 2px solid #e74c3c; padding-bottom: 15px; margin-bottom: 40px; color: #c0392b; }}
        .persona {{ background: #fff; padding: 30px; border-left: 5px solid #34495e; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 30px; }}
        .persona h2 {{ font-family: 'Roboto Mono', monospace; font-size: 1.2em; color: #2980b9; margin-top: 0; text-transform: uppercase; }}
        .quote {{ font-style: italic; font-size: 1.1em; color: #7f8c8d; padding-left: 15px; border-left: 3px solid #bdc3c7; margin-bottom: 20px; }}
        .critique {{ font-size: 0.95em; }}
        .action-plan {{ background: #2c3e50; color: #ecf0f1; padding: 40px; margin-top: 60px; border-radius: 8px; }}
        .action-plan h2 {{ color: #e74c3c; font-family: 'Roboto Mono', monospace; font-size: 1.5em; margin-top: 0; }}
        .action-list li {{ margin-bottom: 15px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Phase II: Advanced Critical Review</h1>
        <p>A secondary stress-test of our 63-dimension audit, bringing in new expert perspectives to identify blind spots in the global clinical research analysis.</p>

        <div class="persona">
            <h2>Persona I: The Global Bioethicist</h2>
            <div class="quote">"You measured the extraction of data and biology, but what about the human aftermath? Do African patients get the drug after the trial ends?"</div>
            <div class="critique">
                <strong>Critique:</strong> The analysis thus far has ignored <strong>Post-Trial Access (PTA)</strong> and <strong>Expanded Access</strong> (Compassionate Use). If a trial is successful, ethical guidelines mandate that participants should have continued access to the intervention. If the Global North is running trials in the South but not offering Expanded Access, it's the ultimate form of ethical extraction.
            </div>
        </div>

        <div class="persona">
            <h2>Persona II: The Data Ontologist</h2>
            <div class="quote">"You counted fields and variables, but did you measure the 'Epistemic Care' taken to fill out the registry?"</div>
            <div class="critique">
                <strong>Critique:</strong> We need to measure the <strong>Epistemic Completeness</strong> of the trial registrations. Are sponsors from the North filling out the optional metadata modules (like IPD Sharing descriptions, Oversight details, and Reference links) when conducting trials in Africa with the same rigor as they do in Europe? Or is there a "Data Care Gap"?
            </div>
        </div>

        <div class="action-plan">
            <h2>Next Evolution: The Ethical & Epistemic Audit</h2>
            <p>Based on these critiques, we must push the analysis further into the moral and epistemic architecture of the data:</p>
            <ul class="action-list">
                <li><strong>Metric 1: The Compassionate Use Gap.</strong> Quantify the availability of "Expanded Access" for interventions tested in Africa vs Europe.</li>
                <li><strong>Metric 2: Epistemic Completeness Index.</strong> Calculate a score based on the presence of non-mandatory metadata modules to measure the "care" taken in registration.</li>
            </ul>
            <p><em>Executing final Python probes to extract these hidden realities...</em></p>
        </div>
    </div>
</body>
</html>
"""

with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"Phase II Review Generated: {OUTPUT_HTML}")
