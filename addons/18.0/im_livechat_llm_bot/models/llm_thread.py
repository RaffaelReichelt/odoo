import logging

import emoji
import markdown2
from markupsafe import Markup

from odoo import models

from .discuss_channel import visitor_language_name
from .llm_provider import PER_CALL_NUM_PREDICT

_logger = logging.getLogger(__name__)

# Live beobachtet (23.08.): ohne Tools UND ohne Laengenlimit generiert das
# Modell in der erzwungenen Text-Runde (siehe unten) manchmal einfach weiter,
# statt zu stoppen - ein einzelner Fall lief >1200 Tokens weit ueber jede
# sinnvolle Chat-Antwort hinaus, nur ein zufaelliger Server-Neustart beendete
# ihn. 500 Tokens sind grosszuegig fuer eine normale Chat-Antwort in
# natuerlicher Sprache (kein Tool-Call-JSON mehr moeglich, da Tools entzogen
# sind), begrenzen aber den Schaden falls das Modell trotzdem nicht stoppt.
#
# Live beobachtet (26.08.): dasselbe Muster trat auch in einer GANZ NORMALEN
# Tool-Runde auf (Tools noch angeboten) - 48.700+ Tokens am Stueck, siehe
# PER_CALL_NUM_PREDICT in llm_provider.py fuer Details. Derselbe Wert gilt
# jetzt daher fuer JEDE Runde, nicht mehr nur die erzwungene letzte: ein
# Tool-Aufruf braucht kaum mehr Tokens als sein JSON (Funktionsname +
# Argumente), eine normale Chat-Antwort laut Prompt-Vorgabe ohnehin nur
# 3-4 Saetze - 500 Tokens sind fuer beide Faelle grosszuegig genug, um keine
# legitime Antwort abzuschneiden.
_MAX_TOKENS_PER_CALL = 500


