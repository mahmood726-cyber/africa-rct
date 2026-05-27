import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
CONFIG_PATH = REPO_ROOT / "e156-submission" / "config.json"
PORTABLE_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "index.html",
    REPO_ROOT / "novel-analysis-extraction-index.html",
    REPO_ROOT / "e156-submission" / "assets" / "index.html",
    REPO_ROOT / "e156-submission" / "assets" / "novel-analysis-extraction-index.html",
    REPO_ROOT / "scripts" / "fetch_admin_view.py",
    REPO_ROOT / "scripts" / "fetch_nurse_view.py",
    REPO_ROOT / "scripts" / "fetch_government_view.py",
    REPO_ROOT / "scripts" / "fetch_doctor_view.py",
    REPO_ROOT / "scripts" / "fetch_extraction_index.py",
    REPO_ROOT / "scripts" / "run_all.py",
    REPO_ROOT / "scripts" / "fetch_comparison_trials.py",
    REPO_ROOT / "scripts" / "sentinel_hidden_audit.py",
    REPO_ROOT / "scripts" / "sentinel_sponsor_equity.py",
    REPO_ROOT / "scripts" / "visualize_research_deserts.py",
    REPO_ROOT / "scripts" / "advanced_stats_analysis.py",
    REPO_ROOT / "scripts" / "cluster_audit.py",
    REPO_ROOT / "scripts" / "experimental_math_analysis.py",
    REPO_ROOT / "scripts" / "forty_angles_audit.py",
    REPO_ROOT / "scripts" / "forensic_audit.py",
    REPO_ROOT / "scripts" / "fluid_dynamics_analysis.py",
    REPO_ROOT / "scripts" / "generate_advanced_review.py",
    REPO_ROOT / "scripts" / "gods_eye_meta_synthesis.py",
    REPO_ROOT / "scripts" / "global_panoramic_audit.py",
    REPO_ROOT / "scripts" / "green_shoots_audit.py",
    REPO_ROOT / "scripts" / "hegemony_audit.py",
    REPO_ROOT / "scripts" / "multi_perspective_review.py",
    REPO_ROOT / "scripts" / "pi_audit.py",
    REPO_ROOT / "scripts" / "planetary_bloc_audit.py",
    REPO_ROOT / "scripts" / "power_audit.py",
    REPO_ROOT / "scripts" / "solutions_audit.py",
    REPO_ROOT / "scripts" / "unseen_audit.py",
    REPO_ROOT / "scripts" / "who_alignment_audit.py",
]

for pattern in ("fix_*.py", "generate_*.py", "final_*fix.py"):
    PORTABLE_FILES.extend(sorted(SCRIPT_DIR.glob(pattern), key=lambda path: str(path).lower()))

PORTABLE_FILES.append(SCRIPT_DIR / "master_E156_generator.py")
PORTABLE_FILES = list(dict.fromkeys(PORTABLE_FILES))


def test_submission_config_uses_repo_relative_root():
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert payload["path"] == ".."
    assert (CONFIG_PATH.parent / payload["path"]).resolve() == REPO_ROOT.resolve()


def test_release_assets_do_not_reference_local_africarct_root():
    for path in PORTABLE_FILES:
        text = path.read_text(encoding="utf-8")
        assert r"C:\AfricaRCT" not in text, path
        assert "C:/AfricaRCT" not in text, path
