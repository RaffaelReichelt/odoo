#!/bin/bash
# Starte den Cron-Dienst im Hintergrund
# service cron start

# Starte Odoo
exec /usr/src/odoo/community/odoo-bin --config=/run/secrets/odoo_conf   