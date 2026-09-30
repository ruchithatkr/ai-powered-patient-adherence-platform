"""Typed runtime settings (env prefix ADHERENCE_). No secrets are required to run locally."""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADHERENCE_", env_file=".env", extra="ignore")

    environment: str = "local"
    policy_path: Path = ROOT / "workflows" / "policy.yaml"
    templates_path: Path = ROOT / "workflows" / "templates.yaml"
    mcp_config_path: Path = ROOT / "mcp" / "servers.yaml"
    kb_path: Path = ROOT / "mcp" / "fixtures" / "knowledge_base.yaml"
    audit_path: Path | None = ROOT / "var" / "audit.jsonl"
    drift_baseline_path: Path | None = None
    otel_console: bool = False
    log_level: str = "INFO"
    synthetic_data_only: bool = True
    allow_real_phi: bool = False
    eval_signing_key: str | None = None

    @model_validator(mode="after")
    def _no_real_phi(self) -> "Settings":
        if self.allow_real_phi or not self.synthetic_data_only:
            raise ValueError("Real PHI mode is forbidden in this build (synthetic data only).")
        return self
