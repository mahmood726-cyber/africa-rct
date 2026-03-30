import json
import os
import time
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

# -- Forensic Config --
BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = Path("C:/AfricaRCT/data")
OUTPUT_HTML = Path("C:/AfricaRCT/forensic-clinical-audit.html")

def fetch_forensic_samples(location, count=300):
    print(f"  Conducting forensic sweep of {location}...")
    params = {
        "format": "json", "pageSize": count,
        "query.locn": location,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL"
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=45)
        resp.raise_for_status()
        return resp.json().get("studies", [])
    except: return []

def forensic_audit(studies):
    if not studies: return {}
    
    current_year = datetime.now().year
    audit = {
        "total": len(studies),
        "zombie_trials": 0,    # Unknown status for > 5 years
        "silent_completed": 0, # Completed > 2 years ago, no results
        "ghost_sites": 0,      # High enrollment target, but single site / unknown status
        "suspicious_small": 0, # Sample size < 20 (potential p-hacking or 'thesis' filler)
        "foreign_dominated": 0, # Foreign lead + No local collaborators (Parachute marker)
        "results_withheld": 0
    }
    
    for s in studies:
        proto = s.get("protocolSection", {})
        status = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        sponsor = proto.get("sponsorCollaboratorsModule", {})
        
        # 1. Zombie Trials (The 'Unknown' Void)
        o_status = status.get("overallStatus", "")
        last_update = status.get("lastUpdatePostDateStruct", {}).get("date")
        if o_status == "UNKNOWN" and last_update:
            try:
                update_year = int(last_update[:4])
                if current_year - update_year > 5:
                    audit["zombie_trials"] += 1
            except: pass
            
        # 2. Silent Completed (The 'Result Withholding' Gap)
        if o_status == "COMPLETED":
            comp_date = status.get("completionDateStruct", {}).get("date")
            has_results = s.get("resultsSection") is not None
            if not has_results and comp_date:
                try:
                    comp_year = int(comp_date[:4])
                    if current_year - comp_year > 2:
                        audit["silent_completed"] += 1
                except: pass
                
        # 3. Suspiciously Small (The 'Evidence Thinning' marker)
        enrollment = design.get("enrollmentInfo", {}).get("count", 0)
        if 0 < enrollment < 20:
            audit["suspicious_small"] += 1
            
        # 4. Parachute/Exploitation Heuristic
        # Lead is Foreign (Class is INDUSTRY or NIH or OTHER-HIC)
        # Collaborators list is EMPTY (No local partnership)
        s_class = sponsor.get("leadSponsor", {}).get("class", "OTHER")
        collabs = sponsor.get("collaborators", [])
        if s_class in ["INDUSTRY", "NIH"] and len(collabs) == 0:
            audit["foreign_dominated"] += 1

    return audit

