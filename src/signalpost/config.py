"""Configuration settings for Signalpost."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Signalpost configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Brreg API Endpoints
    brreg_enhet_base_url: str = "https://data.brreg.no/enhetsregisteret/api/enheter"
    brreg_regnskap_base_url: str = "https://data.brreg.no/regnskapsregisteret/regnskap"
    brreg_bulk_download_url: str = (
        "https://data.brreg.no/enhetsregisteret/api/enheter/lastned"
    )

    # Client & Network Settings
    brreg_request_timeout_seconds: float = 10.0
    brreg_max_concurrency: int = 15
    brreg_max_retries: int = 3
    brreg_retry_backoff_factor: float = 0.5

    # Storage Settings
    sqlite_db_path: str = "signalpost.db"

    # API Keys & LLM Settings
    openrouter_api_key: str | None = None
    breeth_api_key: str | None = None
    enable_llm_summary: bool = False
    llm_provider: str = "openrouter"
    llm_api_key: str | None = None
    llm_model: str = "google/gemini-2.5-flash"


# Default global settings instance
settings = Settings()
