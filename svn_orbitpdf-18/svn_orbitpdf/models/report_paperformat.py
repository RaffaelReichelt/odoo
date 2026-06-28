# -*- coding: utf-8 -*-
# Copyright 2026 Sveltware Solutions

from odoo import fields, models


class ReportPaperformat(models.Model):
    _inherit = 'report.paperformat'

    active = fields.Boolean(default=True)
    css_media = fields.Text(string='CSS Paged Media')

    def _def_css_media(self):
        orient = 'landscape' if self.orientation == 'Landscape' else 'portrait'
        if self.format and self.format != 'custom':
            size_css = f'size: {self.format} {orient};'
        else:
            size_css = f'size: {self.print_page_width:g}mm {self.print_page_height:g}mm;'

        margin_css = f'margin: {self.margin_top:g}mm {self.margin_right:g}mm {self.margin_bottom:g}mm {self.margin_left:g}mm;'

        return f'\n@page{{{size_css}\n{margin_css}}}'
