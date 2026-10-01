import os

from runtime_config import (
    RuntimeConfigError,
    deployment_mode,
    trusted_proxy_networks,
    uploads_dir,
    validate_startup_config,
)
from trusted_proxy import TrustedProxyMiddleware
from webhook_security import validate_encryption_config as validate_webhook_encryption_config

# Security-конфигурация проверяется до импорта auth/роутеров: в production
# нельзя поднять процесс с неявной или невалидной CRM_COOKIE_SECURE, с
# невалидным CRM_TRUSTED_PROXY_NETWORKS, а также без выделенного ключа
# шифрования webhook.

validate_startup_config()
validate_webhook_encryption_config()

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import models

# Импорт ради побочного эффекта: Tenant объявлен на том же Base, поэтому модуль
# обязан быть импортирован ДО create_all ниже — иначе таблица tenants не
# создастся. Тот же приём с пояснением есть в tests/conftest.py.
import models_tenant  # noqa: F401
from auth import require_superadmin
from database import engine
import health_checks
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

# --- Режим публичной документации API (CRM_API_DOCS) -------------------------
#
# FastAPI по умолчанию открывает /docs, /redoc и /openapi.json любому, кто знает
# адрес. Для CRM это полная карта API: все эндпоинты, параметры, структуры
# моделей и имена эндпоинтов служебных подсистем. Ни фронт v2, ни legacy эти
# маршруты не используют (проверено grep'ом по site/, site-v2/ и
# tools/mockups/), поэтому закрыть их ничего не ломает.
#
#   disabled  — маршрутов документации нет вообще (404);
#   superadmin — документация и схема только для роли superadmin (по умолчанию);
#   public    — открыто всем, ТОЛЬКО для локальной разработки: в production
#               такой режим запрещён и поднимает процесс с ошибкой.
#
# Неизвестное значение закрывает startup, а не откатывается к открытой
# документации: опечатка в окружении не должна расширять доступ.
_DOCS_MODES = ("disabled", "superadmin", "public")
_DOCS_FALSE = ("0", "false", "no", "off")
_DOCS_TRUE = ("1", "true", "yes", "on")


def _api_docs_mode(environ=None) -> str:
    """Разобрать CRM_API_DOCS с безопасным значением по умолчанию."""
    env = os.environ if environ is None else environ
    raw = env.get("CRM_API_DOCS", "superadmin").strip().lower()
    if not raw:
        return "superadmin"
    if raw in _DOCS_FALSE:
        return "disabled"
    if raw in _DOCS_TRUE:
        return "superadmin"
    if raw in _DOCS_MODES:
        if raw == "public" and deployment_mode(env) == "production":
            raise RuntimeConfigError(
                "CRM_API_DOCS=public запрещён в production: "
                "используйте superadmin или disabled"
            )
        return raw
    raise RuntimeConfigError(
        "CRM_API_DOCS должен быть disabled, superadmin или public"
    )


API_DOCS_MODE = _api_docs_mode()
logger.info("Режим документации API: %s", API_DOCS_MODE)

# docs_url/redoc_url/openapi_url=None отключают встроенные маршруты: ниже они
# регистрируются вручную, чтобы навесить require_superadmin.
app = FastAPI(
    title="Снабжение и Продажи API",
    description="Бэкенд для Канбан-доски, учета оплат и списаний",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

if API_DOCS_MODE != "disabled":
    # В режиме superadmin каждый из трёх маршрутов закрыт валидным токеном
    # суперадмина (тот же механизм, что и в роутерах: httpOnly-cookie crm_token
    # или заголовок Authorization). Документация попадает в схему только под
    # include_in_schema=False, поэтому не засоряет её.
    _docs_guards = [] if API_DOCS_MODE == "public" else [Depends(require_superadmin())]

    @app.get("/openapi.json", include_in_schema=False, dependencies=_docs_guards)
    def api_openapi_schema():
        """Машиночитаемая схема API (закрыта тем же guard, что /docs)."""
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=_docs_guards)
    def api_swagger_ui():
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — Swagger UI",
        )

    @app.get("/redoc", include_in_schema=False, dependencies=_docs_guards)
    def api_redoc():
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — ReDoc",
        )

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# Uvicorn 0.30 не понимает CIDR в forwarded-allow-ips, поэтому граница
# применяется нашим pure-ASGI-слоем с явной проверкой прямого адреса.
TRUSTED_PROXY_NETWORKS = trusted_proxy_networks()
app.add_middleware(TrustedProxyMiddleware, networks=TRUSTED_PROXY_NETWORKS)


