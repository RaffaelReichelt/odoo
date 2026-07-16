#!/bin/bash
set -e

CONFIG_FILE=/tmp/odoo.conf

# [options] aus Umgebungsvariablen generieren
{
  echo "[options]"
  echo "addons_path      = ${ODOO_ADDONS_PATH}"
  echo "db_port          = ${ODOO_DB_PORT:-5432}"
  echo "db_user          = ${ODOO_DB_USER:-odoo}"
  echo "db_name          = ${ODOO_DB_NAME:-odoo}"
  echo "data_dir         = ${ODOO_DATA_DIR:-/usr/src/odoo/data}"
  echo "list_db          = ${ODOO_LIST_DB:-True}"
  echo "proxy_mode       = ${ODOO_PROXY_MODE:-True}"
  echo "db_maxconn       = ${ODOO_DB_MAXCONN:-100}"
  echo "max_cron_threads = ${ODOO_MAX_CRON_THREADS:-2}"
  echo "max_connections  = ${ODOO_MAX_CONNECTIONS:-1000}"
  echo "session_timeout  = ${ODOO_SESSION_TIMEOUT:-60}"
  echo "session_gc       = ${ODOO_SESSION_GC:-60}"
} > "${CONFIG_FILE}"

# Sensitive Werte aus Secret anhängen (Sektions-Header überspringen)
if [ -f /run/secrets/odoo_conf ]; then
  grep -Ev '^\s*(\[|#|$)' /run/secrets/odoo_conf >> "${CONFIG_FILE}"
fi

exec /usr/src/odoo/community/odoo-bin --config="${CONFIG_FILE}"