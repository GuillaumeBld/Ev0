"""Application configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # Database
    database_url: str = "postgresql://ev0:ev0_dev_password@localhost:5432/ev0"
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # CORS (comma-separated string)
    cors_origins_str: str = "http://localhost:3000"
    
    # API Keys (optional)
    odds_api_key: str | None = None
    
    # Data Ingestion APIs
    firecrawl_api_key: str | None = None  # Firecrawl for web scraping
    openrouter_api_key: str | None = None  # OpenRouter for LLM parsing
    api_football_key: str | None = None  # API-Football for reliable data
    
    # LLM Parser settings
    llm_parser_model: str = "anthropic/claude-sonnet-4-20250514"  # Fast, good at extraction
    
    # Pricing parameters
    goalscorer_decay_lambda: float = 0.025
    assist_decay_lambda: float = 0.017
    min_edge_threshold: float = 0.05  # 5% minimum edge
    
    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins_str.split(",") if origin.strip()]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
