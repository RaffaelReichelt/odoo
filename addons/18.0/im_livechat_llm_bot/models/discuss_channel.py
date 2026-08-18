import logging
import re

from markupsafe import Markup

from odoo import fields, models, tools

_logger = logging.getLogger(__name__)

BOT_PARTNER_XMLID = 'im_livechat_llm_bot.partner_llm_bot'

# Live beobachtet (15.08., Benchmark mistral-small3.2:24b): trotz expliziter
# Prompt-Regel ("nenne niemals Kontaktdaten/URLs aus eigenem Wissen") erfindet
# das Modell weiterhin plausibel klingende, aber falsche eigene Domains
# ("privatemind.de/kontakt" statt der echten "privatemind.eu") - anders als
# bei Telefonnummern/E-Mails, wo dieselbe Regel zuverlaessig gegriffen hat.
# Eine URL wirkt fuer das Modell offenbar weniger wie eine "erfundene
# Tatsache" als eine banale Pfad-Vervollstaendigung. Da Prompt-Text hier
# nachweislich nicht ausreicht (gleiches Muster wie zuvor bei Preisen/Namen),
# wird jede selbstreferenzielle URL, die nicht zur echten Domain passt, vor
# dem Versand entfernt statt "korrigiert" - der genaue Pfad (z.B. /kontakt
# vs. /contact) ist server-seitig nicht zuverlaessig verifizierbar, ein
# entfernter Link ist ungefaehrlicher als ein falscher.
REAL_DOMAIN = 'privatemind.eu'
# Faengt sowohl URLs (mit/ohne Protokoll/www) als auch E-Mail-Adressen auf
# der Domain ab - der optionale "lokalerteil@"-Teil davor sorgt dafuer, dass
# bei einer erfundenen Adresse wie "info@privatemind.de" die GANZE Adresse
# entfernt wird, nicht nur die Domain (sonst bliebe ein kaputtes "info@" im
# Text stehen - schlimmer als der Link ganz zu entfernen).
_SELF_URL_RE = re.compile(
    r'(?:[\w.+-]+@)?(?:https?://)?(?:www\.)?privatemind\.[a-z]{2,}(?:/[^\s<>"\')]*)?',
    re.IGNORECASE,
)


def _strip_fabricated_self_urls(text):
    def replace(match):
        url = match.group(0)
        return url if REAL_DOMAIN.lower() in url.lower() else ''
    return _SELF_URL_RE.sub(replace, text)


# Live beobachtet (17.08.): mistral-small3.2:24b verstuemmelt zuverlaessig
# JEDE Zahl ab ca. 1000 beim Wiedergeben in Fliesstext, unabhaengig von
# Formatierung ("62.500" vs. "62500"), Eindeutigkeit der Frage oder ob es
# die einzige genannte Zahl ist - z.B. wird aus "62500 EUR" die Antwort
# "62,50 EUR", aus "1999 EUR" wird "1,9 EUR", aus "999 EUR" wird "9 EUR".
# Drei verschiedene Prompt-Ansaetze (deutsches Zahlenformat, Zahlen ohne
# Trennzeichen, Mehrdeutigkeit vorher aufloesen) haben das NICHT behoben.
# Ein erster Versuch, die Verstuemmelung rechnerisch rueckgaengig zu machen
# (Annahme: Division durch ~1000), ist gescheitert - die drei beobachteten
# Faelle folgen KEINEM einzigen konsistenten mathematischen Muster (62500->
# 62,50 passt zu /1000, 999->9 nicht). Ein Korrekturversuch waere also
# selbst nur eine weitere Vermutung. Stattdessen: jede Preisangabe, die zu
# keinem echten Katalogpreis passt, laesst die KOMPLETTE Antwort durch eine
# sichere Ausweichantwort ersetzen - lieber eine ehrliche Nicht-Antwort als
# eine geratene, moeglicherweise falsche Zahl an einen Kunden.
_PRICE_MENTION_RE = re.compile(r'(\d[\d.,]*)\s?EUR', re.IGNORECASE)
_SAFE_PRICE_FALLBACK_TEXT = (
    "Ich möchte Ihnen hier keine möglicherweise ungenaue Zahl nennen. "
    "Für den exakten Preis wenden Sie sich bitte über unser Kontaktformular "
    "an unser Vertriebsteam."
)


