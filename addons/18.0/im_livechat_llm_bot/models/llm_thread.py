import logging

import emoji
import markdown2
from markupsafe import Markup

from odoo import models

_logger = logging.getLogger(__name__)


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
