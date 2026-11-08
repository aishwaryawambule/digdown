from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Project(BaseModel):
    name: str = ""  # compose project name (top-level `name:`), if declared
    services: list[str] = Field(default_factory=list)
    depends_on: dict[str, list[str]] = Field(default_factory=dict)


def _parse_depends_on(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict):
        return list(raw.keys())
    return []


def discover_project(compose_path: str) -> Project:
    p = Path(compose_path)
    if not p.exists():
        return Project()
    data = yaml.safe_load(p.read_text()) or {}
    services_block = data.get("services", {}) or {}
    services = list(services_block.keys())
    depends_on = {
        name: _parse_depends_on((spec or {}).get("depends_on"))
        for name, spec in services_block.items()
    }
    return Project(name=data.get("name", "") or "", services=services,
                   depends_on=depends_on)
