#!/bin/sh
# Arranca el servidor de archivos de reports/ (PB-15) con autenticación básica.
# La contraseña llega por variable de entorno y solo se guarda su hash (bcrypt) en memoria.
set -eu

if [ -z "${ACR_FILES_USER:-}" ] || [ -z "${ACR_FILES_PASSWORD:-}" ]; then
	echo "Faltan ACR_FILES_USER o ACR_FILES_PASSWORD: el servidor de archivos no acepta acceso anónimo." >&2
	exit 1
fi

ACR_FILES_HASH="$(caddy hash-password --plaintext "$ACR_FILES_PASSWORD")"
export ACR_FILES_HASH
unset ACR_FILES_PASSWORD

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
