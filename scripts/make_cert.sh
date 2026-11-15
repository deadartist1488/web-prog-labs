#!/usr/bin/env sh
# Самоподписанный сертификат для локального HTTPS (лаба 8). В проде вместо него — Let's Encrypt:
#   certbot certonly --webroot -w /var/www/certbot -d your-domain.com
set -e
DIR="$(dirname "$0")/../nginx/certs"
mkdir -p "$DIR"
MSYS_NO_PATHCONV=1 openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout "$DIR/privkey.pem" -out "$DIR/fullchain.pem" \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,DNS:library.local,IP:127.0.0.1"
echo "certificate written to $DIR"
