import logging

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from odoo import fields, models

_logger = logging.getLogger(__name__)

# Odoo-Kernkonvention (community/addons/website/views/website_templates.xml):
# der eigentliche Seiteninhalt steckt in einem Element mit dieser ID - der
# "Skip to Content"-Link jeder Seite zeigt selbst darauf (#wrap). Header/Nav
# (#o_main_nav) und Footer (#footer) liegen ausserhalb davon und sind auf
# jeder Seite identisch - ohne Filter landen sie als Rauschen in jedem Chunk
# und verduennen die eigentliche Seiteninfo (live beobachtet: von ~2100
# Zeichen Volltext einer Seite waren nur ~1300 tatsaechlicher Inhalt).
MAIN_CONTENT_ID = 'wrap'

# Kurswechsel (15.08.): Preise und der Gruendername wurden hier frueher per
# Regex aus dem Seiteninhalt entfernt (_strip_prices/_strip_person_names),
# weil kein getestetes Modell sie zuverlaessig korrekt wiedergegeben hat -
# der Bot sollte lieber ehrlich eskalieren als raten/erfinden. Auf
# ausdruecklichen Wunsch ist das jetzt nicht mehr die Linie: Preise und der
# Gruender sollen aktiv genannt werden (siehe model_benchmark.py PREIS-1/
# PREIS-2/ANGEBOT-2), Eskalation ist nur noch der Ausnahmefall fuer echte
# Datenluecken. Die strukturelle Redaktion ist daher entfernt - das
# urspruengliche Halluzinationsrisiko (Modell nennt trotz korrektem
# Suchtreffer eine falsche Zahl/einen falschen Namen) bleibt ein bekanntes,
# bewusst in Kauf genommenes Restrisiko, das ueber Sampling-Temperatur
# (siehe llm_provider.py) und die Testkatalog-Checks beobachtet wird statt
# ueber Content-Entfernung ausgeschlossen zu werden.


class WebsitePage(models.Model):
    _inherit = 'website.page'

    llm_source_url = fields.Char(
        string='KI-Wissensbasis-URL',
        compute='_compute_llm_source_url',
        help="Absolute URL, unter der diese Seite fuer die KI-Wissensbasis "
             "(llm_knowledge) abgerufen wird.",
    )

    def _compute_llm_source_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for page in self:
            page.llm_source_url = f"{base_url}{page.url}" if page.url else False

    def llm_get_fields(self, record):
        """Hook von llm_knowledge (siehe llm_resource_parser.py#parse): liefert
        den Content fuer die RAG-Wissensbasis direkt, ohne den generischen
        "http"-Retriever zu nutzen. Der wuerde versuchen, den Inhalt in ein
        Feld namens 'content' AUF DIESEM DATENSATZ zurueckzuschreiben (Muster
        fuer document.page, das so ein Feld hat) - website.page hat keins,
        das fuehrt zu einem KeyError. Deshalb rendern wir die Seite hier per
        internem HTTP-Request selbst (identisch zu dem, was ein Besucher
        sieht), schneiden Header/Nav/Footer heraus und geben nur den
        eigentlichen Seiteninhalt als Markdown zurueck.
        """
        self.ensure_one()
        if not self.is_published or not self.llm_source_url:
            return []
        try:
            response = requests.get(self.llm_source_url, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            _logger.warning(
                "KI-Wissensbasis: Seite %s konnte nicht geladen werden: %s",
                self.llm_source_url, e,
            )
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        main_content = soup.find(id=MAIN_CONTENT_ID)
        if main_content is None:
            _logger.info(
                "KI-Wissensbasis: kein #%s auf %s gefunden, nutze komplette "
                "Seite (inkl. Navigation/Footer) als Fallback.",
                MAIN_CONTENT_ID, self.llm_source_url,
            )
            markdown = md(response.text, heading_style="ATX").strip()
        else:
            markdown = md(str(main_content), heading_style="ATX").strip()

        if not markdown:
            return []

        return [{
            "field_name": "content",
            "mimetype": "text/markdown",
            "rawcontent": markdown,
        }]
