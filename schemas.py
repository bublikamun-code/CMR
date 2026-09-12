from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# --- СХЕМЫ ПОЛЬЗОВАТЕЛЯ ---

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str
    role: str = "manager"

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Пароль должен содержать минимум 8 символов")
        return v

class UserResponse(UserBase):
    id: int
    role: str
    tenant_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

class UserUpdate(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if v and len(v) < 8:
            raise ValueError("Пароль должен содержать минимум 8 символов")
        return v

class PasswordChange(BaseModel):
    """Самостоятельная смена собственного пароля: старый обязателен."""
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v):
        if len(v) < 8:
            raise ValueError("Новый пароль должен содержать минимум 8 символов")
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

# Единственный источник правды по статусам сделки. Колонки канбана
# (js/kanban.js KANBAN_COLUMNS), доска списаний и фильтры «Реестра оплат»
# работают от этого же набора: статус вне списка означает, что карточка
# не попадёт ни на одну доску.
CARD_STATUSES = ("Новый запрос", "В работе", "Ждет оплаты", "Сборка",
                 "На списание", "Закрыто")


class CardBase(BaseModel):
    title: str
    description: Optional[str] = None
    status: str = "Новый запрос"
    total_amount: float = 0.0
    paid_amount: float = 0.0
    payment_status: str = "Не оплачен"
    payment_due_date: Optional[date] = None
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
        if v not in CARD_STATUSES:
            raise ValueError(f"Недопустимый статус: {v}")
        return v

    @field_validator("payment_status")
    @classmethod
    def validate_payment_status(cls, v):
        VALID = {"Не оплачен", "Частично", "Оплачен", "Отсрочка"}
        if v not in VALID:
            raise ValueError(f"Недопустимый статус оплаты: {v}")
        return v

class CardCreate(CardBase):
    client_id: Optional[int] = None
    owner_id: Optional[int] = None
    tag_ids: Optional[List[int]] = []

class CardReorder(BaseModel):
    status: str
    card_ids: List[int]

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in CARD_STATUSES:
            raise ValueError(f"Недопустимый статус: {v}")
        return v

    @field_validator("card_ids")
    @classmethod
    def validate_card_ids(cls, v):
        if not v:
            raise ValueError("Список карточек пуст")
        if len(v) > 500:
            raise ValueError("Слишком много карточек")
        if len(set(v)) != len(v):
            raise ValueError("Есть дубли")
        return v

class CardStatusUpdate(BaseModel):
    status: str


class CardUpdateStatus(BaseModel):
    """Рабочая схема PATCH /kanban/cards/{id}/status.

    FIX 2026-09-12 (Фаза 2, дефект 10): раньше здесь было голое `status: str`,
    поэтому перенос карточки с доски с опечаткой или левым значением сохранял
    мусор в cards.status. Карточка с неизвестным статусом не попадает ни в одну
    колонку канбана (js/kanban.js KANBAN_COLUMNS) и исчезает из интерфейса,
    а вернуть её можно было только правкой базы. Множество статусов берётся из
    CARD_STATUSES — того же, что проверяют CardBase и CardReorder.
    """
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in CARD_STATUSES:
            raise ValueError(f"Недопустимый статус: {v}")
        return v

class CardResponse(CardBase):
    id: int
    owner_id: Optional[int] = None
    client_id: Optional[int] = None
    writeoff_group_id: Optional[int] = None
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
    is_secondary_check: Optional[bool] = False
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
    # FIX 2026-09-12 (Фаза 2, дефект 13): три поля были в модели, но не в схеме,
    # поэтому PATCH /payments/transactions/{id} отвечал 200 и НИЧЕГО не менял.
    #
    # date — самое заметное: js/payments.js правит дату оплаты инлайн-редактором
    # и шлёт {"date": "YYYY-MM-DD"}, а в update_transaction_checkboxes уже лежал
    # разбор этой строки, недостижимый из-за отсутствия поля в схеме. Формат
    # строковый (как у invoice_date) именно потому, что роутер соединяет
    # присланный день с сохранённым временем записи.
    #
    # company_name — ОГРАНИЧЕНИЕ: card_details_router.update_card при смене
    # названия сделки перезаписывает company_name ВО ВСЕХ её транзакциях
    # (денормализация так задумана, см. PLAN_MODERNIZATION.md Фаза 2 п.1).
    # То есть ручная правка подписи одной записи держится только до следующего
    # переименования сделки. Поле добавлено, потому что записи без сделки
    # (card_id is None) и исправление опечаток в подписи иначе невозможны;
    # менять саму синхронизацию — продуктовое решение, а не починка пробела.
    #
    # is_secondary_check — добавлено и в TransactionBase, чтобы поле
    # читалось обратно: записываемое, но невидимое значение фронт не смог бы
    # ни показать, ни корректно переключить.
    date: Optional[str] = None
    company_name: Optional[str] = None
    is_secondary_check: Optional[bool] = None

