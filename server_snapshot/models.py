from sqlalchemy import Column, Integer, String, Numeric, Boolean, ForeignKey, DateTime, Date, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, default="manager")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    cards = relationship("Card", back_populates="owner")

class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    unp = Column(String, nullable=True, index=True)
    address = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)
    note = Column(String, nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    cards = relationship("Card", back_populates="client")

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    unp = Column(String, nullable=True, index=True)
    address = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)
    note = Column(String, nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    # Пункты чек-листов, закреплённые за поставщиком —
    # так же, как карточки закреплены за клиентом.
    checklist_items = relationship("CardChecklist", back_populates="supplier")

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    color = Column(String, default="#4f7cf5")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)

class CardTag(Base):
    __tablename__ = "card_tags"
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)

class Card(Base):
    __tablename__ = "cards"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text, nullable=True)
    status = Column(String, default="Новый запрос", index=True)
    total_amount = Column(Numeric(12, 2), default=0.0)
    store_location = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    sender_email = Column(String(255), nullable=True)
    is_deleted = Column(Boolean, default=False, index=True)
    due_date = Column(Date, nullable=True)
    priority = Column(Integer, default=0)
    position = Column(Integer, default=0, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)

    owner = relationship("User", back_populates="cards")
    client = relationship("Client", back_populates="cards")
    attachments = relationship("CardAttachment", back_populates="card", cascade="all, delete-orphan")
    checklists = relationship("CardChecklist", back_populates="card", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="card")
    tags = relationship("Tag", secondary="card_tags", backref="cards")

class CardAttachment(Base):
    __tablename__ = "card_attachments"
    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String)
    file_path = Column(String)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"))
    card = relationship("Card", back_populates="attachments")

class CardChecklist(Base):
    __tablename__ = "card_checklists"
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, index=True)
    amount = Column(Numeric(12, 2), default=0.0)
    is_paid = Column(Boolean, default=False)
    is_secondary_check = Column(Boolean, default=False)
    note = Column(String, nullable=True)
    invoice_file_name = Column(String, nullable=True)
    invoice_file_path = Column(String, nullable=True)
    # Поставщик выбирается из справочника. company_name остаётся как
    # текстовый снимок названия — для старых записей и на случай,
    # если поставщика удалят из справочника.
    supplier_id = Column(Integer, ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, index=True)
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    card = relationship("Card", back_populates="checklists")
    # ChecklistResponse включает supplier, а FastAPI сериализует ответ уже
    # после того, как роутер закрыл сессию в finally. При ленивой загрузке
    # это давало DetachedInstanceError и 500 на ровном месте.
    # joined — связь подтягивается тем же запросом, объект остаётся полным.
    supplier = relationship("Supplier", back_populates="checklist_items", lazy="joined")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    company_name = Column(String, index=True)
    amount = Column(Numeric(12, 2))
    store_location = Column(String)
    invoice_number = Column(String, nullable=True)
    invoice_date = Column(String, nullable=True)
    is_calculated = Column(Boolean, default=False)
    is_invoice_issued = Column(Boolean, default=False)
    is_written_off = Column(Boolean, default=False)
    is_secondary_check = Column(Boolean, default=False)
    print_status = Column(String, default="Печать")
    note = Column(String, nullable=True)
    is_document = Column(Boolean, default=False, index=True)
    is_invoice_doc = Column(Boolean, default=False)
    is_bill_doc = Column(Boolean, default=False)
    is_warehouse_writeoff = Column(Boolean, default=False)
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="SET NULL"), nullable=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    card = relationship("Card", back_populates="transactions")

class ActivityLog(Base):
    __tablename__ = "activity_log"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="SET NULL"))
    action = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# ============================================================
# v2.2 — Версионирование записей
# ============================================================

class RecordVersion(Base):
    __tablename__ = "record_versions"
    id = Column(Integer, primary_key=True, index=True)
    table_name = Column(String(50), nullable=False, index=True)
    record_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    data_snapshot = Column(Text, nullable=False)  # JSON снимка
    changed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    change_type = Column(String(20), nullable=False)  # 'create', 'update', 'delete'
    changed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)


