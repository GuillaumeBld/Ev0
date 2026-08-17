"""Application configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):  # type: ignore[misc]
    """Application settings loaded from environment."""

    # Database
    database_url: str = "postgresql://ev0:ev0_dev_password@localhost:5432/ev0"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # CORS (comma-separated origins, or "*" for all)
    cors_origins_str: str = "*"

    # API Keys (optional)
    odds_api_key: str | None = None
    telegram_bot_token: str = ""
    telegram_chat_id: str = "8589235488"  # chat historique — sert de filet de secours

    # Un groupe Telegram par canal. Vide -> repli sur telegram_chat_id, message
    # prefixe [canal] + log WARNING : rien n'est jamais perdu en silence.
    telegram_chat_id_value: str = ""
    telegram_chat_id_incidents: str = ""
    telegram_chat_id_autopilot: str = ""

    # GitHub API — garde-fou anti-mort-silencieuse squad-sync (issue + PR
    # correctif d'attente sur echec). Vide -> degradation propre (log CRITICAL,
    # jamais d'appel API), voir app.ingestion.transfermarkt.failure_surface.
    github_token: str = ""
    github_repo: str = "GuillaumeBld/Ev0"

    # Alertes WhatsApp via CallMeBot — canal ops (incidents/santé) et recos (value bets)
    whatsapp_ops_phone: str = ""
    whatsapp_ops_apikey: str = ""
    whatsapp_recos_phone: str = ""    # fallback sur ops si vide
    whatsapp_recos_apikey: str = ""

    # Data Ingestion APIs
    firecrawl_api_key: str | None = None  # Firecrawl for web scraping
    openrouter_api_key: str | None = None  # OpenRouter for LLM parsing
    api_football_key: str | None = None  # API-Football for reliable data
    bzzoiro_api_key: str | None = "3a6c2b83ba4e89e4a12be3704de1e37a1303b1a4"  # Bzzoiro Sports Data API
    perplexity_api_key: str | None = None  # Perplexity sonar-pro for match context

    # LLM Parser settings
    llm_parser_model: str = "anthropic/claude-3.5-sonnet"  # Fast, good at extraction

    # Pricing parameters
    goalscorer_decay_lambda: float = 0.025
    assist_decay_lambda: float = 0.017
    min_edge_threshold: float = 0.05  # 5% minimum edge

    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS origins from comma-separated string or wildcard."""
        raw = self.cors_origins_str.strip()
        if raw == "*":
            return ["*"]
        # Handle JSON array format (e.g. '["*"]')
        if raw.startswith("["):
            import json

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
