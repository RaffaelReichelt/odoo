from odoo import models


class LLMResource(models.Model):
    _inherit = 'llm.resource'

    def _get_record_external_url(self, res_model, res_id):
        # Gleiches Muster wie llm_document_page fuer document.page: liefert die
        # externe URL fuer website.page-Ressourcen, damit sie im Kanban/Formular
        # korrekt verlinkt sind.
        if res_model == 'website.page':
            record = self.env[res_model].browse(res_id)
            if record.exists() and record.llm_source_url:
                return record.llm_source_url
        return super()._get_record_external_url(res_model, res_id)
