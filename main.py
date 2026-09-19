import asyncio
import sys

import httpx
import psycopg
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import Settings, get_settings
from routers.auth import router as auth_router
from routers.reports import router as reports_router
from routers.users import router as users_router
from routers.digital_boundaries import router as digital_boundaries_router
from routers.patrol_configuration import router as patrol_configuration_router
from routers.incidents import router as incidents_router
from routers.roster_members import router as roster_members_router
from routers.patrol_scheduling import router as patrol_scheduling_router
from routers.announcements import router as announcements_router
from routers.otp import router as otp_router
from routers.cctv_cameras import router as cctv_cameras_router
from routers.cctv_events import router as cctv_events_router

from cctv import router as cctv_router


def _selector_loop_factory(use_subprocess: bool = False):
    """Return a SelectorEventLoop on Windows.

    psycopg 3 async mode refuses to run on a ProactorEventLoop, and uvicorn 0.49
    hard-codes ProactorEventLoop on Windows (uvicorn/loops/asyncio.py — its global
    `asyncio.set_event_loop_policy` call is ignored there). uvicorn's
    `Config.get_loop_factory()` accepts a dotted path to a custom factory, so point
    `loop="main:_selector_loop_factory"` at this. The reloader's subprocess worker
    re-imports main.py and receives the same loop via the config, which also fixes
    the stuck reloader worker caused by ProactorEventLoop on startup.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


app = FastAPI(title="CPSS Backend")

# CORS setup
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://cpss-frontend.vercel.app",
    "https://cpss.culiatpublicsafety.com",
]

# Allow any local dev server port (e.g. flutter run -d chrome picks a
# random port like 49359) plus Vercel preview deployments.
origin_regex = (
    r"^https://cpss-frontend(-[a-z0-9-]+)?\.vercel\.app$"
    r"|^http://(localhost|127\.0\.0\.1)(:\d+)?$"
)

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
app.include_router(reports_router)
app.include_router(digital_boundaries_router)
app.include_router(patrol_configuration_router)
app.include_router(incidents_router)
app.include_router(roster_members_router)
app.include_router(patrol_scheduling_router)
app.include_router(announcements_router)
app.include_router(otp_router)
app.include_router(cctv_cameras_router)
app.include_router(cctv_events_router)
app.include_router(cctv_router)


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
    # loop="main:_selector_loop_factory" forces a SelectorEventLoop on Windows so
    # psycopg async can run (uvicorn would otherwise use ProactorEventLoop).
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        loop="main:_selector_loop_factory",
    )