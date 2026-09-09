"""
Роутер для Webhooks — уведомления внешних систем при событиях.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import json
import hashlib
import hmac
import logging
import urllib.request
import urllib.error
import ssl
import socket
import ipaddress

import models
from database import SessionLocal

logger = logging.getLogger(__name__)
from auth import get_current_user, require_admin
from db_utils import resolve_tenant_db_standalone as _get_db
from urllib.parse import urlparse

# Blocked host patterns for SSRF protection
SSRF_BLOCKED = [
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "169.254.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.", ".internal", ".local",
]

def _validate_webhook_url(url: str) -> str:
    """Validate webhook URL to prevent SSRF attacks."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Webhook URL must use http or https")
    hostname = parsed.hostname or ""
    for blocked in SSRF_BLOCKED:
        if hostname == blocked or hostname.endswith(blocked) or hostname.startswith(blocked):
            raise HTTPException(status_code=400, detail=f"Webhook URL hostname is not allowed: {hostname}")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
        for family, type_, proto, canonname, sockaddr in addrinfo:
            ip = sockaddr[0]
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                raise HTTPException(status_code=400, detail=f"Webhook URL resolves to a private/reserved IP: {ip}")
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Webhook URL hostname could not be resolved")

    return url


def _assert_public_host(url: str) -> None:
    """FIX 2026-08-29 (SSRF): повторная проверка хоста непосредственно перед
    запросом. Раньше валидация была только при создании вебхука — DNS rebinding
    позволял подменить IP между проверкой и urlopen; test_webhook и
    notify_webhooks вообще не проверяли адрес."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Недопустимый URL вебхука: {url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    for info in socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP):
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"Ходится в непубличный адрес: {addr}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """FIX 2026-08-29 (SSRF): редиректы запрещены — ими обходят проверку хоста."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_webhook(req: urllib.request.Request):
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open(req, timeout=10)

router = APIRouter(
    prefix="/webhooks",
    tags=["Webhooks"],
    # FIX 2026-08-30 (роли): вебхуки — системная интеграция, отправляющая данные
    # сделок наружу. Раньше создавать их мог любой аутентифицированный
    # (включая склад/документы): канал утечки базы. Фичей пока не пользуется
    # никто (таблица пуста), вкладка в UI скрыта для не-админов.
    dependencies=[Depends(require_admin())]
)


class WebhookCreate(BaseModel):
    url: str
    secret: Optional[str] = None
    events: list  # ["card.created", "card.updated", "client.created"]

class WebhookUpdate(BaseModel):
    url: Optional[str] = None
    secret: Optional[str] = None
    events: Optional[list] = None
    is_active: Optional[bool] = None


AVAILABLE_EVENTS = [
    "card.created", "card.updated", "card.deleted",
    "client.created", "client.updated", "client.deleted",
    "supplier.created", "supplier.updated", "supplier.deleted",
    "payment.created", "payment.updated",
    "document.created", "document.updated",
]


@router.get("/events")
def list_events():
    return AVAILABLE_EVENTS


