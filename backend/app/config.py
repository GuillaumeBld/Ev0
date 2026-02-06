"""Application configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # Database
    database_url: str = "postgresql://ev0:ev0_dev_password@localhost:5432/ev0"
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]
    
    # API Keys (optional)
    odds_api_key: str | None = None
    
    # Pricing parameters
    goalscorer_decay_lambda: float = 0.025
    assist_decay_lambda: float = 0.017
    min_edge_threshold: float = 0.05  # 5% minimum edge
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
