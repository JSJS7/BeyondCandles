from collections.abc import Iterator
from typing import ClassVar

from sqlalchemy import Table, create_engine, text
from sqlalchemy.engine import URL, Connection
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    __table__: ClassVar[Table]


database_url = URL.create(
    drivername="postgresql+psycopg",
    username=settings.postgres_user,
    password=settings.postgres_password,
    host=settings.postgres_host,
    port=settings.postgres_port,
    database=settings.postgres_db,
)

engine = create_engine(database_url, connect_args={"connect_timeout": 5})


def get_connection() -> Iterator[Connection]:
    """Keep one database connection open for the duration of an HTTP request."""
    with engine.connect() as connection:
        yield connection


def lock_data_imports(connection: Connection) -> None:
    """Serialize imports and rebuilds with one transaction-scoped advisory lock."""
    connection.execute(text("SELECT pg_advisory_xact_lock(204812, 1)"))
