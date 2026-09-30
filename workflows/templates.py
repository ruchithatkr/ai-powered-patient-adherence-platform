"""Approved template library with locale support. A locale is usable only if its status is APPROVED."""

import re
import string
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

ALLOWED_FIELDS = {"first_name", "medication_label", "time", "kb_text"}


class LocaleTemplate(BaseModel):
    status: Literal["APPROVED", "DRAFT"]
    approval_record: str
    body: str


class StandingApproval(BaseModel):
    granted: bool
    record: str | None = None


class Template(BaseModel):
    id: str = ""
    version: str
    kb: str | None
    high_touch: bool
    standing_approval: StandingApproval
    locales: dict[str, LocaleTemplate]

    def approved_locale(self, language: str) -> LocaleTemplate | None:
        loc = self.locales.get(language)
        return loc if loc and loc.status == "APPROVED" else None


class TemplateLibrary:
    def __init__(self, library_version: str, templates: dict[str, Template]) -> None:
        self.version = library_version
        self.templates = templates
        for tpl in templates.values():  # personalisation is restricted to approved fields only
            for loc in tpl.locales.values():
                fields = {f for _, f, _, _ in string.Formatter().parse(loc.body) if f}
                if fields - ALLOWED_FIELDS:
                    raise ValueError(f"{tpl.id}: unapproved personalisation fields {fields - ALLOWED_FIELDS}")

    @classmethod
    def load(cls, path: Path) -> "TemplateLibrary":
        raw = yaml.safe_load(path.read_text())
        templates = {}
        for tid, body in raw["templates"].items():
            templates[tid] = Template(id=tid, **body)
        return cls(str(raw["library_version"]), templates)

    def get(self, template_id: str) -> Template | None:
        return self.templates.get(template_id)

    @staticmethod
    def safe_first_name(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-zÀ-ɏ'-]", "", name)[:40]
        return cleaned or "there"
