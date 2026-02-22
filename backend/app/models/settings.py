"""User settings model (key-value store)."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserSettings(Base, TimestampMixin):
    """A key-value setting entry."""

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<UserSettings {self.key}={self.value[:50]}>"
