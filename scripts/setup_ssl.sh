#!/bin/bash
# Obtain TLS certificates for the CRM frontend.
#
#   bash scripts/setup_ssl.sh example.com admin@example.com   # Let's Encrypt
#   bash scripts/setup_ssl.sh --self-signed                   # local testing only
#
# Produces ssl/cert.pem and ssl/key.pem, the paths nginx/https.conf expects.
#
# Let's Encrypt mode uses the webroot challenge, so port 80 must be reachable
# from the internet and DNS for the domain must already point at this host.

set -euo pipefail

cd "$(dirname "$0")/.."

SSL_DIR="ssl"
WEBROOT="certbot-webroot"

mkdir -p "$SSL_DIR"

if [ "${1:-}" = "--self-signed" ]; then
    echo "==> Generating a self-signed certificate (browsers will warn - testing only)"
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout "$SSL_DIR/key.pem" \
        -out "$SSL_DIR/cert.pem" \
        -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null
    chmod 600 "$SSL_DIR/key.pem"
    echo "==> Done: $SSL_DIR/cert.pem, $SSL_DIR/key.pem"
    echo "    Self-signed certificates are for local testing. Never use in production."
    exit 0
fi

DOMAIN="${1:-}"
EMAIL="${2:-}"

if [ -z "$DOMAIN" ]; then
    echo "Usage: bash scripts/setup_ssl.sh <domain> [email]" >&2
    echo "       bash scripts/setup_ssl.sh --self-signed" >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is required to run certbot." >&2
    exit 1
fi

mkdir -p "$WEBROOT"

echo "==> Requesting a certificate for $DOMAIN from Let's Encrypt"
echo "    Port 80 must be reachable and $DOMAIN must resolve to this host."

EMAIL_ARG="--register-unsafely-without-email"
if [ -n "$EMAIL" ]; then
    EMAIL_ARG="--email $EMAIL"
fi

docker run --rm \
    -v "$PWD/letsencrypt:/etc/letsencrypt" \
    -v "$PWD/$WEBROOT:/var/www/certbot" \
    certbot/certbot certonly \
    --webroot -w /var/www/certbot \
    -d "$DOMAIN" \
    $EMAIL_ARG \
    --agree-tos --non-interactive

LIVE="letsencrypt/live/$DOMAIN"
if [ ! -f "$LIVE/fullchain.pem" ]; then
    echo "ERROR: certbot did not produce $LIVE/fullchain.pem" >&2
    exit 1
fi

# Copy rather than symlink: the paths are bind-mounted into the container, where
# a symlink pointing outside the mount would not resolve.
cp "$LIVE/fullchain.pem" "$SSL_DIR/cert.pem"
cp "$LIVE/privkey.pem"   "$SSL_DIR/key.pem"
chmod 600 "$SSL_DIR/key.pem"

echo "==> Installed: $SSL_DIR/cert.pem, $SSL_DIR/key.pem"
cat <<NOTES

Next steps:
  1. Set server_name in nginx/https.conf to $DOMAIN
  2. Switch the frontend to the TLS config in docker-compose.production.yml:
       - ./nginx/https.conf:/etc/nginx/conf.d/default.conf:ro
  3. docker compose -f docker-compose.production.yml up -d

Renewal (certificates last 90 days) - add to crontab:
  0 3 * * 1 cd $PWD && bash scripts/setup_ssl.sh $DOMAIN $EMAIL && docker compose -f docker-compose.production.yml restart frontend
NOTES
