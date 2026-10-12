"""Runtime settings, read from the environment and `.env`."""

from functools import lru_cache

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPrice(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache writes


# Anthropic first-party list prices (verify at https://platform.claude.com/docs/en/about-claude/pricing).
# Override with the PRICES env var (JSON) if they change.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-opus-5-5": ModelPrice(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://pipeline:pipeline@localhost:5432/pipeline"
    anthropic_api_key: SecretStr | None = None
    # Needed only for API keys that aren't scoped to a workspace: the API then requires the
    # anthropic-workspace-id header to say which workspace to use (and bill).
    anthropic_workspace_id: str | None = None

    # Extraction (D-023..D-027)
    extract_model: str = "claude-haiku-5-5"
    escalation_model: str = "claude-opus-5-5"
    extract_effort: str = "medium"
    escalation_effort: str = "medium"
    extract_max_tokens: int = 16000
    confidence_threshold: float = 0.6  # required field below this -> escalate
    grounding_fail_ratio: float = 0.34  # share of ungrounded fields that triggers escalation
    extract_workers: int = 8  # concurrent API calls
    escalation_fallbacks: bool = True  # server-side refusal fallback on the escalation model
    # Validation (D-031): a present value below this goes to human review. Kept above
    # confidence_threshold: values in between aren't worth the bigger model but deserve a look.
    review_confidence: float = 0.75
    # Reconciliation (D-033, D-036)
    match_auto: float = 92.0  # normalized-name score to merge without review
    match_review: float = 80.0  # below this, names are different organizations
    match_llm: bool = True  # let the small model adjudicate the band in between
    match_llm_confidence: float = 0.8  # below this the model's verdict goes to human review
    prices: dict[str, ModelPrice] = DEFAULT_PRICES

    @field_validator("anthropic_api_key", "anthropic_workspace_id", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        return None if v in ("", None) else v  # .env.example ships ANTHROPIC_API_KEY= (blank)


@lru_cache
def get_settings() -> Settings:
    return Settings()
