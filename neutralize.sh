#!/bin/bash
# This script is used to neutralize the Odoo database 
source /Users/raffael/Projekte/PrivateMind/.venv/bin/activate
./community/odoo-bin neutralize -d odoo --db_port 54321 -r odoo -w odoo --db_host localhost