# Live beobachtet (18.08., deepseek-r1:32b): eine ansonsten inhaltlich
# korrekte deutsche Antwort wechselte mitten im Satz unangekuendigt ins
# Chinesische ("...der Appliances远程完成，通常需要2-4小时。") - DeepSeek ist
# ein chinesisches Modell, ein gelegentlicher Sprachwechsel liegt in der
# Natur des Trainingskorpus, ist aber fuer deutschsprachige Kunden
# unbrauchbar. Anders als bei Preisen/URLs ist der INHALT hier meist
# korrekt, nur die Sprache falsch - eine komplette Ersatzantwort waere hier
# unnoetig hart. Stattdessen: nicht-lateinische Schriftzeichen zuverlaessig
# per Unicode-Bereich erkennen (keine Sprach-Erkennungs-Bibliothek noetig)
# und die Antwort GEZIELT nachuebersetzen lassen - mit demselben
# Modell/Provider, aber einem eng gefassten reinen Uebersetzungs-Prompt
# (Temperatur 0, kein Tool-Zugriff, kein Spielraum fuer neue Fakten).
# Schlaegt auch die Uebersetzung fehl oder enthaelt selbst noch fremde
# Zeichen, gilt dieselbe Grundregel wie beim Preis-Guard: lieber eine
# ehrliche Ausweichantwort als etwas Unlesbares/Ungeprueftes an den Kunden.
_NON_GERMAN_SCRIPT_RE = re.compile(
    '['
    '一-鿿'   # CJK Unified Ideographs (Chinesisch)
    '぀-ヿ'   # Hiragana/Katakana (Japanisch)
    '가-힯'   # Hangul (Koreanisch)
    'Ѐ-ӿ'   # Kyrillisch
    '؀-ۿ'   # Arabisch
    '฀-๿'   # Thai
    ']',
)
_SAFE_LANGUAGE_FALLBACK_TEXT = (
    "Entschuldigung, bei der Erstellung der Antwort ist ein technisches "
    "Problem aufgetreten. Bitte wenden Sie sich über unser Kontaktformular "
    "an unser Team."
)


def _contains_non_german_script(text):
    return bool(_NON_GERMAN_SCRIPT_RE.search(text or ''))


def _translate_to_german(provider, model, text):
    """Uebersetzt text mit einem knappen, faktenneutralen Prompt ins
    Deutsche. Nutzt bewusst denselben Provider/Modell wie die eigentliche
    Antwort (kein zusaetzlicher Anbieter/API-Key noetig) - eine reine
    Uebersetzungs-Aufgabe ist ein deutlich engerer, weniger
    halluzinationsanfaelliger Auftrag als freie Beantwortung, daher hier
    vertretbar. Gibt None zurueck, wenn der Aufruf fehlschlaegt oder das
    Ergebnis selbst noch fremde Schriftzeichen enthaelt - der Aufrufer
    faellt dann auf die sichere Ausweichantwort zurueck.
    """
    if provider.service != 'ollama':
        # Uebersetzung ist aktuell nur fuer den Ollama-Pfad implementiert
        # (client.chat() Signatur/Antwortformat unterscheidet sich je
        # Anbieter) - bei Cloud-Providern lieber sauber auf die
        # Ausweichantwort fallen statt ein ungetestetes API-Format zu riskieren.
        return None
    try:
        client = provider.client
        response = client.chat(
            model=model.name,
            messages=[{
                'role': 'user',
                'content': (
                    'Uebersetze den folgenden Text VOLLSTAENDIG und '
                    'AUSSCHLIESSLICH ins Deutsche. Gib NUR die Uebersetzung '
                    'zurueck, ohne Anmerkungen, ohne Anfuehrungszeichen, '
                    'ohne den Inhalt zu veraendern oder zu ergaenzen:\n\n' + text
                ),
            }],
            options={'temperature': 0},
            stream=False,
        )
        translated = (response.get('message') or {}).get('content', '').strip()
        if not translated or _contains_non_german_script(translated):
            return None
        return translated
    except Exception:
        _logger.exception(
            "Livechat-KI-Bot: Uebersetzung ins Deutsche fehlgeschlagen, "
            "verwende sichere Ausweichantwort.",
        )
        return None


