from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
E156_DIR = REPO_ROOT / "E156"
TEMPLATES_DIR = REPO_ROOT / "templates"

E156_DIR.mkdir(parents=True, exist_ok=True)


def repo_file(*parts):
    return REPO_ROOT.joinpath(*parts)


def data_file(*parts):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR.joinpath(*parts)


def e156_file(*parts):
    E156_DIR.mkdir(parents=True, exist_ok=True)
    return E156_DIR.joinpath(*parts)


def template_file(*parts):
    return TEMPLATES_DIR.joinpath(*parts)
