"""Runtime settings, read once from environment variables (and a local .env file if present)."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional in production, handy locally
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    # Comma-separated fallback chain: the next model is tried on 429/5xx/timeouts.
    gemini_models: str = os.getenv("GEMINI_MODELS", "gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite").strip()
    gemini_thinking: str = os.getenv("GEMINI_THINKING", "low").strip()
    # auto = Gemini when a key is set, otherwise the offline stub model
    llm_provider: str = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./agent_lab.db").strip()

    max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "10"))
    max_tokens_per_run: int = int(os.getenv("AGENT_MAX_TOKENS", "60000"))
    max_draft_attempts: int = int(os.getenv("AGENT_MAX_DRAFT_ATTEMPTS", "2"))
    recent_contact_days: int = int(os.getenv("RECENT_CONTACT_DAYS", "30"))

    runs_per_hour_per_ip: int = int(os.getenv("RUNS_PER_HOUR_PER_IP", "40"))
    evals_per_hour: int = int(os.getenv("EVALS_PER_HOUR", "6"))
    admin_token: str = os.getenv("ADMIN_TOKEN", "").strip()

    # USD per 1M tokens, used only for the cost estimate shown in traces
    price_in_per_m: float = float(os.getenv("PRICE_IN_PER_M", "0.30"))
    price_out_per_m: float = float(os.getenv("PRICE_OUT_PER_M", "2.50"))

    @property
    def models(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    @property
    def provider(self) -> str:
        if self.llm_provider == "stub":
            return "stub"
        if self.llm_provider == "gemini" or self.gemini_api_key:
            return "gemini" if self.gemini_api_key else "stub"
        return "stub"


settings = Settings()


def load_json(name: str):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


LEADS = {lead["id"]: lead for lead in load_json("leads.json")}
COMPANIES = load_json("companies.json")
SELLER = load_json("seller.json")