class TransactionResponse(TransactionBase):
    id: int
    date: Optional[datetime] = None
    card_id: Optional[int] = None
    writeoff_group_id: Optional[int] = None
    is_document: bool = False
    # Сводные поля для СГРУППИРОВАННОГО реестра оплат (одна строка = одна сделка).
    # В обычном (несгруппированном) ответе остаются None и ни на что не влияют.
    parts_count: Optional[int] = None      # сколько записей списания скрыто за строкой
    invoices_count: Optional[int] = None   # из них выписанных накладных
    part_ids: Optional[List[int]] = None   # id всех частей сделки для массового обновления флагов
    # Поля ниже оставлены ради совместимости и всегда пусты: реестр оплат
    # больше не читает чек-лист карточки — тот про закупку у поставщиков.
    paid_amount: Optional[float] = None
    payment_status: Optional[str] = None
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
    user_name: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# --- ОБНОВЛЕНИЕ КАРТОЧКИ ---

class CardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    total_amount: Optional[float] = None
    paid_amount: Optional[float] = None
    payment_status: Optional[str] = None
    payment_due_date: Optional[date] = None
    store_location: Optional[str] = None
    client_id: Optional[int] = None
    due_date: Optional[date] = None
    priority: Optional[int] = None
    sender_email: Optional[str] = None
    tag_ids: Optional[List[int]] = None
    # FIX 2026-09-12 (Фаза 2, дефект 13): владелец сделки назначался только при
    # создании (kanban_router.create_card), переназначить его было нельзя —
    # поле отсутствовало в схеме, и PATCH отвечал 200, ничего не меняя.
    # Отпускание сделки (owner_id=null) тоже проходит через это поле: у FK
    # ondelete="SET NULL", а auth_router.remove_user уже обнуляет owner_id
    # у карточек удаляемого пользователя, так что NULL — штатное значение.
    owner_id: Optional[int] = None

    @field_validator("payment_status")
    @classmethod
    def validate_payment_status(cls, v):
        if v is None:
            return v
        VALID = {"Не оплачен", "Частично", "Оплачен", "Отсрочка"}
        if v not in VALID:
            raise ValueError(f"Недопустимый статус оплаты: {v}")
        return v

class CardPaymentUpdate(BaseModel):
    paid_amount: Optional[float] = None
    payment_status: Optional[str] = None
    payment_due_date: Optional[date] = None

    @field_validator("payment_status")
    @classmethod
    def validate_payment_status(cls, v):
        if v is None:
            return v
        VALID = {"Не оплачен", "Частично", "Оплачен", "Отсрочка"}
        if v not in VALID:
            raise ValueError(f"Недопустимый статус оплаты: {v}")
        return v


class WriteoffGroupCard(BaseModel):
    id: int
    title: str
    total_amount: float
    status: str
    model_config = ConfigDict(from_attributes=True)

class WriteoffGroupCreate(BaseModel):
    card_ids: List[int]
    name: Optional[str] = None

class WriteoffGroupResponse(BaseModel):
    id: int
    name: str
    client_id: Optional[int] = None
    store_location: Optional[str] = None
    total_amount: float
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    written_off: bool
    cards: List[WriteoffGroupCard] = []
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- Задачи ---

TASK_STATUSES = ("todo", "in_work", "done")

class TaskAssignee(BaseModel):
    id: int
    username: str

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    status: str = "todo"
    due_date: Optional[datetime] = None
    # FIX 2026-09-12 (Фаза 2, дефект 13): priority есть в модели и ОТДАЁТСЯ
    # клиенту в TaskResponse, но задать его было нельзя — ни при создании,
    # ни правкой. Дефолт модели (0) совпадает с дефолтом здесь.
    priority: Optional[int] = 0
    assignee_id: Optional[int] = None
    card_id: Optional[int] = None
    client_id: Optional[int] = None

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: Optional[int] = None
    assignee_id: Optional[int] = None
    card_id: Optional[int] = None
    client_id: Optional[int] = None

