from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cik: Mapped[str | None] = mapped_column(String(10), unique=True)
    financial_history_note: Mapped[str | None] = mapped_column(Text)
