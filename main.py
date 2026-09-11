import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import models
import models_tenant
from database import engine
from limiter_config import limiter
from version import __version__, __build__

from routers import auth_router
from routers import kanban_router
from routers import card_details_router
from routers import payments_router
from routers import writeoffs_router
from routers import writeoff_groups_router
from routers import clients_router
from routers import tags_router
from routers import suppliers_router
from routers import activity_router
from routers import tasks_router
from routers import notifications_router
from routers import email_parser_router
from routers import custom_objects_router
from routers import workflows_router
from routers import webhooks_router
from routers import nakladnye_router

models.Base.metadata.create_all(bind=engine)

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Снабжение и Продажи API",
    description="Бэкенд для Канбан-доски, учета оплат и списаний",
    version=__version__
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})

# CORS: the app is served same-origin, so this list only needs the hosts the
# UI is actually reached by. Override with CRM_CORS_ORIGINS when the domain
# changes; do not add wildcards, allow_credentials=True forbids them.
_DEFAULT_ORIGINS = ",".join([
    "http://87-232-64-12.nip.io",
    "https://87-232-64-12.nip.io",
    "http://87.232.64.12",
])
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CRM_CORS_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

os.makedirs("uploads", exist_ok=True)
os.makedirs("tenants", exist_ok=True)

app.include_router(auth_router.router)
app.include_router(kanban_router.router)
app.include_router(card_details_router.router)
app.include_router(payments_router.router)
app.include_router(writeoffs_router.router)
app.include_router(writeoff_groups_router.router)
app.include_router(clients_router.router)
app.include_router(tags_router.router)
app.include_router(suppliers_router.router)
app.include_router(activity_router.router)
app.include_router(tasks_router.router)
app.include_router(notifications_router.router)
app.include_router(email_parser_router.router)
# Cron-only routes (/email-parser/sync-all). This router was defined but never
# registered, so scheduled email sync silently did nothing. It is guarded by
# require_cron_token, not by a user JWT.
app.include_router(email_parser_router.cron_router)
# Cron-only route (/notifications/sync-overdue) — просрочки задач и сделок
app.include_router(notifications_router.cron_router)
app.include_router(custom_objects_router.router)
app.include_router(workflows_router.router)
app.include_router(webhooks_router.router)
app.include_router(nakladnye_router.router)

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")

@app.get("/admin")
def serve_admin():
    return FileResponse("admin.html")

@app.get("/custom_objects.html")
def serve_custom_objects():
    return FileResponse("custom_objects.html")

@app.get("/workflows.html")
def serve_workflows():
    return FileResponse("workflows.html")

@app.get("/settings.html")
def serve_settings():
    return FileResponse("settings.html")

@app.get("/api/version")
def get_version():
    return {"version": __version__, "build": __build__}

@app.get("/health")
def healthcheck():
    return {"status": "ok", "version": __version__}

@app.middleware("http")
async def add_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path

    if path.startswith("/css/") or path.startswith("/js/"):
        # Static assets are referenced with ?v=<sha1 of content> (see
        # tools/stamp_assets.py), so a given URL can never change meaning.
        # Caching them for a year removes ~250 KB CSS + 20 JS requests from
        # every page load. Any edit changes the hash and busts the cache.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, private"
    else:
        # HTML must never be cached: it carries the asset hashes above, so a
        # stale copy would keep pointing browsers at superseded JS and CSS.
        response.headers["Cache-Control"] = "no-cache, must-revalidate"

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # UI FIX 2026-08-31: заголовки из аудита. CSP осторожный: фронтенд целиком
    # на инлайн-скриптах и инлайн-обработчиках (unsafe-inline), внешние хосты —
    # только Google Fonts. frame-ancestors дублирует X-Frame-Options.
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
    return response

app.mount("/css", StaticFiles(directory="css"), name="css")
app.mount("/js", StaticFiles(directory="js"), name="js")
