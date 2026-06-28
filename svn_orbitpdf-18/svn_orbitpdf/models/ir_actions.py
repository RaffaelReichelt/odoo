# -*- coding: utf-8 -*-
# Copyright 2026 Sveltware Solutions

import io
import json
import logging
import plutoprint

from odoo import api, models, fields
from odoo.tools.safe_eval import safe_eval, time

from collections import OrderedDict
from PIL import Image

_logger = logging.getLogger(__name__)


def orbit_css_meta(css: str, marker='/* ob-data'):
    i = css.find(marker)
    if i < 0:
        return {}, css
    j = css.find('*/', i + len(marker))
    if j < 0:
        raise ValueError(f'{marker} missing closing */')

    body = css[i + len(marker) : j].strip().rstrip(',')
    data = json.loads('{' + body + '}') if body else {}

    return data, css[:i] + css[j + 2 :]


class IrActionReport(models.AbstractModel):
    _inherit = 'ir.actions.report'

    u_pdf_engine = fields.Selection(
        [
            ('default', 'Default'),
            ('pluto', 'PlutoPrint'),
        ],
        string='Render Engine',
    )

    @api.model
    def _render_qweb_html(self, report_ref, docids, data=None):
        if data and data.get('orbitpdf'):
            report = self._get_report(report_ref).sudo()
            return self._pluto_render_qweb_html(report, docids, data), 'html'

        return super()._render_qweb_html(report_ref, docids, data)

    def _orbit_paged_media(self):
        paper_format = self.get_paperformat()
        return paper_format.css_media or paper_format._def_css_media()

    def _pluto_render_qweb_html(self, report, res_ids, data=None, base_url=None):
        data = dict(data or {})
        data.setdefault('report_type', 'html')
        data.setdefault('full_width', True)
        data.setdefault('ob_assets_pdf', True)

        data = self._get_rendering_context(report, res_ids, data)

        base_url = base_url or self._get_report_url()
        css_media = report._orbit_paged_media()
        extr_data, extr_css = orbit_css_meta(css_media)
        data.update({'ob_url': base_url, 'ob_css': extr_css, **extr_data})

        return self._render_template(report.report_name, data)

    def _render_qweb_pdf_prepare_streams(self, report_ref, data, res_ids=None):
        """Override to use PlutoPrint engine when configured"""
        report_sudo = self._get_report(report_ref)

        # Fallback to default Odoo behavior if not using PlutoPrint
        if report_sudo.u_pdf_engine != 'pluto' and 'orbitpdf' not in data:
            return super()._render_qweb_pdf_prepare_streams(report_ref, data, res_ids)

        collected_streams = OrderedDict()

        # ---------------------------------------------------------------------
        # Standard Odoo logic for data preparation and attachment handling
        # ---------------------------------------------------------------------

        has_duplicated_ids = res_ids and len(res_ids) != len(set(res_ids))

        # Retrieve existing attachments
        if res_ids:
            records = self.env[report_sudo.model].browse(res_ids)
            for record in records:
                res_id = record.id
                if res_id in collected_streams:
                    continue

                stream = None
                attachment = None

                if not has_duplicated_ids and report_sudo.attachment and not self._context.get('report_pdf_no_attachment'):
                    attachment = report_sudo.retrieve_attachment(record)

                    # Extract the stream from the attachment
                    if attachment and report_sudo.attachment_use:
                        stream = io.BytesIO(attachment.raw)

                        # Ensure the stream can be saved in Image
                        if attachment.mimetype.startswith('image'):
                            img = Image.open(stream)
                            new_stream = io.BytesIO()
                            img.convert('RGB').save(new_stream, format='pdf')
                            stream.close()
                            stream = new_stream

                collected_streams[res_id] = {'stream': stream, 'attachment': attachment}
                if res_title := report_sudo.print_report_name:
                    collected_streams[res_id]['title'] = safe_eval(res_title, {'object': record, 'time': time})

        # ---------------------------------------------------------------------
        # PlutoPrint rendering for records without existing streams
        # ---------------------------------------------------------------------

        if has_duplicated_ids or not res_ids:
            # Sentinel for single whole-document render (core-compatible)
            res_ids_wo_stream = [False]
            collected_streams[False] = {'attachment': None}
        else:
            res_ids_wo_stream = [res_id for res_id, stream_data in collected_streams.items() if not stream_data['stream']]

        base_url = self._get_report_url()

        for res_id in res_ids_wo_stream:
            res_data = {'title': collected_streams[res_id].get('title') if res_id else False, **data}
            html = self._pluto_render_qweb_html(report_sudo, [res_id] if res_id else [], res_data, base_url)

            book = plutoprint.Book()
            book.load_data(html, base_url=base_url)

            pdf_stream = io.BytesIO()
            book.write_to_pdf_stream(pdf_stream)
            collected_streams[res_id]['stream'] = pdf_stream

        return collected_streams
