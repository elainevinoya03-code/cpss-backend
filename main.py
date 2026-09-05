import asyncio
import sys

import httpx
import psycopg
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import Settings, get_settings
from routers.auth import router as auth_router
from routers.users import router as users_router

# psycopg 3 async mode requires a SelectorEventLoop on Windows;
# uvicorn's default ProactorEventLoop raises a RuntimeError.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

app = FastAPI(title="CPSS Backend")

# CORS setup
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://cpss-frontend.vercel.app",
]

# Allow Vercel preview deployments (e.g. cpss-frontend-git-main-cpss.vercel.app)
origin_regex = r"^https://cpss-frontend(-[a-z0-9-]+)?\.vercel\.app$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)


@app.get("/")
async def home():
    return {"status": "FastAPI server is running"}


@app.get("/api/hello")
async def read_root():
    return {"message": "Hello from FastAPI backend!"}


def _check_postgres(db_url: str) -> dict:
    async def _run() -> dict:
        async with await psycopg.AsyncConnection.connect(
            db_url, connect_timeout=5
        ) as aconn:
            async with aconn.cursor() as cur:
                await cur.execute("SELECT version();")
                row = await cur.fetchone()
                return {
                    "connected": True,
                    "version": row[0] if row else "Unknown",
                }

    try:
        return asyncio.run(_run(), loop_factory=asyncio.SelectorEventLoop)
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.get("/api/health")
async def health_check(settings: Settings = Depends(get_settings)):
    # Linisin ang whitespace o extra characters
    db_url = settings.DATABASE_URL.strip() if settings.DATABASE_URL else ""
    sb_url = settings.SUPABASE_URL.strip() if settings.SUPABASE_URL else ""
    sb_key = settings.SUPABASE_KEY.strip() if settings.SUPABASE_KEY else ""

    # DEBUG: Ipapakita sa terminal window kung ano ang eksaktong binabasa ng Python
    print("\n--- [DEBUG] READ FROM ENV ---")
    print(f"DATABASE_URL : '{db_url}'")
    print(f"SUPABASE_URL : '{sb_url}'")
    print(f"SUPABASE_KEY : '{sb_key[:15]}...' (truncated)")
    print("-----------------------------\n")

    result = {
        "supabase_url_configured": bool(sb_url),
        "database_url_configured": bool(db_url),
        "postgres": {"connected": False},
        "supabase_api": {"connected": False},
    }

    # Async PostgreSQL Check (psycopg 3)
    # Runs on its own SelectorEventLoop in a worker thread because psycopg
    # async mode is incompatible with the ProactorEventLoop uvicorn uses.
    if db_url:
        result["postgres"] = await asyncio.to_thread(_check_postgres, db_url)

    # Supabase REST root (/rest/v1/) requires the service_role key,
    # so use the auth health endpoint which accepts the anon key.
    if sb_url and sb_key:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{sb_url}/auth/v1/health",
                    headers={"apikey": sb_key},
                )
                result["supabase_api"] = {
                    "connected": resp.status_code == 200,
                    "status_code": resp.status_code,
                }
        except Exception as e:
            result["supabase_api"] = {"connected": False, "error": str(e)}

    return result


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)