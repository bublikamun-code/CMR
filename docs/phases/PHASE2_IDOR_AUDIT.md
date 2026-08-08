> **Внимание: план в этом документе применять НЕЛЬЗЯ без переработки.**
>
> Предложенный патч `if card.tenant_id != current_user.tenant_id: raise 403`
> положит CRM: у всех 497 карточек `tenant_id = NULL`, проверка даст 403 на каждой.
>
> Изоляция арендаторов здесь реализована **отдельными файлами БД**
> (`database.py`: `tenants/crm_{tenant_id}.db`), а не колонкой `tenant_id`.
> 13 из 14 роутеров уже используют `get_tenant_db()` — чужие данные им недоступны.
>
> Цифра «40+» получена наивным скриптом (любой `.first()` без слова `tenant_id`
> рядом). Реальный прогон даёт 32, преимущественно ложные срабатывания.
>
> Что действительно стоит проверить — см. [`../../STATUS.md`](../../STATUS.md).

# Phase 2: IDOR Security Audit & Fixes

## Audit Results

**Total potential IDOR issues found: 40+**  
**Files affected: 10**

### Summary by Router

| Router | Issues | Status |
|--------|--------|--------|
| kanban_router.py | 8 | 🔴 HIGH RISK |
| payments_router.py | 7 | 🔴 HIGH RISK |
| card_details_router.py | 2 | 🟡 MEDIUM |
| email_parser_router.py | 2 | 🟡 MEDIUM |
| clients_router.py | ? | TBD |
| suppliers_router.py | ? | TBD |
| custom_objects_router.py | ? | TBD |
| workflows_router.py | ? | TBD |
| tags_router.py | 2 | 🟡 MEDIUM |
| webhooks_router.py | ? | TBD |
| auth_router.py | 3 | 🟢 LOW (user-level, not tenant-specific) |

---

## IDOR Fix Pattern

### Before (Vulnerable)
```python
@router.get("/cards/{card_id}")
def get_card(card_id: int, db: Session = Depends(get_db), 
             current_user: models.User = Depends(get_current_user)):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    
    return card  # ⚠️ IDOR: No tenant check!
```

### After (Secure)
```python
@router.get("/cards/{card_id}")
def get_card(card_id: int, db: Session = Depends(get_db), 
             current_user: models.User = Depends(get_current_user)):
    # Determine tenant context
    tenant_id = current_user.tenant_id if current_user.role != "superadmin" else None
    tdb = get_tenant_db(tenant_id) if tenant_id else db
    
    # Fetch with tenant filtering
    card = tdb.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    
    # Extra validation for non-superadmin users
    if current_user.role != "superadmin" and card.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return card
```

---

## Fix Priority

### 🔴 CRITICAL (Do First)
1. **kanban_router.py** — Card operations (highest risk)
2. **payments_router.py** — Financial data
3. **card_details_router.py** — Card details access

### 🟡 HIGH
4. **clients_router.py** — Client data
5. **suppliers_router.py** — Supplier data
6. **custom_objects_router.py** — Custom data structures

### 🟢 MEDIUM
7. **tags_router.py** — Tag operations
8. **workflows_router.py** — Workflow access
9. **webhooks_router.py** — Webhook configuration

---

## Implementation Checklist

### kanban_router.py (8 issues)
- [ ] Line 53: GET list (add tenant filter)
- [ ] Line 61: GET detail (add tenant check)
- [ ] Line 144-210: All card operations (add tenant validation)
- [ ] Verify `get_tenant_db()` usage

### payments_router.py (7 issues)
- [ ] Line 52: GET list (add tenant filter)
- [ ] Line 205: GET detail (add tenant check)
- [ ] Line 277, 301, 774: Card access (add tenant validation)
- [ ] Line 461, 786: Transaction operations (add tenant validation)

### card_details_router.py (2 issues)
- [ ] Line 45, 77: All queries (add tenant filtering)

### clients_router.py (TBD)
- [ ] Audit all GET/POST/PATCH/DELETE operations
- [ ] Add tenant_id filtering to queries
- [ ] Verify client ownership validation

### suppliers_router.py (TBD)
- [ ] Same as clients_router

### custom_objects_router.py (TBD)
- [ ] Audit all custom object operations
- [ ] Verify tenant isolation

---

## Tenant Filtering Pattern

### Query with tenant filter
```python
# For superadmin (access all tenants)
if current_user.role == "superadmin":
    obj = db.query(Model).filter(Model.id == obj_id).first()
else:
    # For regular users (access only own tenant)
    tenant_id = current_user.tenant_id
    obj = db.query(Model).filter(
        Model.id == obj_id,
        Model.tenant_id == tenant_id
    ).first()
```

### Using get_tenant_db()
```python
# More concise approach
tenant_id = current_user.tenant_id if current_user.role != "superadmin" else None
tdb = get_tenant_db(tenant_id) if tenant_id else db

# Now queries are automatically tenant-scoped
obj = tdb.query(Model).filter(Model.id == obj_id).first()
```

---

## Testing IDOR Fixes

After fixing each endpoint:

```bash
# 1. Test as non-superadmin with own data
curl -H "Authorization: Bearer $USER_TOKEN" \
  http://localhost:8000/api/cards/1

# 2. Test with someone else's data (should fail with 403)
curl -H "Authorization: Bearer $USER_TOKEN" \
  http://localhost:8000/api/cards/999

# 3. Test as superadmin (should access any tenant's data)
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8000/api/cards/999
```

---

## Timeline

- **Day 1:** Fix kanban_router.py (8 issues) + payments_router.py (7 issues)
- **Day 2:** Fix remaining critical endpoints (clients, suppliers, custom_objects)
- **Day 3:** Test all fixes + create test cases
- **Day 4:** Code review + merge

---

## Files to Modify

1. `server_snapshot/routers/kanban_router.py`
2. `server_snapshot/routers/payments_router.py`
3. `server_snapshot/routers/card_details_router.py`
4. `server_snapshot/routers/clients_router.py`
5. `server_snapshot/routers/suppliers_router.py`
6. `server_snapshot/routers/custom_objects_router.py`
7. `server_snapshot/routers/workflows_router.py`
8. `server_snapshot/routers/webhooks_router.py`
9. `server_snapshot/routers/tags_router.py`
10. `server_snapshot/routers/email_parser_router.py`

---

## Automation Helper

Run audit periodically:
```bash
python3 scripts/audit_idor.py
```

---

## Next: Git History Cleanup

After IDOR fixes, remove `.secret_key` from git:

```bash
# Using filter-branch (built-in)
git filter-branch --tree-filter 'rm -f server_snapshot/routers/.secret_key' -- --all

# Or using BFG (faster)
bfg --delete-files .secret_key
git reflog expire --expire=now --all && git gc --prune=now --aggressive
git push --force-with-lease origin main
```

---

**Ready to begin Phase 2 fixes?**
