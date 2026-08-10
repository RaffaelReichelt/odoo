import logging

from odoo import models

from odoo.addons.llm_tool.decorators import llm_tool

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def search_sellable_products(self, query: str, limit: int = 5) -> dict:
        """Durchsucht den verkaeuflichen Produktkatalog nach passenden Produkten

        Nutze dieses Tool, um Preise und Beschreibungen nachzuschlagen, bevor du
        einem Website-Besucher ein Produkt empfiehlst oder eine Preisfrage
        beantwortest (z.B. "Was bietet ihr an?" oder "Was kostet X?"). Liefert nur
        Produkte, die tatsaechlich verkaufbar und aktiv sind - erfinde niemals
        Preise oder Produkte, die hier nicht auftauchen.

        Args:
            query: Freitext-Suchbegriff (durchsucht Produktname und Verkaufsbeschreibung)
            limit: Maximale Anzahl zurueckgegebener Produkte (Standard: 5)

        Returns:
            Dictionary mit einer Liste passender Produkte (Name, Preis, Waehrung, Beschreibung)
        """
        limit = max(1, min(limit, 20))
        domain = [('sale_ok', '=', True), ('active', '=', True)]
        if query:
            domain += ['|', ('name', 'ilike', query), ('description_sale', 'ilike', query)]

        products = self.sudo().search(domain, limit=limit)
        currency = self.env.company.currency_id

        return {
            'query': query,
            'count': len(products),
            'currency': currency.name,
            'products': [
                {
                    'name': product.name,
                    'price': product.list_price,
                    'description': product.description_sale or '',
                }
                for product in products
            ],
        }
