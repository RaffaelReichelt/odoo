import logging

from odoo import models
from odoo.osv import expression

from odoo.addons.llm_tool.decorators import llm_tool

_logger = logging.getLogger(__name__)

# Live beobachtet (15.08.): "Anwaltskanzlei mit insgesamt 4 Anwaelten" enthaelt
# das Wort "mit" (3 Zeichen, besteht den alten len(t) > 2 Filter) - ein extrem
# haeufiges Wort, das in etlichen Produktbeschreibungen vorkommt. Das laesst
# den OR-Filter unten praktisch die HAELFTE des Katalogs matchen, inklusive
# der teuren Enterprise-Produkte, die mit Anwaltskanzleien nichts zu tun
# haben. Ohne Relevanz-Ranking (siehe unten) wurden dadurch die generischen
# Treffer vor "PrivateMind Standard" einsortiert (das Wort "Anwaltskanzleien"
# steht explizit in dessen Beschreibung) und bei limit=5 abgeschnitten -
# das Modell bekam das eigentlich passende Produkt gar nicht erst zu sehen
# und hat ehrlich, aber unnoetig eskaliert.
_STOPWORDS = {
    'mit', 'und', 'oder', 'der', 'die', 'das', 'ist', 'für', 'fuer', 'eine',
    'ein', 'sie', 'wir', 'uns', 'ich', 'auf', 'von', 'sich', 'wie', 'was',
    'hat', 'haben', 'sind', 'kann', 'können', 'koennen', 'bitte', 'gerne',
    'ihr', 'ihre', 'ihren', 'ihrem', 'ihrer', 'mich', 'dir', 'dich', 'zu',
    'im', 'in', 'den', 'des', 'dem', 'insgesamt', 'total', 'alle', 'auch',
    'nur', 'noch', 'schon', 'sehr', 'aber', 'wenn', 'dann', 'diese', 'dieser',
    'dieses', 'welche', 'welcher', 'welches', 'da', 'dass', 'daher',
}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def search_sellable_products(self, query: str, limit: int = 5) -> dict:
        """Durchsucht den verkaeuflichen Produktkatalog nach Name, Kurzbeschreibung und Preis

        Nutze dieses Tool, um herauszufinden, ob es zu einer Kundenanfrage ein
        konkretes verkaeufliches Produkt/Angebot gibt (z.B. um einen Lead korrekt
        zuzuordnen oder eine Preisfrage konkret zu beantworten) - NICHT fuer
        allgemeine Fragen zu Website-Inhalten oder zur Firma (dafuer:
        knowledge_retriever). Nenne bei Preisfragen den Preis direkt aus der
        Beschreibung, wenn er dort steht - erfinde niemals einen Preis, der
        nicht in der Beschreibung genannt wird, und verweise nur an einen
        menschlichen Mitarbeiter, wenn die Beschreibung wirklich keinen Preis
        enthaelt.

        Args:
            query: Freitext-Suchbegriff (durchsucht Produktname und Verkaufsbeschreibung)
            limit: Maximale Anzahl zurueckgegebener Produkte (Standard: 5)

        Returns:
            Dictionary mit einer Liste passender Produkte (Name und vollstaendige
            Verkaufsbeschreibung inkl. Preis, sofern hinterlegt)
        """
        limit = max(1, min(limit, 20))
        base_domain = [('sale_ok', '=', True), ('active', '=', True)]

        # Live beobachtet: eine mehrwortige Anfrage wie "Kanzlei 8 Anwaelte"
        # wurde bisher als EIN Substring gegen name/description_sale geprueft
        # (ilike) - das matcht praktisch nie, selbst wenn ein einzelnes Wort
        # (hier "Kanzlei" in "Anwaltskanzleien") eindeutig gepasst haette.
        # Ergebnis: 0 Treffer, das Modell hat danach ein Fantasieprodukt
        # erfunden. Wortweise mit OR verknuepfen behebt das strukturell.
        # Stoppwoerter werden vorher entfernt (siehe _STOPWORDS-Kommentar).
        terms = [
            t for t in (query or '').split()
            if len(t) > 2 and t.lower() not in _STOPWORDS
        ]
        if terms:
            term_domain = expression.OR([
                ['|', ('name', 'ilike', term), ('description_sale', 'ilike', term)]
                for term in terms
            ])
            domain = expression.AND([base_domain, term_domain])
        else:
            domain = base_domain

        # Ohne Limit suchen und ERST DANACH nach Relevanz (Anzahl treffender
        # Begriffe) sortieren, statt dem DB-Standard-Ordering zu vertrauen -
        # sonst kann ein Produkt, das nur einen generischen Nebenbegriff
        # trifft, ein hochrelevantes Produkt aus den Top-N verdraengen, bevor
        # ueberhaupt sortiert wird (siehe Kommentar zu _STOPWORDS oben).
        candidates = self.sudo().search(domain)
        if terms and len(candidates) > limit:
            def _relevance(product):
                haystack = f'{product.name} {product.description_sale or ""}'.lower()
                return sum(1 for t in terms if t.lower() in haystack)
            candidates = candidates.sorted(key=_relevance, reverse=True)
        products = candidates[:limit]

        # Der Katalog ist klein (ca. 10 Produkte) - statt bei 0 Treffern ein
        # leeres Ergebnis zu liefern (das das Modell zum Erfinden verleitet),
        # lieber den GESAMTEN echten Katalog zurueckgeben. Das Modell soll
        # dann selbst das passendste ECHTE Produkt auswaehlen, statt eines
        # zu erfinden - ein falsches Produkt ist schlimmer als ein zu
        # breites Ergebnis.
        fallback_used = False
        if not products:
            # Live beobachtet (15.08., "Benoetige ich einen klimatisierten
            # Serverraum?"): der Kommentar oben verspricht den GESAMTEN
            # Katalog als Fallback, aber der Code hat trotzdem "limit"
            # (typischerweise 5) angewendet - bei ca. 10 echten Produkten
            # fielen dadurch zufaellig genau die Kernprodukte (PrivateMind
            # Standard/Professional) raus, das Modell bekam sie nie zu sehen
            # und musste ehrlich eskalieren, obwohl die Antwort im Katalog
            # stand. Der Fallback ignoriert das Limit jetzt bewusst - der
            # Katalog ist klein genug, dass "alles zeigen" kein Problem ist.
            fallback_used = True
            products = self.sudo().search(base_domain)

        return {
            'query': query,
            'count': len(products),
            'fallback_full_catalog_used': fallback_used,
            'products': [
                {
                    'name': product.name,
                    'description': product.description_sale or '',
                    # Strukturiertes Preisfeld zusaetzlich zum Preis-Erwaehnung
                    # in der Freitext-Beschreibung: das Modell muss die Zahl so
                    # nicht mehr selbst aus Fliesstext herausparsen (Fehlerquelle
                    # fuer Zahlendreher/falsche Waehrung), sondern kann sie direkt
                    # uebernehmen.
                    'price': product.list_price,
                    'currency': product.currency_id.name,
                }
                for product in products
            ],
        }