def _json_safe_validation_value(value):
    """Убирает не-finite значения из тела ошибки валидации.

    Стандартный JSONResponse не умеет сериализовать ``nan`` и ``inf`` из
    ``input`` Pydantic-ошибки. Для некорректного JSON это приводило к 500
    вместо ожидаемого 422, хотя денежный валидатор уже отвергал значение.
    """
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else "non-finite"
    if isinstance(value, dict):
        return {str(key): _json_safe_validation_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_validation_value(item) for item in value]
    if isinstance(value, BaseException):
        return str(value)
    return value


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [
        {key: _json_safe_validation_value(value) for key, value in error.items()}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


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

# FIX 2026-09-30 (аудит, С-12): TrustedHostMiddleware.
#
# Зачем. Приложение слушает 0.0.0.0, а nginx проксирует на него по HTTP. То
# есть весь API доступен напрямую мимо nginx: по адресу сервера и порту 20008
# без TLS, без заголовков nginx и без каких-либо его ограничений. CORS не
# защищает от этого: он ограничивает чтение ОТВЕТА браузером, а не
# выполнение запроса — curl или скрипт шлёт что угодно, Origin ему не мешает.
# TrustedHost отсекает обращения с чужим Host-заголовком, то есть попытки
# прийти на бэкенд напрямую, подставив домен в Host.
#
# Почему список берётся из CRM_CORS_ORIGINS, а не задаётся отдельно. Эти
# хосты — ровно те, через которые система УЖЕ работает: если адрес не проходит
# CORS-проверку, он и не должен приниматься. Отдельный список разошёлся бы с
# ним при первом же переезде, и тогда одно из двух мест тихо перестало бы
# работать. Локальные адреса добавлены обязательно: стенд, health-чеки и
# прямые заходы с самого сервера идут именно так.
#
# ПОСЛЕДСТВИЕ ДЛЯ ВЛАДЕЛЬЦА: при смене домена нужно добавить его в
# CRM_CORS_ORIGINS (и в CRM_ALLOWED_HOSTS, если он отличается). Забытый домен
# даст 400 на ВСЕ запросы сразу — поэтому переменная вынесена отдельно и
# переопределяет вывод, а не дополняет его молча.
_ALLOWED_HOSTS = {
    host.strip()
    for host in os.environ.get("CRM_ALLOWED_HOSTS", "").split(",")
    if host.strip()
} or {
    # Из CRM_CORS_ORIGINS выкидываем схему и возможный путь.
    origin.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    for origin in ALLOWED_ORIGINS
    if "//" in origin
} | {"127.0.0.1", "localhost", "testserver", "[::1]"}

app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(_ALLOWED_HOSTS))
logger.info("TrustedHost: разрешены хосты %s", sorted(_ALLOWED_HOSTS))

UPLOADS_DIR = uploads_dir()
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
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

# CSP v2 собран без inline-скриптов, inline-стилей и style-атрибутов. Legacy
# остаётся совместимым с замороженным интерфейсом, поэтому unsafe-inline
# сохраняется только для legacy-маршрутов и не-v2 режима.
CSP_V2 = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)
CSP_LEGACY = (
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
# Swagger UI и ReDoc грузят свой код со cdn.jsdelivr.net и вставляют инлайн-
# стили: под политиками выше страница отдавалась бы с 200, но осталась бы пустой
# (браузер блокирует ресурс). Это единственная страница приложения, которой
# нужен внешний CDN, и она доступна только суперадмину.
CSP_API_DOCS = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def _uses_v2_csp(path: str) -> bool:
    """Выбирает строгий CSP для v2-страницы и его редиректов."""
    if path == "/v2" or path.startswith("/v2/"):
        return True
    return FRONTEND_MODE == "v2" and path in {"/", "/admin"}


def _csp_for(path: str) -> str:
    """Политика CSP для маршрута: документация, v2 или legacy."""
    if path in DOCS_PATHS:
        return CSP_API_DOCS
    return CSP_V2 if _uses_v2_csp(path) else CSP_LEGACY


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
@app.get("/health/live")
def healthcheck():
    """Совместимый liveness-alias: процесс отвечает, зависимости не опрашиваются."""
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
def readinesscheck():
    report = health_checks.readiness_report(engine)
    if not report["ready"]:
        return JSONResponse(status_code=503, content=report)
    return report

@app.middleware("http")
async def add_headers(request: Request, call_next):
    # FIX 2026-09-30 (аудит, С-16): 500 больше не остаётся без заголовков
    # безопасности.
    #
    # Раньше `response = await call_next(request)` стоял без защиты, и любое
    # необработанное исключение проходило мимо этого кода: ответ собирал
    # внешний ServerErrorMiddleware, ВЫШЕ add_headers по стеку, поэтому CSP,
    # X-Frame-Options и nosniff на странице ошибки отсутствовали. То есть ровно
    # на самой нештатной странице браузер не получал политики, которые обычно и
    # защищают.
    #
    # Исключение не глотаем: оно уходит в журнал целиком, клиенту возвращается
    # одинаковая 500 без внутренних деталей.
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Необработанная ошибка при обработке запроса method=%s path=%s",
            request.method, request.url.path,
        )
        response = JSONResponse(
            status_code=500,
            content={"detail": "Внутренняя ошибка сервера"},
        )
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
    # UI FIX 2026-08-31: заголовки из аудита. CSP выбирается по маршруту:
    # v2 получает строгую политику, а замороженный legacy сохраняет
    # unsafe-inline и внешние Google Fonts. frame-ancestors дублирует
    # X-Frame-Options.
    # blob: в img-src (22.09.2026): фото входящих накладных и предпросмотр
    # выбранного файла в закупке v2 рисуются через URL.createObjectURL — без
    # blob: браузер блокировал 14 миниатюр ещё на загрузке страницы. На
    # скачивание файлов (<a download>) и window.open(blob:) CSP не влияет.
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = _csp_for(path)
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
