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
import urllib.request
import urllib.error
import ssl
import socket
import ipaddress

import models
from database import SessionLocal
from auth import get_current_user
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

router = APIRouter(
    prefix="/webhooks",
    tags=["Webhooks"],
    dependencies=[Depends(get_current_user)]
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
            ctx = ssl.create_default_context()
            data = json.dumps(payload).encode()
            req = urllib.request.Request(h.url, data=data, headers=headers, method='POST')
            resp = urllib.request.urlopen(req, timeout=10, context=ctx)
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
        db = SessionLocal()
        try:
            hooks = db.query(models.Webhook).filter(
                models.Webhook.tenant_id == tenant_id,
                models.Webhook.is_active == True
            ).all()

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
                    ctx = ssl.create_default_context()
                    data = json.dumps(payload).encode()
                    req = urllib.request.Request(h.url, data=data, headers=headers, method='POST')
                    urllib.request.urlopen(req, timeout=10, context=ctx)
                except Exception:
                    pass  # Не блокируем основной процесс
        finally:
            db.close()
    except Exception:
        pass
