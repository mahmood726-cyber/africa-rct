from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"


def repo_file(*parts):
    return REPO_ROOT.joinpath(*parts)


def data_file(*parts):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR.joinpath(*parts)
