import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import psycopg
from psycopg.rows import dict_row

from config import Settings, get_settings


@asynccontextmanager
async def get_db() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    """Yield an async psycopg connection with dict rows."""
    settings = get_settings()
    dsn = settings.DATABASE_URL.strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")

    conn = await psycopg.AsyncConnection.connect(
        dsn,
        row_factory=dict_row,
        connect_timeout=10,
    )
    try:
        await conn.set_autocommit(True)
        yield conn
    finally:
        await conn.close()