class TaskChecklistItemCreate(BaseModel):
    title: str

class TaskChecklistItemUpdate(BaseModel):
    title: Optional[str] = None
    is_done: Optional[bool] = None

class TaskChecklistItemResponse(BaseModel):
    id: int
    title: str
    is_done: bool
    model_config = ConfigDict(from_attributes=True)

class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    status: str
    due_date: Optional[datetime] = None
    priority: int
    assignee_id: Optional[int] = None
    creator_id: Optional[int] = None
    card_id: Optional[int] = None
    client_id: Optional[int] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    assignee_username: Optional[str] = None
    creator_username: Optional[str] = None
    card_title: Optional[str] = None
    client_name: Optional[str] = None
    checklist: List[TaskChecklistItemResponse] = []
    model_config = ConfigDict(from_attributes=True)


# --- Уведомления ---

class NotificationReadRequest(BaseModel):
    ids: List[int]

class NotificationResponse(BaseModel):
    id: int
    type: str
    title: str
    details: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class NotificationsListResponse(BaseModel):
    unread_count: int
    items: List[NotificationResponse]


# --- Накладные ---

NAKLADNYE_STATUSES = ("new", "verified", "arrived", "paid")
NAKLADNYE_DOC_TYPES = ("ТН", "ТТН", "УПД")


# Общие проверки накладной. Вынесены в функции, потому что NakladnayaUpdate
# НЕ наследует NakladnayaBase (у Update все поля Optional=None, у Base есть
# значения по умолчанию), а правила должны быть одинаковыми в обоих: иначе
# мусор, который не проходит при создании, проходил бы при правке.

def _check_nakladnaya_status(v):
    """FIX 2026-09-12 (Фаза 2, дефект 12): NAKLADNYE_STATUSES была объявлена,
    но ни одним валидатором не использовалась — в status сохранялась любая строка.

    Статус здесь не украшение: по нему работает фильтр таблицы накладных
    (js/nakladnye.js сравнивает r.status === значение селекта) и переключатели
    «Проверена / Приехала / Оплачена» пишут строго 'verified'/'arrived'/'paid'.
    Запись с левым статусом не находится ни одним фильтром.

    None пропускаем: в NakladnayaUpdate все поля опциональны, а эндпоинты пишут
    только model_dump(exclude_unset=True), то есть None означает «не прислали».
    """
    if v is None:
        return v
    v = v.strip()
    if v not in NAKLADNYE_STATUSES:
        raise ValueError(
            f"Недопустимый статус накладной: {v!r}. "
            f"Допустимые: {', '.join(NAKLADNYE_STATUSES)}"
        )
    return v


def _check_nakladnaya_doc_type(v):
    """FIX 2026-09-12 (Фаза 2, дефект 12): то же для типа документа.

    Пустая строка приводится к None, а не отклоняется. Telegram-бот шлёт
    doc_type="" в тех случаях, когда OCR не распознал тип (payload собирается
    как first.get("doc_type", "")), а в селекте модалки CRM есть пункт «—»
    с пустым значением. Отказ означал бы, что накладная с нераспознанным типом
    не сохраняется вообще — потеря документа ради чистоты справочника.

    Регистр и пробелы нормализуются: значение приходит из OCR, а фронт
    сравнивает строки посимвольно (фильтр таблицы и подстановка в селект),
    поэтому "ттн" не совпало бы с "ТТН" и запись выглядела бы без типа.
    """
    if v is None:
        return v
    v = v.strip().upper()
    if not v:
        return None
    if v not in NAKLADNYE_DOC_TYPES:
        raise ValueError(
            f"Недопустимый тип документа: {v!r}. "
            f"Допустимые: {', '.join(NAKLADNYE_DOC_TYPES)}"
        )
    return v


