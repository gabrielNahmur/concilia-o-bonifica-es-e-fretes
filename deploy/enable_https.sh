#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/ubuntu/apps/contracts-gbi"
CRM_DIR="/home/ubuntu/apps/crm-gbi-whatsapp"
DOMAIN="contratos.atendimento-gbi.online"
EXPECTED_IP="54.172.162.33"

if ! getent ahostsv4 "$DOMAIN" | awk '{print $1}' | grep -qx "$EXPECTED_IP"; then
    echo "DNS ainda não aponta $DOMAIN para $EXPECTED_IP" >&2
    exit 1
fi

docker run --rm \
    -v "$CRM_DIR/certbot/conf:/etc/letsencrypt" \
    -v "$CRM_DIR/certbot/www:/var/www/certbot" \
    certbot/certbot certonly --webroot -w /var/www/certbot \
    -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email

if ! grep -q 'ssl_certificate /etc/letsencrypt/live/contratos.atendimento-gbi.online' "$CRM_DIR/frontend/nginx.prod.conf"; then
    cat "$APP_DIR/deploy/nginx-contracts.ssl.conf" >> "$CRM_DIR/frontend/nginx.prod.conf"
fi
docker exec crm_frontend nginx -t
docker exec crm_frontend nginx -s reload
curl -fsS "https://$DOMAIN/api/health"
printf '\n'
