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
from routers import clients_router
from routers import tags_router
from routers import suppliers_router
from routers import activity_router
from routers import email_parser_router
from routers import custom_objects_router
from routers import workflows_router
from routers import webhooks_router

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

# CORS: allow both HTTP and HTTPS origins
ALLOWED_ORIGINS = os.environ.get("CRM_CORS_ORIGINS", "https://crm.svetvdome.by,http://crm.svetvdome.by,http://87.232.65.217").split(",")

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
app.include_router(clients_router.router)
app.include_router(tags_router.router)
app.include_router(suppliers_router.router)
app.include_router(activity_router.router)
app.include_router(email_parser_router.router)
app.include_router(custom_objects_router.router)
app.include_router(workflows_router.router)
app.include_router(webhooks_router.router)

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
    if request.url.path.startswith("/css/") or request.url.path.startswith("/js/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response

app.mount("/css", StaticFiles(directory="css"), name="css")
app.mount("/js", StaticFiles(directory="js"), name="js")
