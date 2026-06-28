#!/bin/bash
source /Users/raffael/Projekte/PrivateMind/.venv/bin/activate
./community/odoo-bin --db_port 54321 -d odoo \
    -r odoo \
    -w odoo \
    --db_host localhost \
    -i base,l10n_de \
    --load-language=de_DE \
    --stop-after-init \
    --without-demo=all