def _ensure_german(text, provider, model):
    if not _contains_non_german_script(text):
        return text
    _logger.warning(
        "Livechat-KI-Bot: Bot-Antwort enthaelt nicht-deutsche Schriftzeichen "
        "- versuche Nachuebersetzung.",
    )
    translated = _translate_to_german(provider, model, text)
    if translated:
        return translated
    return _SAFE_LANGUAGE_FALLBACK_TEXT


def _get_known_prices(env):
    """Sammelt alle echten Preise direkt aus dem Produktkatalog - eine
    Quelle der Wahrheit, die bei Preisaenderungen automatisch aktuell
    bleibt (keine hartkodierte Liste, die aus dem Ruder laufen kann)."""
    products = env['product.template'].sudo().search(
        [('sale_ok', '=', True), ('active', '=', True)])
    known = set()
    for p in products:
        if not p.list_price:
            continue
        price = round(p.list_price)
        known.add(price)
        # All-In Bundles laufen ueber 36 Monate - der oft genannte
        # Gesamtbetrag (monatlich * 36) ist im Text ebenfalls ein legitimer
        # "bekannter" Wert, keine Erfindung.
        if 'all-in' in (p.name or '').lower():
            known.add(price * 36)
    return known


def _validate_prices(text, known_prices):
    """Prueft jede "<Zahl> EUR"-Erwaehnung gegen die echten Katalogpreise
    (Trennzeichen egal - "62.500", "62500" werden gleich behandelt). Findet
    sich eine Zahl, die zu keinem bekannten Preis passt, wird die komplette
    Antwort durch eine sichere Ausweichantwort ersetzt (siehe Kommentar
    oben, warum keine Korrektur versucht wird)."""
    if not known_prices:
        return text

    for match in _PRICE_MENTION_RE.finditer(text):
        raw = match.group(1)
        digits_only = re.sub(r'[.,]', '', raw)
        as_int = int(digits_only) if digits_only.isdigit() else None
        if as_int not in known_prices:
            _logger.warning(
                "Livechat-KI-Bot: Preisangabe in Bot-Antwort passt zu "
                "keinem bekannten Katalogpreis (%s EUR) - ersetze komplette "
                "Antwort durch sichere Ausweichantwort statt eine "
                "moeglicherweise falsche Zahl zu zeigen.", raw,
            )
            return _SAFE_PRICE_FALLBACK_TEXT

    return text


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    llm_thread_id = fields.Many2one(
        'llm.thread',
        string='KI-Thread',
        copy=False,
        help="Der llm.thread, der den Gespraechsverlauf dieses Livechat-Kanals "
             "gegenueber der KI abbildet.",
    )

    def _message_post_after_hook(self, message, msg_vals):
        result = super()._message_post_after_hook(message, msg_vals)
        try:
            self._llm_bot_try_reply(message, msg_vals)
        except Exception:
            _logger.exception(
                "Livechat-KI-Bot: Fehler beim Verarbeiten der Nachricht in Kanal %s",
                self.id,
            )
        return result

    def _llm_bot_try_reply(self, message, msg_vals):
        self.ensure_one()

        if self.channel_type != 'livechat' or self.chatbot_current_step_id:
            return

        assistant = self.livechat_channel_id.sudo().llm_assistant_id
        if not assistant:
            return

        if msg_vals.get('message_type') != 'comment':
            return

        bot_partner = self.env.ref(BOT_PARTNER_XMLID, raise_if_not_found=False)
        if not bot_partner:
            return

        author_id = msg_vals.get('author_id')
        author_guest_id = msg_vals.get('author_guest_id')

        if author_id == bot_partner.id:
            return  # eigene Antwort des Bots: Endlosschleife vermeiden

        if not author_id and not author_guest_id:
            # Weder Partner noch Gast als Autor: keine echte Besucher-/Operator-Nachricht.
            return

        if author_id:
            # Anonyme Besucher posten ueber mail.guest (author_guest_id) und haben
            # kein author_id. Ist author_id gesetzt, kann es ein eingeloggter
            # interner Operator ODER ein angemeldeter Portal-/Kunden-Besucher sein -
            # nur beim internen Operator soll der Bot nicht antworten. partner_share
            # ist Odoos eigenes Feld dafuer ("hat einen internen, nicht-Portal-User").
            author_partner = self.env['res.partner'].sudo().browse(author_id)
            if not author_partner.partner_share:
                _logger.info(
                    "Livechat-KI-Bot: keine Antwort in Kanal %s - Nachricht kam von "
                    "internem User %s (partner_share=False), nicht von einem Gast. "
                    "Zum Testen als Besucher im Inkognito-Fenster/ausgeloggt schreiben.",
                    self.id, author_partner.display_name,
                )
                return

        body_text = tools.html2plaintext(msg_vals.get('body') or '').strip()
        if not body_text:
            return

        thread = self.sudo()._llm_bot_get_or_create_thread(assistant)

        # Nativer Discuss-Typing-Indikator (_notify_typing) - wird von der internen
        # Backend-Ansicht gerendert, aber NICHT vom eingebetteten Besucher-Widget
        # (das zeigt "tippt..." nur fuer den regelbasierten Chatbot-Script-Player,
        # siehe im_livechat/static/src/embed/common/thread_patch.xml). Fuer den
        # Besucher zaehlt daher die echte Platzhalter-Nachricht unten.
        bot_member = self.sudo().channel_member_ids.filtered(
            lambda m: m.partner_id == bot_partner,
        )
        if bot_member:
            bot_member._notify_typing(True)

        # Echte Platzhalter-Nachricht mit demselben pulsierenden GIF, das auch der
        # eingebaute Regel-Chatbot fuer "tippt..." nutzt - die Generierung kann bei
        # der GX10-Modellgroesse leicht 30-90s dauern (mehrere Tool-Call-Runden),
        # ohne sichtbares Feedback wirkt der Chat in der Zeit wie haengen geblieben.
        typing_message = self.sudo().message_post(
            body=Markup('<img src="/im_livechat/static/src/img/chatbot_is_typing.gif" height="30"/>'),
            author_id=bot_partner.id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )
        # Bus-/Websocket-Benachrichtigungen haengen in Odoo an env.cr.postcommit
        # und werden erst beim COMMIT der Transaktion verschickt (bus/models/bus.py).
        # Ohne diesen expliziten Zwischen-Commit bleibt der Platzhalter oben nur
        # eine Zeile Code, aber unsichtbar fuer den Besucher: der komplette Hook
        # (inkl. der ggf. 30-90s dauernden Generierung) laeuft sonst in derselben,
        # noch offenen Transaktion wie der urspruengliche HTTP-Request des
        # Besuchers - live beobachtet, dass "tippt..." nie ankam, sondern erst die
        # fertige Antwort, alles auf einen Schlag beim finalen Request-Commit.
        self.env.cr.commit()
        try:
            for _event in thread.generate(user_message_body=body_text):
                pass
        finally:
            if bot_member:
                bot_member._notify_typing(False)

        reply = thread.message_ids.filtered(
            lambda m: m.llm_role == 'assistant' and not m.is_error,
        ).sorted('id')[-1:]
        if not reply:
            typing_message.unlink()
            return

        # Platzhalter live durch die echte Antwort ersetzen (statt einer zweiten
        # Nachricht), damit sie im Besucher-Widget an derselben Stelle erscheint.
        clean_body = _strip_fabricated_self_urls(reply.body or '')
        clean_body = _ensure_german(clean_body, thread.provider_id, thread.model_id)
        clean_body = _validate_prices(clean_body, _get_known_prices(self.env))
        typing_message.write({'body': clean_body})
        typing_message._bus_send_store(typing_message, {
            'body': typing_message.body,
            'write_date': typing_message.write_date,
        })

    def _llm_bot_get_or_create_thread(self, assistant):
        self.ensure_one()

        if self.llm_thread_id:
            return self.llm_thread_id

        thread = self.env['llm.thread'].create({
            'name': f"Livechat #{self.id}",
            'user_id': self.env.user.id,
            'provider_id': assistant.provider_id.id,
            'model_id': assistant.model_id.id,
            'model': self._name,
            'res_id': self.id,
        })
        thread.set_assistant(assistant.id)
        self.llm_thread_id = thread
        return thread