class LLMThread(models.Model):
    _inherit = 'llm.thread'

    def _process_llm_body(self, body):
        # Kompatibilitaets-Fix: llm_thread._process_llm_body() (odoo-llm 18.0)
        # ruft emoji.demojize(body) auf - das wandelt aber ECHTE Unicode-Emoji
        # in :shortcode:-Text um (z.B. "😊" -> ":smiling_face_with_smiling_eyes:"),
        # das Gegenteil von dem, was fuer die Anzeige noetig ist. Live
        # beobachtet: das Modell schreibt echte Emoji oder kurze Shortcodes
        # (":smile:") in seine Antwort, und der Besucher sieht danach nur noch
        # den rohen Shortcode-Text statt eines Emoji. emojize() macht beides
        # richtig: echte Emoji bleiben unveraendert, Shortcodes werden zu
        # echten Emoji gewandelt.
        if not body or isinstance(body, Markup):
            return body
        # Zwei Durchgaenge, da 'alias' (kurze Slack/GitHub-Codes wie ":smile:")
        # und 'en' (volle Namen wie ":smiling_face_with_smiling_eyes:", exakt
        # das Format, das demojize() erzeugt) unterschiedliche Shortcode-Sets
        # abdecken. Echte Unicode-Emoji bleiben in beiden Durchgaengen unveraendert.
        text = emoji.emojize(body, language='alias')
        text = emoji.emojize(text, language='en')
        return markdown2.markdown(text)

    def get_context(self, base_context=None):
        # Speist die Besuchersprache als 'customer_language'-Argument in das
        # Rendering des Kundenservice-Prompt ein (siehe llm.prompt-Datensatz,
        # Platzhalter "{{customer_language}}"). get_context() wird JEDE Runde
        # neu aufgerufen (siehe get_prepend_messages() in odoo-llm,
        # llm_assistant/models/llm_thread.py), nicht nur beim Thread-Erstellen -
        # daher reicht das simple Auslesen hier, ohne Zwischenspeicherung.
        # Kein customer_language-Eintrag (Sprache unbekannt/nicht gelistet in
        # visitor_language_name, oder Thread gehoert gar keinem Livechat-Kanal):
        # der Default "Deutsch" aus arguments_json des Prompts greift dann
        # unveraendert wie bisher.
        context = super().get_context(base_context)
        if self.model == 'discuss.channel' and self.res_id:
            channel = self.env['discuss.channel'].browse(self.res_id)
            language_name = visitor_language_name(channel._llm_bot_visitor_lang_code())
            if language_name:
                context = {**context, 'customer_language': language_name}
        return context

    def _prepare_chat_kwargs(self, message_history, use_streaming):
        # Kompatibilitaets-Fix: llm.assistant.tool_calls_max verspricht laut eigenem
        # Hilfetext, konsekutive Tool-Aufrufe zu begrenzen ("... before breaking the
        # loop to prevent infinite tool calling"), wird aber in odoo-llm (Stand
        # 18.0, Aug 2026) im generate()-Loop von llm_thread.py nirgends ausgewertet.
        # Ohne diese Bremse ruft ein Modell, das nicht erkennt, dass es bereits genug
        # Informationen hat, ein Tool beliebig oft erneut auf - live beobachtet: 22
        # Aufrufe von search_sellable_products in Folge, obwohl der erste Aufruf
        # schon ein vollstaendiges Ergebnis lieferte, ohne dass der Besucher je eine
        # Antwort bekam. Sobald das Limit erreicht ist, bieten wir dem Modell fuer
        # die naechste Anfrage keine Tools mehr an - dadurch KANN es keinen weiteren
        # Tool-Call mehr erzeugen und muss stattdessen in Text antworten.
        kwargs = super()._prepare_chat_kwargs(message_history, use_streaming)
        # Siehe Kommentar bei _MAX_TOKENS_PER_CALL oben (26.08.-Vorfall): gilt
        # jetzt fuer JEDE Runde, nicht mehr nur die weiter unten erzwungene
        # letzte - sonst kann dieselbe Endlos-Generierung in jeder Runde
        # auftreten, in der das Modell noch Tools angeboten bekommt.
        PER_CALL_NUM_PREDICT.set(_MAX_TOKENS_PER_CALL)
        max_calls = self.assistant_id.tool_calls_max or 0
        if max_calls and self._llm_bot_consecutive_tool_rounds() >= max_calls:
            _logger.info(
                "Thread %s: tool_calls_max (%s) erreicht, biete dem Modell keine "
                "Tools mehr an, um eine Text-Antwort zu erzwingen.",
                self.id, max_calls,
            )
            kwargs['tools'] = self.env['llm.tool']
            # Nur Tools wegzunehmen reicht nicht: live beobachtet, dass das Modell
            # dann einfach den Tool-Aufruf als Klartext ausschreibt (z.B.
            # "search_sellable_products(query="")") statt zu antworten, weil ihm nie
            # gesagt wird, WARUM keine Tools mehr da sind. Letzte Nachricht pruefen,
            # um den Hinweis nicht mehrfach zu posten, falls diese Methode fuer
            # dieselbe Runde erneut aufgerufen wird.
            last_message = message_history[-1:] if message_history else self.env['mail.message']
            if not last_message or last_message.llm_role != 'system':
                self.message_post(
                    body=(
                        "Du hast das Limit an aufeinanderfolgenden Tool-Aufrufen erreicht "
                        "und hast jetzt KEIN Tool mehr zur Verfuegung - ruf keines mehr auf "
                        "und schreibe auch keinen Tool-Aufruf als Text. Antworte dem Kunden "
                        "jetzt direkt in normalem Text, ausschliesslich basierend auf den "
                        "Ergebnissen, die in diesem Gespraech bereits per Tool gefunden "
                        "wurden. Wurde nichts Passendes gefunden, sag das ehrlich und biete "
                        "einen menschlichen Mitarbeiter an."
                    ),
                    llm_role='system',
                )
                kwargs['messages'] = self.get_llm_messages()
        return kwargs

    def _llm_bot_consecutive_tool_rounds(self):
        """Zaehlt aufeinanderfolgende Tool-Call-Runden seit der letzten User-Nachricht."""
        self.ensure_one()
        count = 0
        for message in reversed(self.get_llm_messages(limit=50)):
            if message.llm_role == 'user':
                break
            if message.llm_role == 'assistant' and message.has_tool_calls():
                count += 1
        return count

    def _llm_bot_faq_override_answer(self):
        """Liefert die Katalog-Antwort zurueck, wenn faq_price_lookup in
        dieser Gespraechsrunde (seit der letzten User-Nachricht) mit
        matched=True geantwortet hat - sonst None.

        Live beobachtet (26.08.): selbst mit einer expliziten "wortwoertlich
        wiedergeben"-Anweisung DIREKT im Tool-Ergebnis (siehe faq_price_lookup
        in llm_tool.py) hat gemma4:12b eine korrekte Zahl trotzdem beim
        Aufschreiben verstuemmelt ("3.800 EUR" -> "3.80 EUR"). Der bestehende
        Preis-Guard (discuss_channel.py: _validate_prices) hat das zuverlaessig
        abgefangen - aber nur durch Ersetzen der GESAMTEN Antwort durch eine
        Ausweichfloskel, obwohl die richtige Antwort im selben Zug bereits
        vorlag. Ergebnis in der Praxis: Preisfragen wirkten fuer echte
        Besucher durchgaengig ausweichend. Die Zahlen-Transkriptionsschwaeche
        ist laut der LoRA-Fine-Tuning-Untersuchung (siehe
        /home/raffael/Projekte/lora, Stand 19.08.) eine inhaerente
        Modell-Eigenschaft, keine per Prompt/Tool-Hinweis loesbare
        Angewohnheit - deshalb hier die Katalog-Antwort direkt uebernehmen,
        statt dem Modell die Zahlen-Wiedergabe noch einmal zu ueberlassen.
        """
        self.ensure_one()
        for message in reversed(self.get_llm_messages(limit=50)):
            if message.llm_role == 'user':
                break
            if message.llm_role != 'tool':
                continue
            tool_data = message.get_tool_data() or {}
            if tool_data.get('tool_name') != 'faq_price_lookup':
                continue
            if tool_data.get('status') != 'completed':
                continue
            result = tool_data.get('result') or {}
            if result.get('matched') and result.get('answer'):
                # Neuester Tool-Aufruf gewinnt: wir laufen rueckwaerts, der
                # erste Treffer hier ist also der zeitlich letzte.
                return result['answer']
        return None