def _check_nakladnaya_amounts(amount, vat_amount):
    """FIX 2026-09-12 (Фаза 2, дефект 11): amount >= vat_amount.

    amount — «Стоимость с НДС», vat_amount — «Сумма НДС» ВНУТРИ этой стоимости,
    поэтому НДС больше итога документа быть не может физически. Проверка жила
    только в telegram-боте и там не отклоняла, а молча меняла значения местами;
    CRM-эндпоинты принимали что угодно, и в базе оказывалась накладная,
    у которой НДС превышает сумму.

    Отказ мягкий, когда хотя бы одно из полей не задано: оба поля опциональны
    (OCR распознаёт не всё, а в модалке CRM их можно оставить пустыми), и
    сравнивать нечего. Бот к тому же меняет amount и vat_amount местами ДО
    отправки — но только когда оба значения непустые, поэтому согласованность
    присланной пары он уже гарантирует.

    Сравнение по round(x, 2): колонки REAL/Numeric, а сумма и НДС часто
    вычислены независимо, и расхождение в третий знак после запятой
    содержательной ошибкой не является.
    """
    if amount is None or vat_amount is None:
        return
    if round(float(amount), 2) < round(float(vat_amount), 2):
        raise ValueError(
            f"НДС ({round(float(vat_amount), 2):.2f}) не может превышать сумму "
            f"документа с НДС ({round(float(amount), 2):.2f})"
        )


class NakladnayaBase(BaseModel):
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    doc_type: Optional[str] = None
    doc_series: Optional[str] = None
    doc_number: Optional[str] = None
    doc_date: Optional[str] = None
    amount: Optional[float] = None
    vat_amount: Optional[float] = None
    # FIX 2026-09-12 (Фаза 2, дефект 14): колонка есть в боевом DDL, но отсутствовала
    # и в models.py, и здесь — клиент физически не мог её заполнить.
    amount_no_vat: Optional[float] = None
    unload_address: Optional[str] = None
    store: Optional[str] = None
    is_verified: bool = False
    is_arrived: bool = False
    is_paid: bool = False
    status: str = "new"
    products: Optional[List[dict]] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        return _check_nakladnaya_status(v)

    @field_validator("doc_type")
    @classmethod
    def validate_doc_type(cls, v):
        return _check_nakladnaya_doc_type(v)

    @model_validator(mode="after")
    def validate_amounts(self):
        _check_nakladnaya_amounts(self.amount, self.vat_amount)
        return self

class NakladnayaCreate(NakladnayaBase):
    pass

class NakladnayaUpdate(BaseModel):
    supplier_id: Optional[int] = None
    supplier_name: Optional[str] = None
    doc_type: Optional[str] = None
    doc_series: Optional[str] = None
    doc_number: Optional[str] = None
    doc_date: Optional[str] = None
    amount: Optional[float] = None
    vat_amount: Optional[float] = None
    unload_address: Optional[str] = None
    store: Optional[str] = None
    is_verified: Optional[bool] = None
    is_arrived: Optional[bool] = None
    is_paid: Optional[bool] = None
    status: Optional[str] = None
    products: Optional[List[dict]] = None
    amount_no_vat: Optional[float] = None   # дефект 14: как и в NakladnayaBase

    # Те же правила, что и при создании (дефект 12). Без них мусор, который
    # не прошёл в POST /nakladnye, свободно записывался бы через PATCH —
    # а именно PATCH используют переключатели «Проверена/Приехала/Оплачена».
    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        return _check_nakladnaya_status(v)

    @field_validator("doc_type")
    @classmethod
    def validate_doc_type(cls, v):
        return _check_nakladnaya_doc_type(v)

    @model_validator(mode="after")
    def validate_amounts(self):
        # ОГРАНИЧЕНИЕ (дефект 11): схема видит только присланные поля, поэтому
        # частичная правка одного из пары (например только vat_amount=500 при
        # сохранённом amount=100) здесь непроверяема в принципе — значения
        # живут в разных местах. Такую правку отсекает nakladnye_router
        # (_ensure_amounts_consistent), сравнивая присланное со строкой базы.
        _check_nakladnaya_amounts(self.amount, self.vat_amount)
        return self

class SupplierBrief(BaseModel):
    id: int
    name: str
    model_config = ConfigDict(from_attributes=True)

class NakladnayaResponse(NakladnayaBase):
    # Наследует валидаторы NakladnayaBase. Схема мертва (KNOWN_DEAD_SCHEMAS в
    # tests/test_schema_parity.py): nakladnye_router отдаёт dict из _nak_dict,
    # поэтому легас-строки с мусорным status/doc_type не ломают чтение.
    # Если схему когда-нибудь подключат как response_model — это станет
    # проверкой и на выход, и старые грязные записи придётся сначала почистить.
    id: int
    photo_paths: Optional[List[str]] = None
    created_by_bot: bool = False
    created_at: datetime
    supplier: Optional[SupplierBrief] = None
    model_config = ConfigDict(from_attributes=True)