# ============================================================
# v2.2 — Кастомные объекты (динамическая модель данных)
# ============================================================

class CustomObjectType(Base):
    __tablename__ = "custom_object_types"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True)
    label = Column(String(200), nullable=False)
    icon = Column(String(50), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class CustomFieldDef(Base):
    __tablename__ = "custom_field_defs"
    id = Column(Integer, primary_key=True, index=True)
    object_type_id = Column(Integer, ForeignKey("custom_object_types.id", ondelete="CASCADE"))
    name = Column(String(100), nullable=False)
    label = Column(String(200), nullable=False)
    field_type = Column(String(50), nullable=False)  # text, number, select, date, relation, currency, boolean, file
    is_required = Column(Boolean, default=False)
    options = Column(Text, nullable=True)  # JSON: [{"label":"...","value":"..."}]
    relation_target = Column(String(100), nullable=True)  # имя связанного объекта
    position = Column(Integer, default=0)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)

class CustomRecord(Base):
    __tablename__ = "custom_records"
    id = Column(Integer, primary_key=True, index=True)
    object_type_id = Column(Integer, ForeignKey("custom_object_types.id"))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class CustomFieldValue(Base):
    __tablename__ = "custom_field_values"
    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("custom_records.id", ondelete="CASCADE"))
    field_def_id = Column(Integer, ForeignKey("custom_field_defs.id"))
    value_text = Column(Text, nullable=True)
    value_number = Column(Numeric, nullable=True)
    value_boolean = Column(Boolean, nullable=True)
    value_date = Column(DateTime, nullable=True)
    value_json = Column(Text, nullable=True)
    value_relation = Column(Integer, ForeignKey("custom_records.id"), nullable=True)


# ============================================================
# v2.2 — Справочники
# ============================================================

class StoreLocation(Base):
    __tablename__ = "store_locations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    address = Column(Text, nullable=True)
    phone = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)

class DealStatus(Base):
    __tablename__ = "deal_statuses"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    position = Column(Integer, default=0)
    color = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)


# ============================================================
# v2.2 — Воркфлоу
# ============================================================

class Workflow(Base):
    __tablename__ = "workflows"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class WorkflowTrigger(Base):
    __tablename__ = "workflow_triggers"
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"))
    trigger_type = Column(String(50), nullable=False)  # record_event, manual, schedule, webhook
    config = Column(Text, nullable=False)  # JSON
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"))
    parent_step_id = Column(Integer, ForeignKey("workflow_steps.id"), nullable=True)
    step_type = Column(String(50), nullable=False)  # action, condition, delay
    action_type = Column(String(50), nullable=True)  # create_record, update_record, send_email, http_request
    config = Column(Text, nullable=False)  # JSON
    position = Column(Integer, default=0)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)

class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="SET NULL"))
    trigger_id = Column(Integer, ForeignKey("workflow_triggers.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(20), default="running")  # running, completed, failed
    input_data = Column(Text, nullable=True)  # JSON
    output_data = Column(Text, nullable=True)  # JSON
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)


# ============================================================
# v2.2 — Webhooks
# ============================================================

class Webhook(Base):
    __tablename__ = "webhooks"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(500), nullable=False)
    secret = Column(String(200), nullable=True)
    events = Column(Text, nullable=False)  # JSON: ["card.created", "card.updated"]
    is_active = Column(Boolean, default=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ============================================================
# v2.2 — Сохранённые представления
# ============================================================

class SavedView(Base):
    __tablename__ = "saved_views"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    view_type = Column(String(20), default="table")  # table, kanban, calendar
    target_page = Column(String(50), nullable=False)  # kanban, payments, documents
    filters = Column(Text, nullable=True)  # JSON
    sort_by = Column(String(100), nullable=True)
    sort_direction = Column(String(10), default="asc")
    group_by = Column(String(100), nullable=True)
    is_default = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
