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
  }]
};
