from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Optional
from datetime import datetime, date

# --- СХЕМЫ ПОЛЬЗОВАТЕЛЯ ---

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str
    role: str = "manager"

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError("Пароль должен содержать минимум 6 символов")
        return v

class UserResponse(UserBase):
    id: int
    role: str
    model_config = ConfigDict(from_attributes=True)

class UserUpdate(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if v and len(v) < 6:
            raise ValueError("Пароль должен содержать минимум 6 символов")
        return v


# --- СХЕМЫ ТЕГОВ ---

class TagBase(BaseModel):
    name: str
    color: str = "#4f7cf5"

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if len(v) > 50:
            raise ValueError("Название тега не может превышать 50 символов")
        return v

    @field_validator("color")
    @classmethod
    def validate_color(cls, v):
        import re
        if not re.match(r'^#[0-9a-fA-F]{6}$', v):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return v

class TagCreate(TagBase):
    pass

class TagResponse(TagBase):
    id: int
    model_config = ConfigDict(from_attributes=True)


# --- СХЕМЫ ПОСТАВЩИКОВ ---

class SupplierBase(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    unp: Optional[str] = None
    address: Optional[str] = None
    contact_person: Optional[str] = None
    note: Optional[str] = None

class SupplierCreate(SupplierBase):
    pass

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    unp: Optional[str] = None
    address: Optional[str] = None
    contact_person: Optional[str] = None
    note: Optional[str] = None

class SupplierResponse(SupplierBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- СХЕМЫ ЧЕК-ЛИСТА ---

class ChecklistBase(BaseModel):
    company_name: str
    amount: float
    is_paid: bool = False
    is_secondary_check: bool = False
    note: Optional[str] = None
    # Поставщик из справочника. company_name остаётся снимком названия,
    # чтобы старые записи и удалённые поставщики не теряли подпись.
    supplier_id: Optional[int] = None

class ChecklistCreate(ChecklistBase):
    company_name: Optional[str] = None      # подставится из справочника

class ChecklistUpdate(BaseModel):
    company_name: Optional[str] = None
    amount: Optional[float] = None
    is_paid: Optional[bool] = None
    is_secondary_check: Optional[bool] = None
    note: Optional[str] = None
    supplier_id: Optional[int] = None

class ChecklistResponse(ChecklistBase):
    id: int
    card_id: int
    invoice_file_name: Optional[str] = None
    invoice_file_path: Optional[str] = None
    supplier: Optional[SupplierResponse] = None
    model_config = ConfigDict(from_attributes=True)


# --- СХЕМЫ ВЛОЖЕНИЙ ---

class AttachmentBase(BaseModel):
    file_name: str
    file_path: str

class AttachmentCreate(AttachmentBase):
    pass

class AttachmentResponse(AttachmentBase):
    id: int
    card_id: int
    uploaded_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- СХЕМЫ КЛИЕНТОВ ---

class ClientBase(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    unp: Optional[str] = None
    address: Optional[str] = None
    contact_person: Optional[str] = None
    note: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    unp: Optional[str] = None
    address: Optional[str] = None
    contact_person: Optional[str] = None
    note: Optional[str] = None

class ClientResponse(ClientBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- СХЕМЫ КАРТОЧКИ ---

class CardBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: str = "Новый запрос"
    total_amount: float = 0.0
    store_location: Optional[str] = None
    due_date: Optional[date] = None
    priority: Optional[int] = 0
    sender_email: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v):
        if len(v) > 200:
            raise ValueError("Название не может превышать 200 символов")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        VALID = {"Новый запрос", "В работе", "Ждет оплаты", "Сборка", "На списание", "Закрыто"}
        if v not in VALID:
            raise ValueError(f"Недопустимый статус: {v}")
        return v

class CardCreate(CardBase):
    client_id: Optional[int] = None
    tag_ids: Optional[List[int]] = []

class CardStatusUpdate(BaseModel):
    status: str


class CardUpdateStatus(BaseModel):
    status: str

class CardResponse(CardBase):
    id: int
    owner_id: Optional[int] = None
    client_id: Optional[int] = None
    created_at: datetime
    is_deleted: bool = False
    attachments: List[AttachmentResponse] = []
    checklists: List[ChecklistResponse] = []
    owner: Optional[UserResponse] = None
    client: Optional[ClientResponse] = None
    tags: List[TagResponse] = []
    model_config = ConfigDict(from_attributes=True)


# --- СХЕМЫ ТРАНЗАКЦИЙ ---

class TransactionBase(BaseModel):
    company_name: str
    amount: float
    store_location: str = ""
    is_calculated: Optional[bool] = False
    is_invoice_issued: Optional[bool] = False
    is_written_off: Optional[bool] = False
    print_status: Optional[str] = "Печать"
    note: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    is_document: bool = False
    is_invoice_doc: Optional[bool] = False
    is_bill_doc: Optional[bool] = False
    is_warehouse_writeoff: Optional[bool] = False

class TransactionCreate(TransactionBase):
    pass

class TransactionUpdate(BaseModel):
    amount: Optional[float] = None          # раньше отсутствовало — правка суммы молча терялась
    store_location: Optional[str] = None
    is_calculated: Optional[bool] = None
    is_invoice_issued: Optional[bool] = None
    is_written_off: Optional[bool] = None
    print_status: Optional[str] = None
    note: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    is_document: Optional[bool] = None
    is_invoice_doc: Optional[bool] = None
    is_bill_doc: Optional[bool] = None
    is_warehouse_writeoff: Optional[bool] = None

class TransactionResponse(TransactionBase):
    id: int
    date: Optional[datetime] = None
    card_id: Optional[int] = None
    is_document: bool = False
    # Сводные поля для СГРУППИРОВАННОГО реестра оплат (одна строка = одна сделка).
    # В обычном (несгруппированном) ответе остаются None и ни на что не влияют.
    parts_count: Optional[int] = None      # сколько записей списания скрыто за строкой
    invoices_count: Optional[int] = None   # из них выписанных накладных
    # Поля ниже оставлены ради совместимости и всегда пусты: реестр оплат
    # больше не читает чек-лист карточки — тот про закупку у поставщиков.
    paid_amount: Optional[float] = None
    is_partial_payment: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)


# --- СХЕМЫ ЛОГА ДЕЙСТВИЙ ---

class ActivityLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    card_id: Optional[int] = None
    action: str
    details: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- ОБНОВЛЕНИЕ КАРТОЧКИ ---

class CardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    total_amount: Optional[float] = None
    store_location: Optional[str] = None
    client_id: Optional[int] = None
    due_date: Optional[date] = None
    priority: Optional[int] = None
    sender_email: Optional[str] = None
    tag_ids: Optional[List[int]] = None
