"""Выгружает OpenAPI-спецификацию приложения в openapi.yaml (лаба 2). python scripts/export_openapi.py"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.main import app_factory  # noqa: E402

if __name__ == "__main__":
    spec = app_factory(Settings(database_url="sqlite://", log_level="ERROR")).openapi()
    text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=110)
    (ROOT / "openapi.yaml").write_bytes(text.encode("utf-8"))  # LF и UTF-8 на любой ОС
    print(f"openapi.yaml: {len(spec['paths'])} paths")
