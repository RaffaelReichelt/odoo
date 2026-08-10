import logging

from odoo import _, models

from odoo.addons.llm_tool.decorators import llm_tool

_logger = logging.getLogger(__name__)

LIVECHAT_SOURCE_XMLID = 'im_livechat_llm_bot.utm_source_livechat_bot'


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    @llm_tool(destructive_hint=True)
    def create_lead_from_livechat(
        self,
        description: str,
        contact_name: str = "",
        email: str = "",
        phone: str = "",
        company_name: str = "",
    ) -> dict:
        """Erfasst einen Vertriebs-Lead aus einem laufenden Livechat-Gespraech

        Rufe dieses Tool erst auf, wenn der Besucher echtes Interesse gezeigt hat
        (konkreter Bedarf, Budget oder Zeitrahmen) UND mindestens ein Kontaktweg
        (E-Mail oder Telefon) bekannt ist. Nicht aufrufen, nur weil jemand "Hallo"
        gesagt hat - nur bei einer echten Verkaufschance fuer das Vertriebsteam.
        Frage den Besucher aktiv nach Kontaktdaten, bevor du dieses Tool nutzt,
        falls sie noch fehlen.

        Args:
            description: Zusammenfassung des Bedarfs/Interesses, moeglichst in den
                eigenen Worten des Besuchers
            contact_name: Name des Besuchers, falls bekannt
            email: E-Mail-Adresse, falls bekannt
            phone: Telefonnummer, falls bekannt
            company_name: Firma des Besuchers, falls bekannt/relevant (B2B)

        Returns:
            Dictionary mit Bestaetigung des angelegten Leads
        """
        source = self.env.ref(LIVECHAT_SOURCE_XMLID, raise_if_not_found=False)

        lead_vals = {
            'name': description[:100] or _("Livechat-Anfrage"),
            'description': description,
            'type': 'lead',
            'source_id': source.id if source else False,
        }
        if contact_name:
            lead_vals['contact_name'] = contact_name
        if email:
            lead_vals['email_from'] = email
        if phone:
            lead_vals['phone'] = phone
        if company_name:
            lead_vals['partner_name'] = company_name

        lead = self.sudo().create(lead_vals)
        _logger.info("Livechat-KI-Bot: Lead #%s aus Chat erstellt", lead.id)

        return {
            'lead_id': lead.id,
            'lead_name': lead.name,
            'stage': lead.stage_id.name if lead.stage_id else '',
        }
