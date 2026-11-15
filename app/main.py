from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.db import get_engine
from app.errors import install_error_handlers
from app.routers import auth, me, projects, requirements, runners, scans, tenant, test_cases, test_runs, test_scripts, users

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="TestMax Pilot 2", docs_url=None, redoc_url=None, openapi_url=None)
install_error_handlers(app)
app.include_router(auth.router)
app.include_router(tenant.router)
app.include_router(me.router)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(requirements.router)
app.include_router(runners.router)
app.include_router(scans.router)
app.include_router(test_cases.router)
app.include_router(test_scripts.router)
app.include_router(test_runs.router)

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
)


@app.middleware("http")
async def security(request: Request, call_next):
    # CSRF defence in depth (cookies are SameSite=Lax): reject cross-origin writes.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse(
                {"error": {"code": "bad_origin", "message": "Cross-origin request blocked."}}, status_code=403
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = _CSP
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    with get_engine().connect() as conn:
        conn.execute(text("select 1"))
    return {"status": "ok"}


class RevalidatingStaticFiles(StaticFiles):
    """No build step means no file hashes in the names, so the browser must check with the server (ETag, cheap 304)
    on every load; otherwise it keeps showing old scripts for a while after an upgrade."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"error": {"code": "not_found", "message": "Not found."}}, status_code=404)
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