@router.get("/")
def list_webhooks(current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        hooks = db.query(models.Webhook).filter(
            models.Webhook.tenant_id == current_user.tenant_id
        ).all()
        return [{"id": h.id, "url": h.url, "events": json.loads(h.events) if h.events else [],
                 "is_active": h.is_active, "created_at": h.created_at.isoformat()} for h in hooks]
    finally:
        db.close()


@router.post("/")
def create_webhook(wh: WebhookCreate, current_user=Depends(get_current_user)):
    _validate_webhook_url(wh.url)
    db = _get_db(current_user)
    try:
        new_wh = models.Webhook(
            url=wh.url,
            secret=wh.secret,
            events=json.dumps(wh.events),
            tenant_id=current_user.tenant_id
        )
        db.add(new_wh)
        db.commit()
        db.refresh(new_wh)
        return {"id": new_wh.id, "url": new_wh.url, "message": "Webhook создан"}
    finally:
        db.close()


@router.patch("/{wh_id}")
def update_webhook(wh_id: int, wh: WebhookUpdate, current_user=Depends(get_current_user)):
    if wh.url is not None:
        _validate_webhook_url(wh.url)
    db = _get_db(current_user)
    try:
        h = db.query(models.Webhook).filter(
            models.Webhook.id == wh_id,
            models.Webhook.tenant_id == current_user.tenant_id
        ).first()
        if not h:
            raise HTTPException(status_code=404, detail="Webhook не найден")
        if wh.url is not None: h.url = wh.url
        if wh.secret is not None: h.secret = wh.secret
        if wh.events is not None: h.events = json.dumps(wh.events)
        if wh.is_active is not None: h.is_active = wh.is_active
        db.commit()
        return {"message": "Обновлено"}
    finally:
        db.close()


@router.delete("/{wh_id}")
def delete_webhook(wh_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        h = db.query(models.Webhook).filter(
            models.Webhook.id == wh_id,
            models.Webhook.tenant_id == current_user.tenant_id
        ).first()
        if not h:
            raise HTTPException(status_code=404, detail="Webhook не найден")
        db.delete(h)
        db.commit()
        return {"message": "Удалено"}
    finally:
        db.close()


@router.post("/{wh_id}/test")
def test_webhook(wh_id: int, current_user=Depends(get_current_user)):
    db = _get_db(current_user)
    try:
        h = db.query(models.Webhook).filter(
            models.Webhook.id == wh_id,
            models.Webhook.tenant_id == current_user.tenant_id
        ).first()
        if not h:
            raise HTTPException(status_code=404, detail="Webhook не найден")

        payload = {
            "event": "test",
            "data": {"message": "Тестовый webhook от CRM"},
            "timestamp": datetime.now().isoformat()
        }

        headers = {"Content-Type": "application/json"}
        if h.secret:
            sig = hmac.new(h.secret.encode(), json.dumps(payload).encode(), hashlib.sha256).hexdigest()
            headers["X-Webhook-Signature"] = sig

        try:
            _assert_public_host(h.url)
            ctx = ssl.create_default_context()
            data = json.dumps(payload).encode()
            req = urllib.request.Request(h.url, data=data, headers=headers, method='POST')
            resp = _open_webhook(req)
            return {"status": resp.status, "success": resp.status < 400}
        except urllib.error.HTTPError as e:
            return {"status": e.code, "success": False, "error": str(e)}
        except Exception as e:
            return {"status": 0, "success": False, "error": str(e)}
    finally:
        db.close()


# === Функция отправки webhook (вызывается из других роутеров) ===

async def notify_webhooks(tenant_id, event, data):
    """Отправляет webhook всем активным подписчикам для данного tenant."""
    try:
        # Фикс аудита 10.09: вебхуки тенанта живут в его tenant-базе, а поиск
        # шёл только по основной — подписчики-тенанты молча не получали
        # ничего. Теперь ищем в обеих базах (в основной — NULL-подписчики).
        db = SessionLocal()
        hooks = []
        try:
            q = db.query(models.Webhook).filter(models.Webhook.is_active == True)
            if tenant_id is None:
                q = q.filter(models.Webhook.tenant_id == None)
            else:
                q = q.filter(models.Webhook.tenant_id == tenant_id)
            hooks.extend(q.all())
        finally:
            db.close()
        if tenant_id is not None:
            try:
                from database import get_tenant_db
                tdb = get_tenant_db(tenant_id)
                try:
                    hooks.extend(tdb.query(models.Webhook).filter(
                        models.Webhook.tenant_id == tenant_id,
                        models.Webhook.is_active == True
                    ).all())
                finally:
                    tdb.close()
            except Exception:
                logger.warning("Tenant webhook lookup failed for tenant %s", tenant_id, exc_info=True)

        for h in hooks:
            events = json.loads(h.events) if h.events else []
            if event not in events:
                continue

            payload = {
                "event": event,
                "data": data,
                "timestamp": datetime.now().isoformat()
            }

            headers = {"Content-Type": "application/json"}
            if h.secret:
                sig = hmac.new(h.secret.encode(), json.dumps(payload).encode(), hashlib.sha256).hexdigest()
                headers["X-Webhook-Signature"] = sig

            try:
                _assert_public_host(h.url)
                ctx = ssl.create_default_context()
                data = json.dumps(payload).encode()
                req = urllib.request.Request(h.url, data=data, headers=headers, method='POST')
                _open_webhook(req)
            except Exception as e:
                logger.warning("Webhook %s delivery failed: %s", h.url, e)
    except Exception:
        logger.warning("Webhook dispatch failed for event %s", event, exc_info=True)


def notify_webhooks_async(tenant_id, event, data):
    """FIX 2026-08-29: фоновая отправка вебхуков в отдельном потоке.

    Раньше вызывалось как asyncio.get_event_loop().create_task() из
    синхронного хендлера: event loop в потоке threadpool отсутствует,
    вызов падал с RuntimeError и глушился except — вебхуки никогда
    не отправлялись. asyncio.run() в daemon-потоке решает это, не
    блокируя ответ API.
    """
    import asyncio
    import threading

    def _run():
        try:
            asyncio.run(notify_webhooks(tenant_id, event, data))
        except Exception:
            logger.warning("notify_webhooks crashed for %s", event, exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
