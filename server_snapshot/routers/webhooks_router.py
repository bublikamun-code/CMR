"""Роутер webhook: секреты шифруются, доставка идёт через безопасный transport."""
import json
import logging
import socket
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import models
from database import SessionLocal, get_db
from webhook_delivery import (
    DEFAULT_MAX_ATTEMPTS,
    DeliveryOutcome,
    WebhookURLValidationError,
    create_delivery,
    deliver_delivery,
    mark_dead_letter,
    resolve_public_target,
    submit_delivery,
)
from webhook_security import (
    MAX_SECRET_LENGTH,
    WebhookEncryptionConfigError,
    encrypt_secret,
    obtain_fernet,
)

logger = logging.getLogger(__name__)

from auth import get_current_user, require_admin


def _validate_webhook_url(url: str) -> str:
    """Проверить схему, hostname, порт и текущий публичный DNS-ответ."""
    try:
        resolve_public_target(url, resolver=socket.getaddrinfo)
    except WebhookURLValidationError as exc:
        raise HTTPException(status_code=400, detail="Недопустимый URL webhook") from exc
    return url.strip()


def _assert_public_host(url: str) -> None:
    """Совместимый строгий preflight; сам connect всё равно pinning-транспортом."""
    resolve_public_target(url, resolver=socket.getaddrinfo)


def _obtain_webhook_fernet():
    try:
        return obtain_fernet()
    except WebhookEncryptionConfigError as exc:
        logger.error("Webhook encryption unavailable code=encryption_key_unavailable")
        raise HTTPException(
            status_code=503,
            detail="Шифрование секрета webhook временно недоступно",
        ) from exc


router = APIRouter(
    prefix="/webhooks",
    tags=["Webhooks"],
    dependencies=[Depends(require_admin())],
)


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    secret: Optional[str] = Field(default=None, max_length=MAX_SECRET_LENGTH)
    events: list


class WebhookUpdate(BaseModel):
    url: Optional[str] = Field(default=None, max_length=500)
    secret: Optional[str] = Field(default=None, max_length=MAX_SECRET_LENGTH)
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


def _webhook_response(webhook: models.Webhook) -> dict:
    return {
        "id": webhook.id,
        "url": webhook.url,
        "events": json.loads(webhook.events) if webhook.events else [],
        "is_active": webhook.is_active,
        "has_secret": bool(webhook.secret),
        "created_at": webhook.created_at.isoformat(),
    }


@router.get("/")
def list_webhooks(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    hooks = db.query(models.Webhook).filter(
        models.Webhook.tenant_id == current_user.tenant_id
    ).all()
    # Секрет никогда не входит в сериализацию, даже признаком его значения.
    return [_webhook_response(webhook) for webhook in hooks]


@router.post("/")
def create_webhook(
    wh: WebhookCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    url = _validate_webhook_url(wh.url)
    fernet = _obtain_webhook_fernet()
    encrypted_secret = (
        encrypt_secret(wh.secret, fernet) if wh.secret is not None else None
    )
    new_webhook = models.Webhook(
        url=url,
        secret=encrypted_secret,
        events=json.dumps(wh.events),
        tenant_id=current_user.tenant_id,
    )
    db.add(new_webhook)
    db.commit()
    db.refresh(new_webhook)
    return {"id": new_webhook.id, "message": "Webhook создан"}


@router.patch("/{wh_id}")
def update_webhook(
    wh_id: int,
    wh: WebhookUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    webhook = db.query(models.Webhook).filter(
        models.Webhook.id == wh_id,
        models.Webhook.tenant_id == current_user.tenant_id,
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook не найден")

    if "url" in wh.model_fields_set:
        url = (wh.url or "").strip()
        if not url:
            raise HTTPException(
                status_code=400,
                detail="Укажите URL получателя — он не может быть пустым",
            )
        webhook.url = _validate_webhook_url(url)
    if "secret" in wh.model_fields_set:
        if wh.secret is None:
            webhook.secret = None
        elif not wh.secret.strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Секрет не может быть пустой строкой: чтобы снять подпись, "
                    "пришлите null"
                ),
            )
        else:
            fernet = _obtain_webhook_fernet()
            webhook.secret = encrypt_secret(wh.secret.strip(), fernet)
    if "events" in wh.model_fields_set:
        if wh.events is None:
            raise HTTPException(
                status_code=400, detail="Список событий не может быть пустым (null)"
            )
        webhook.events = json.dumps(wh.events)
    if "is_active" in wh.model_fields_set:
        if wh.is_active is None:
            raise HTTPException(
                status_code=400,
                detail="Признак активности не может быть null: true или false",
            )
        webhook.is_active = bool(wh.is_active)
    db.commit()
    return {"message": "Обновлено"}


@router.delete("/{wh_id}")
def delete_webhook(
    wh_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    webhook = db.query(models.Webhook).filter(
        models.Webhook.id == wh_id,
        models.Webhook.tenant_id == current_user.tenant_id,
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook не найден")
    db.delete(webhook)
    db.commit()
    return {"message": "Удалено"}


def _delivery_response(outcome: DeliveryOutcome) -> dict:
    return {
        "status": outcome.response_status or 0,
        "success": outcome.status == "succeeded",
        "delivery_status": outcome.status,
        "attempts": outcome.attempts,
        "correlation_id": outcome.correlation_id,
        "error_code": outcome.error_code,
    }


@router.post("/{wh_id}/test")
def test_webhook(
    wh_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    webhook = db.query(models.Webhook).filter(
        models.Webhook.id == wh_id,
        models.Webhook.tenant_id == current_user.tenant_id,
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook не найден")

    payload = {
        "event": "test",
        "data": {"message": "Тестовый webhook от CRM"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    delivery = create_delivery(
        db,
        webhook_id=webhook.id,
        event="test",
        tenant_id=current_user.tenant_id,
    )
    outcome = deliver_delivery(delivery.id, payload)
    return _delivery_response(outcome)


async def notify_webhooks(tenant_id, event, data):
    """Поставить доставки в ограниченный executor без потока на событие."""
    db = SessionLocal()
    deliveries = []
    try:
        query = db.query(models.Webhook).filter(models.Webhook.is_active == True)
        if tenant_id is None:
            query = query.filter(models.Webhook.tenant_id == None)
        else:
            query = query.filter(models.Webhook.tenant_id == tenant_id)
        for webhook in query.all():
            try:
                events = json.loads(webhook.events) if webhook.events else []
                if event not in events:
                    continue
                payload = {
                    "event": event,
                    "data": data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                delivery = create_delivery(
                    db,
                    webhook_id=webhook.id,
                    event=event,
                    tenant_id=tenant_id,
                )
                deliveries.append((delivery.id, payload))
            except (TypeError, ValueError):
                logger.error(
                    "Webhook dispatch rejected code=configuration_invalid webhook_id=%s",
                    webhook.id,
                )
    except Exception:
        logger.error("Webhook dispatch failed code=dispatch_failed")
    finally:
        db.close()

    for delivery_id, payload in deliveries:
        if not submit_delivery(deliver_delivery, delivery_id, payload):
            correlation_id = mark_dead_letter(delivery_id)
            logger.error(
                "Webhook delivery rejected code=queue_full correlation_id=%s",
                correlation_id,
            )


def notify_webhooks_async(tenant_id, event, data):
    """Совместимая точка входа: сама создаёт доставки, поток на событие не нужен."""
    import asyncio

    try:
        asyncio.run(notify_webhooks(tenant_id, event, data))
    except Exception:
        logger.error("Webhook dispatch failed code=dispatch_failed")
