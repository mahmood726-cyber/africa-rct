import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
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
    REPO_ROOT / "scripts" / "experimental_math_analysis.py",
    REPO_ROOT / "scripts" / "forensic_audit.py",
    REPO_ROOT / "scripts" / "fluid_dynamics_analysis.py",
    REPO_ROOT / "scripts" / "unseen_audit.py",
]


def test_submission_config_uses_repo_relative_root():
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert payload["path"] == ".."
    assert (CONFIG_PATH.parent / payload["path"]).resolve() == REPO_ROOT.resolve()


def test_release_assets_do_not_reference_local_africarct_root():
    for path in PORTABLE_FILES:
        text = path.read_text(encoding="utf-8")
        assert r"C:\AfricaRCT" not in text, path
        assert "C:/AfricaRCT" not in text, path
