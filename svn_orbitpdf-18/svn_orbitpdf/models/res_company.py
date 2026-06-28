# -*- coding: utf-8 -*-
# Copyright 2026 Sveltware Solutions

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    font = fields.Selection(
        selection_add=[
            ('Inter', 'Inter'),
            ('Roboto',),
            ('Open_Sans',),
            ('SourceSans3', 'Source Sans 3'),
            ('SourceSerif4', 'Source Serif 4'),
            ('IBMPlexSans', 'IBM Plex Sans'),
        ]
    )
