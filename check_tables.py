import asyncio
import sys
from db import get_db

# Set Windows selector event loop policy for async operations
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def check_tables():
    async with get_db() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            rows = await cur.fetchall()
            print([row['table_name'] for row in rows])

if __name__ == "__main__":
    asyncio.run(check_tables())