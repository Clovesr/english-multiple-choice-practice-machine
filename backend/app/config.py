from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "backend" / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
QUESTION_BANK_DIR = DATA_DIR / "question_banks"
DATABASE_PATH = DATA_DIR / "question_bank.db"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
BUNDLED_RESOURCE_DIR = ROOT_DIR / "backend" / "resources"
BUNDLED_DICTIONARY_PATH = BUNDLED_RESOURCE_DIR / "ecdict-essential.dbpkg"


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    QUESTION_BANK_DIR.mkdir(parents=True, exist_ok=True)
