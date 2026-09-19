# ЗАМОРОЖЕНО: старый фронт (legacy)

С 19.09.2026 основной фронт CRM — v2: прод-каталог `site-v2/`,
исходники `server_snapshot/tools/mockups/` + шаблоны `tools/v2-{api,boot}-template.js`,
сборка `python3 server_snapshot/tools/build_site_v2.py`.
Этот каталог (`site/`) больше не развивается: только критические
security-фиксы.

- На проде живёт по адресу `/legacy` (старая админка — `/legacy/admin`);
  `/` и `/admin` ведут в v2.
- Осознанный откат корня на старый фронт: `CRM_FRONTEND=legacy` в
  `.pm2.env` на сервере + `pm2 restart crm`. Дефолт в коде — `"v2"`,
  поэтому сброс окружения сам по себе откат не делает.
- Правки сюда не вносить: они разойдутся с продом при следующем
  `deploy.sh front` и вернут исчезнувшие дефекты (см.
  `docs/audits/V2-FIX-PLAN-2026-09-19.md`, Пакет G).
