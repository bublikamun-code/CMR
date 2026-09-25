"""Проверка удаления файлов сделки из настроенного каталога uploads."""
import models
from routers import kanban_router


def test_permanent_delete_uses_configured_uploads_dir_and_realpath_containment(
        client, admin, db, make_card, tmp_path, monkeypatch):
    _, headers = admin
    uploads_dir = tmp_path / "custom-uploads"
    uploads_dir.mkdir()
    attachment_path = uploads_dir / "attachment.pdf"
    invoice_path = uploads_dir / "invoice.pdf"
    attachment_path.write_bytes(b"attachment")
    invoice_path.write_bytes(b"invoice")

    outside_path = tmp_path / "outside.pdf"
    outside_path.write_bytes(b"outside")
    outside_link = uploads_dir / "outside-link.pdf"
    outside_link.symlink_to(outside_path)

    config_calls = []

    def configured_uploads_dir():
        config_calls.append(True)
        return uploads_dir

    monkeypatch.setattr(
        kanban_router.runtime_config,
        "uploads_dir",
        configured_uploads_dir,
    )

    card = make_card()
    card_id = card.id
    card.attachments.append(models.CardAttachment(
        file_name="attachment.pdf",
        file_path=str(attachment_path),
    ))
    card.attachments.append(models.CardAttachment(
        file_name="outside.pdf",
        file_path=str(outside_link),
    ))
    card.checklists.append(models.CardChecklist(
        company_name="Поставщик",
        invoice_file_name="invoice.pdf",
        invoice_file_path=str(invoice_path),
    ))
    db.commit()

    response = client.delete(
        f"/kanban/cards/{card_id}/permanent",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert config_calls == [True]
    assert not attachment_path.exists()
    assert not invoice_path.exists()
    assert outside_path.read_bytes() == b"outside"
