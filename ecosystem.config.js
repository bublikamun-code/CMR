// PM2-дескриптор обоих боевых приложений. Единственный механизм запуска
// (см. HANDOFF.md): watchdog (cron */5) делает pm2 resurrect из дампа, при
// его неудаче — pm2 start ecosystem.config.js.
//
// ВАЖНО про env nakladnye-bot: TELEGRAM_BOT_TOKEN и OPENAI_API_KEY НЕ лежат
// ни здесь, ни в .pm2.env (только CRM_DATA_DIR/CRM_SECRET_KEY/CRM_UPLOADS_DIR/
// PORT) — они живут в окружении процесса, которым бота когда-то стартовали,
// и pm2 хранит их в дампе. Поэтому:
//   - рестарт бота — ТОЛЬКО «pm2 restart nakladnye-bot» БЕЗ --update-env
//     (scripts/deploy.sh так и делает);
//   - после правок этого файла нужен «pm2 save», иначе resurrect вернёт
//     старое описание.
module.exports = {
  apps: [{
    name: "crm",
    script: "server.py",
    interpreter: "/var/www/h212005/data/www/cmr-svetvdome.online/.venv/bin/python",
    cwd: "/var/www/h212005/data/www/cmr-svetvdome.online",
    max_restarts: 10,
    restart_delay: 3000,
    max_memory_restart: "512M",
    env: {
      PORT: "20008",
      PYTHONUNBUFFERED: "1"
    },
    autorestart: true,
    watch: false,
    wait_ready: false,
    listen_timeout: 30000
  }, {
    name: "nakladnye-bot",
    script: "telegram_nakladnye_bot.py",
    interpreter: "/var/www/h212005/data/www/cmr-svetvdome.online/.venv/bin/python",
    cwd: "/var/www/h212005/data/www/cmr-svetvdome.online",
    autorestart: true,
    watch: false
    // env намеренно пуст: токены бота — в окружении процесса (см. шапку).
  }]
};