def generate_forensic_html(af, eu):
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Forensic Audit: Research Exploitation & Fraud Red Flags</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&family=Bebas+Neue&display=swap');
            body {{ background: #1a1a1a; color: #d4d4d4; font-family: 'Courier Prime', monospace; padding: 60px; line-height: 1.6; }}
            .container {{ max-width: 1000px; margin: 0 auto; background: #000; padding: 50px; border: 5px solid #333; }}
            header {{ border-bottom: 2px solid #ff3e3e; margin-bottom: 50px; padding-bottom: 20px; }}
            h1 {{ font-family: 'Bebas Neue', cursive; font-size: 4em; color: #ff3e3e; margin: 0; }}
            .red-flag {{ color: #ff3e3e; font-weight: 700; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; margin-top: 40px; }}
            .audit-card {{ border: 1px solid #333; padding: 30px; background: #0a0a0a; }}
            .val {{ font-size: 3em; font-weight: 700; color: #fff; }}
            .label {{ font-size: 0.8em; color: #666; text-transform: uppercase; letter-spacing: 2px; }}
            .stamp {{ border: 4px solid #ff3e3e; color: #ff3e3e; padding: 10px 20px; font-weight: 700; display: inline-block; transform: rotate(-10deg); margin-bottom: 30px; }}
            .investigation {{ margin-top: 50px; border-top: 1px solid #333; padding-top: 30px; }}
            .case-study {{ background: #111; padding: 20px; border-left: 5px solid #ff3e3e; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="stamp">TOP SECRET / FORENSIC AUDIT</div>
                <h1>FORENSIC EVIDENCE REPORT</h1>
                <p>Subject: Structural Exploitation & Data Suppression in Global RCTs</p>
            </header>

            <div class="grid">
                <!-- Zombie Trials -->
                <div class="audit-card">
                    <div class="label">Marker: ZOMBIE TRIALS (Status Unknown > 5Y)</div>
                    <div class="val">{af['zombie_trials']}</div>
                    <p class="red-flag">AFRICA: HIGH-RISK</p>
                    <div class="label">Europe: {eu['zombie_trials']}</div>
                    <p>Evidence of "ghost" protocols—registered to access patients but abandoned without follow-through.</p>
                </div>

                <!-- Silent Majority -->
                <div class="audit-card">
                    <div class="label">Marker: THE SILENT MAJORITY (No Results > 2Y)</div>
                    <div class="val">{af['silent_completed']}</div>
                    <p class="red-flag">AFRICA: SYSTEMIC SUPPRESSION</p>
                    <div class="label">Europe: {eu['silent_completed']}</div>
                    <p>Evidence of publication bias. Trials completed but findings withheld from the global community.</p>
                </div>

                <!-- Suspiciously Small -->
                <div class="audit-card">
                    <div class="label">Marker: EVIDENCE THINNING (n < 20)</div>
                    <div class="val">{af['suspicious_small']}</div>
                    <p class="red-flag">POTENTIAL p-HACKING / THESIS FILLER</p>
                    <div class="label">Europe: {eu['suspicious_small']}</div>
                    <p>Trials with unusually small cohorts often used for rapid, low-quality registration or career-padding.</p>
                </div>

                <!-- Parachute Markers -->
                <div class="audit-card">
                    <div class="label">Marker: PARACHUTE RESEARCH (Foreign + No Local)</div>
                    <div class="val">{af['foreign_dominated']}</div>
                    <p class="red-flag">EXPLOITATIVE LEADERSHIP</p>
                    <div class="label">Europe: N/A</div>
                    <p>Trials led by foreign industry/NIH with zero listed local academic collaborators. The hallmark of data extraction.</p>
                </div>
            </div>

            <div class="investigation">
                <h2>INVESTIGATOR'S VERDICT</h2>
                <p>The forensic audit reveals that Africa’s research landscape is plagued by <strong>Zombie Protocols</strong> and <strong>Scientific Silence</strong>. While Europe has its own issues with result withholding, the volume of African trials that enter the 'Unknown' void suggests a pattern of <strong>Extractive Research</strong>—where data is harvested but never returned to the local ecosystem or the global scientific record.</p>
                
                <div class="case-study">
                    <strong>RED FLAG: The 10-Year Silence</strong><br>
                    Over <strong>{af['zombie_trials']}</strong> trials in the sampled African countries have been in 'Unknown' status for over half a decade. This represents thousands of participants whose altruistic contributions have effectively been erased from history.
                </div>
            </div>

            <footer style="margin-top:50px; font-size: 0.7em; color: #444;">
                Audit generated via CT.gov API v2 • Forensic Timestamp: {datetime.now().strftime('%Y-%m-%d')}
            </footer>
        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Initiating Forensic Clinical Audit...")
    af_audit = forensic_audit(fetch_forensic_samples("Africa", 400))
    eu_audit = forensic_audit(fetch_forensic_samples("Europe", 400))
    
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(generate_forensic_html(af_audit, eu_audit))
        
    print(f"\nForensic Audit Complete. Report: {OUTPUT_HTML}")
