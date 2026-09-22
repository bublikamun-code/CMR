import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import models

# Импорт ради побочного эффекта: Tenant объявлен на том же Base, поэтому модуль
# обязан быть импортирован ДО create_all ниже — иначе таблица tenants не
# создастся. Тот же приём с пояснением есть в tests/conftest.py.
import models_tenant  # noqa: F401
from database import engine
from limiter_config import limiter
from routers import (
    activity_router,
    auth_router,
    card_details_router,
    clients_router,
    custom_objects_router,
    dictionaries_router,
    email_parser_router,
    kanban_router,
    nakladnye_router,
    notifications_router,
    payments_router,
    suppliers_router,
    tags_router,
    tasks_router,
    webhooks_router,
    workflows_router,
    writeoff_groups_router,
    writeoffs_router,
)
from version import __build__, __version__

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
    logger.error(f"Unhandled error: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})

# CORS: the app is served same-origin, so this list only needs the hosts the
# UI is actually reached by. Override with CRM_CORS_ORIGINS when the domain
# changes; do not add wildcards, allow_credentials=True forbids them.
_DEFAULT_ORIGINS = "http://87-232-64-12.nip.io,https://87-232-64-12.nip.io,http://87.232.64.12"
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
app.include_router(dictionaries_router.router)
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

# Какой фронтенд считать основным на "/". Переключение — этап 4 плана замены
# (design-plans/frontend-replacement-plan.md), Пакет G. С 19.09.2026 основным
# считается v2 — решение владельца после закрытия P0/V1–V10 и верификации на
# копии прод-БД. Дефолт "v2" фиксирует выбор в КОДЕ: потеря/сброс .pm2.env не
# откатывает корень на старый фронт молча. Осознанный откат — CRM_FRONTEND=legacy
# в .pm2.env + pm2 restart crm; старый фронт заморожен и живёт на /legacy.
FRONTEND_MODE = os.environ.get("CRM_FRONTEND", "v2")


@app.get("/")
def serve_frontend():
    if FRONTEND_MODE == "v2":
        # Редирект, а не FileResponse: ассеты v2 лежат относительными путями
        # внутри /v2/ и на корне резолвились бы в маунты старого фронта /css и /js.
        return RedirectResponse("/v2/", status_code=302)
    return FileResponse("index.html")


@app.get("/legacy")
def serve_legacy_frontend():
    # Старый фронт на период приёмки. Его ассеты смонтированы на /css и /js
    # абсолютными путями, поэтому работают и с корня, и из /legacy.
    return FileResponse("index.html")


@app.get("/legacy/admin")
def serve_legacy_admin():
    return FileResponse("admin.html")


@app.get("/admin")
def serve_admin():
    if FRONTEND_MODE == "v2":
        # Админка v2 — раздел «Пульт» в SPA; старую админку держим только
        # на /legacy/admin, чтобы закладка /admin не вела в замороженный UI.
        return RedirectResponse("/v2/", status_code=302)
    return FileResponse("admin.html")

@app.get("/manifest.json")
def serve_manifest():
    # PWA-манифест, на который ссылается index.html. Отдельный роут нужен
    # потому, что StaticFiles смонтирован только на /css и /js — без него
    # браузер получал 404 и установка приложения не работала.
    return FileResponse("manifest.json", media_type="application/manifest+json")

# Отдельные страницы /settings.html, /workflows.html и /custom_objects.html удалены:
# на проде этих файлов нет и роуты отдавали HTTP 500. Настройки, воркфлоу и кастомные
# объекты живут страницами внутри index.html (js/settings.js ходит в /custom/* напрямую).

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

    if path.startswith(("/css/", "/js/")):
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
    # blob: в img-src (22.09.2026): фото входящих накладных и предпросмотр
    # выбранного файла в закупке v2 рисуются через URL.createObjectURL — без
    # blob: браузер блокировал 14 миниатюр ещё на загрузке страницы. На
    # скачивание файлов (<a download>) и window.open(blob:) CSP не влияет.
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )
    return response

app.mount("/css", StaticFiles(directory="css"), name="css")
app.mount("/js", StaticFiles(directory="js"), name="js")
# /static/logo.svg нужен manifest.json (иконка) и admin.html (логотип в шапке).
# Без маунта оба запроса отдавали 404: роутов на отдельные файлы нет, а
# StaticFiles был смонтирован только на /css и /js.
app.mount("/static", StaticFiles(directory="static"), name="static")
# Новый фронт (site-v2, Этап 0/4 плана замены — design-plans/frontend-replacement-plan.md):
# nginx проксирует всё на приложение, поэтому каталог вебрута сам по себе не
# отдаётся — нужен маунт. html=True открывает /v2/ на index.html. Старый фронт
# остаётся на / до переключения; откат /v2 — снятие маунта.
app.mount("/v2", StaticFiles(directory="site-v2", html=True), name="v2")

# Кэш-политика /v2. HTML всегда no-cache — он держит штампы ?v= и должен
# перепроверяться при каждом входе, иначе после деплоя браузер смешает новый
# HTML со старой статикой. Штампованная статика (css/, js/) — иммутабельный
# годовой кэш: контент меняется → пересборка меняет штамп → меняется URL
# (?v=...), поэтому «вечное» кэширование безопасно. Шрифты в CSS url()
# без штампа — их версия зашита в имя файла (см. build_site_v2.py).
# no-cache — revalidation по ETag, не запрет кэширования.
# Правка 21.09: раньше no-cache стоял на всём /v2*, и каждый вход гонял
# ~20 условных запросов — на мобильном RTT это давало десятки секунд до
# отрисовки (жалоба «канбан появляется через 20 секунд»).
V2_IMMUTABLE_PREFIXES = ("/v2/css/", "/v2/js/", "/v2/fonts/")

@app.middleware("http")
async def v2_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/v2"):
        if path.startswith(V2_IMMUTABLE_PREFIXES):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
    return response
