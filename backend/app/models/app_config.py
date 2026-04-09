"""App configuration model (key-value store)."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppConfig(Base):
    """Global application configuration key-value pairs."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))

    def __repr__(self) -> str:
        return f"<AppConfig {self.key}={self.value[:50]}>"
