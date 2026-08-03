from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import admin, auth, dashboard, freights, monthly_routine, portal_statements, purchases, reconciliations, reports, units
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.services.seed import seed_reference_data


settings = get_settings()
frontend_dir = Path(__file__).resolve().parents[1] / "frontend_dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.app_env.lower() not in {"production", "prod"}:
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            seed_reference_data(db)
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.secure_cookies else "/api/docs",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/api/health", tags=["infraestrutura"])
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception:
        return JSONResponse({"status": "error", "database": "unavailable"}, status_code=503)


for router in (auth.router, dashboard.router, units.router, purchases.router, reconciliations.router, monthly_routine.router, freights.router, portal_statements.router, reports.router, admin.router):
    app.include_router(router, prefix="/api")


if (frontend_dir / "assets").exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")


@app.get("/logo-gbi.png", include_in_schema=False)
def company_logo():
    logo = frontend_dir / "logo-gbi.png"
    if logo.exists():
        return FileResponse(logo, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    return JSONResponse({"detail": "Logo não encontrada"}, status_code=404)


@app.get("/brand-{brand}.png", include_in_schema=False)
def brand_logo(brand: str):
    """Serve apenas os quatro logos fornecidos para as bandeiras da plataforma."""
    normalized = brand.lower()
    if normalized not in {"br", "ipiranga", "shell", "texaco"}:
        return JSONResponse({"detail": "Logo não encontrada"}, status_code=404)
    logo = frontend_dir / f"brand-{normalized}.png"
    if logo.exists():
        return FileResponse(logo, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    return JSONResponse({"detail": "Logo não encontrada"}, status_code=404)


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    index = frontend_dir / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return JSONResponse({"message": "Frontend ainda não compilado. Use o Vite em http://localhost:5173."})
