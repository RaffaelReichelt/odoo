import logging
import re

from markupsafe import Markup

from odoo import fields, models, tools

from .llm_tool import faq_catalog_lookup

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
_SAFE_PRICE_FALLBACK_TEXT_DE = (
    "Ich möchte Ihnen hier keine möglicherweise ungenaue Zahl nennen. "
    "Für den exakten Preis wenden Sie sich bitte über unser Kontaktformular "
    "an unser Vertriebsteam."
)
# Analog zu _SAFE_LANGUAGE_FALLBACK_TEXTS (siehe unten): Live beobachtet
# (25.08.) - dieser Fallback lief bisher NACH _ensure_customer_language,
# war aber selbst hart auf Deutsch verdrahtet. Ergebnis: eine bereits
# korrekt ins Englische uebersetzte Antwort wurde bei einer nicht
# katalogkonformen Preisnennung durch deutschen Text ersetzt - der
# Sprachwechsel wirkte dadurch fuer den Besucher, als haette der
# Sprach-Fix gar nicht gegriffen. Bewusst nur 'en' explizit gelistet,
# sonst greift wie bisher der deutsche Text als Default.
_SAFE_PRICE_FALLBACK_TEXTS = {
    'en': (
        "I don't want to give you a potentially inaccurate figure here. "
        "For the exact price, please reach out to our sales team via the "
        "contact form on our website."
    ),
}


# Live beobachtet (18.08., deepseek-r1:32b): eine ansonsten inhaltlich
# korrekte deutsche Antwort wechselte mitten im Satz unangekuendigt ins
# Chinesische ("...der Appliances远程完成，通常需要2-4小时。") - DeepSeek ist
# ein chinesisches Modell, ein gelegentlicher Sprachwechsel liegt in der
# Natur des Trainingskorpus, ist aber fuer den Kunden unbrauchbar. Anders
# als bei Preisen/URLs ist der INHALT hier meist korrekt, nur die Sprache
# falsch - eine komplette Ersatzantwort waere hier unnoetig hart.
# Stattdessen: nicht-lateinische Schriftzeichen zuverlaessig per
# Unicode-Bereich erkennen (keine Sprach-Erkennungs-Bibliothek noetig) und
# die Antwort GEZIELT nachuebersetzen lassen - mit demselben Modell/Provider,
# aber einem eng gefassten reinen Uebersetzungs-Prompt (Temperatur 0, kein
# Tool-Zugriff, kein Spielraum fuer neue Fakten). Schlaegt auch die
# Uebersetzung fehl oder enthaelt selbst noch fremde Zeichen, gilt dieselbe
# Grundregel wie beim Preis-Guard: lieber eine ehrliche Ausweichantwort als
# etwas Unlesbares/Ungeprueftes an den Kunden.
#
# Urspruenglich war diese Pruefung hart auf Deutsch verdrahtet (Zielsprache
# war immer Deutsch). Seit Einfuehrung der besuchersprachigen Antworten
# (24.08.) ist die Zielsprache dynamisch (siehe _llm_bot_visitor_lang_code/
# visitor_language_name) - die Pruefung selbst bleibt unveraendert sinnvoll,
# da auch fuer Englisch/Franzoesisch/etc. (alles lateinische Schrift) ein
# Abrutschen ins Chinesische/Kyrillische genauso fehl am Platz waere.
_UNEXPECTED_SCRIPT_RE = re.compile(
    '['
    '一-鿿'   # CJK Unified Ideographs (Chinesisch)
    '぀-ヿ'   # Hiragana/Katakana (Japanisch)
    '가-힯'   # Hangul (Koreanisch)
    'Ѐ-ӿ'   # Kyrillisch
    '؀-ۿ'   # Arabisch
    '฀-๿'   # Thai
    ']',
)
# Sprachcode (siehe visitor_language_name) -> Ausweichtext, falls selbst die
# Nachuebersetzung fehlschlaegt. Bewusst nur die beiden aktuell unterstuetzten
# Zielsprachen explizit - fuer jede andere/unbekannte Sprache ist der
# deutsche Text der sichere Default (entspricht dem bisherigen Verhalten).
_SAFE_LANGUAGE_FALLBACK_TEXTS = {
    'en': (
        "Sorry, a technical problem occurred while preparing this reply. "
        "Please reach out to our team via the contact form on our website."
    ),
}
_SAFE_LANGUAGE_FALLBACK_TEXT_DE = (
    "Entschuldigung, bei der Erstellung der Antwort ist ein technisches "
    "Problem aufgetreten. Bitte wenden Sie sich über unser Kontaktformular "
    "an unser Team."
)


def _contains_unexpected_script(text):
    return bool(_UNEXPECTED_SCRIPT_RE.search(text or ''))


def _translate_to_language(provider, model, text, language_name):
    """Uebersetzt text mit einem knappen, faktenneutralen Prompt in
    language_name (z.B. 'Deutsch', 'Englisch'). Nutzt bewusst denselben
    Provider/Modell wie die eigentliche Antwort (kein zusaetzlicher
    Anbieter/API-Key noetig) - eine reine Uebersetzungs-Aufgabe ist ein
    deutlich engerer, weniger halluzinationsanfaelliger Auftrag als freie
    Beantwortung, daher hier vertretbar. Gibt None zurueck, wenn der Aufruf
    fehlschlaegt oder das Ergebnis selbst noch fremde Schriftzeichen
    enthaelt - der Aufrufer faellt dann auf die sichere Ausweichantwort
    zurueck.
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
                    f'Uebersetze den folgenden Text VOLLSTAENDIG und '
                    f'AUSSCHLIESSLICH in diese Sprache: {language_name}. Gib '
                    'NUR die Uebersetzung zurueck, ohne Anmerkungen, ohne '
                    'Anfuehrungszeichen, ohne den Inhalt zu veraendern oder '
                    'zu ergaenzen:\n\n' + text
                ),
            }],
            options={'temperature': 0},
            stream=False,
        )
        translated = (response.get('message') or {}).get('content', '').strip()
        if not translated or _contains_unexpected_script(translated):
            return None
        return translated
    except Exception:
        _logger.exception(
            "Livechat-KI-Bot: Nachuebersetzung fehlgeschlagen, verwende "
            "sichere Ausweichantwort.",
        )
        return None


def _ensure_customer_language(text, provider, model, lang_code, language_name):
    if not _contains_unexpected_script(text):
        return text
    _logger.warning(
        "Livechat-KI-Bot: Bot-Antwort enthaelt unerwartete Schriftzeichen "
        "- versuche Nachuebersetzung nach %s.", language_name or 'Deutsch',
    )
    translated = _translate_to_language(
        provider, model, text, language_name or 'Deutsch',
    )
    if translated:
        return translated
    return _SAFE_LANGUAGE_FALLBACK_TEXTS.get(
        lang_code, _SAFE_LANGUAGE_FALLBACK_TEXT_DE,
    )


def _get_known_prices_by_product(env):
    """Sammelt alle echten Preise direkt aus dem Produktkatalog - eine
    Quelle der Wahrheit, die bei Preisaenderungen automatisch aktuell
    bleibt (keine hartkodierte Liste, die aus dem Ruder laufen kann).

    Anders als eine simple Preis-Menge wird hier PRO Produktname
    gespeichert, welche Preise fuer GENAU dieses Produkt gueltig sind.

    Live beobachtet (21.08., "Steuerbuero mit 4 Steuerberatern"): eine
    flache Menge aller Katalogpreise laesst eine falsche Zahl durchgehen,
    solange sie IRGENDWO im Katalog real vorkommt - hier wurde "29 EUR
    pro Monat" fuer das "All-In Bundle Standard" genannt (echter Preis:
    299 EUR/Monat), aber 29 EUR ist der reale Preis von "PrivateMind
    Backup Basic - Managed", einem voellig unverwandten Produkt. Die alte
    Pruefung haette das durchgelassen, weil 29 ein "bekannter Preis" ist -
    nur eben fuer das falsche Produkt. Grund fuer die Verwechslung: bei
    dieser Anfrage matchte kein Produkt die Suchbegriffe, wodurch
    search_sellable_products auf den Fallback "kompletten Katalog
    zurueckgeben" auswich (siehe product_template.py) - das Modell sah
    dadurch auch preislich unpassende Nebenprodukte wie die Backup-Plaene."""
    products = env['product.template'].sudo().search(
        [('sale_ok', '=', True), ('active', '=', True)])
    by_product = {}
    for p in products:
        if not p.list_price or not p.name:
            continue
        price = round(p.list_price)
        prices = {price}
        # All-In Bundles laufen ueber 36 Monate - der oft genannte
        # Gesamtbetrag (monatlich * 36) ist im Text ebenfalls ein legitimer
        # "bekannter" Wert, keine Erfindung.
        if 'all-in' in p.name.lower():
            prices.add(price * 36)
        by_product[p.name] = prices

    # Live beobachtet (26.08., "Und das All-In Bundle Standard?"): der echte
    # Produktname traegt einen technischen Klammerzusatz ("All-In Bundle
    # Standard (36 Monate)", "PrivateMind Enterprise (ASUS ESC4000A-E12)"),
    # den weder Modell noch Katalogantworten immer mitschreiben. Wird nur
    # der Kurzname genannt ("All-In Bundle Standard"), matcht oben KEIN
    # Produktname im Text - die Pruefung faellt dadurch auf die allgemeine
    # "irgendein bekannter Preis im GESAMTEN Katalog"-Regel zurueck (siehe
    # Kommentar oben, 21.08.-Vorfall) und liess "29 EUR" durch, weil das der
    # reale Preis von "PrivateMind Backup Basic - Managed" ist - einem
    # voellig unverwandten Produkt. Fix: fuer jeden Namen mit einem
    # abschliessenden Klammerzusatz zusaetzlich einen Alias OHNE diesen
    # Zusatz registrieren, damit der Kurzname genauso produktscharf
    # geprueft wird. Bewusst NUR Klammerzusaetze (nicht z.B. " - Managed"):
    # "PrivateMind Backup Basic" und "PrivateMind Backup Basic - Managed"
    # sind unterschiedliche Produkte mit unterschiedlichem Preis - ein
    # Alias wuerde dort faelschlich beide Preismengen vermischen.
    for name, prices in list(by_product.items()):
        alias = re.sub(r'\s*\([^)]*\)\s*$', '', name).strip()
        if alias and alias != name:
            by_product.setdefault(alias, set()).update(prices)
    return by_product


# Antworten kommen als markdown2-Output an, d.h. praktisch immer als eine
# Folge von <p>...</p>-Bloecken. Damit laesst sich eine falsche Preisangabe
# auf ihren eigenen Absatz eingrenzen (siehe Kommentar in _validate_prices,
# warum das notwendig wurde).
_PARAGRAPH_RE = re.compile(r'<p>.*?</p>', re.DOTALL)


def _validate_prices(text, known_prices_by_product, lang_code=None):
    """Prueft jede "<Zahl> EUR"-Erwaehnung gegen die echten Katalogpreise
    (Trennzeichen egal - "62.500", "62500" werden gleich behandelt).

    Wird VOR der Preisnennung im Text bereits ein konkreter Produktname
    genannt, muss die Zahl zum Preis GENAU DIESES Produkts passen - nicht
    nur zu irgendeinem Preis irgendwo im Katalog (siehe Kommentar bei
    _get_known_prices_by_product, warum die alte, produktlose Pruefung
    eine falsche Zahl durchgelassen hat). Ohne erkennbaren Produktnamen in
    der Naehe bleibt die alte, grosszuegigere Pruefung gegen den
    Gesamtkatalog als Sicherheitsnetz bestehen.

    Live beobachtet (25.08.): eine reine Produktempfehlungs-Frage (kein
    Preis gefragt) bekam eine inhaltlich korrekte Antwort, die das Modell
    unaufgefordert um eine Preisangabe ergaenzt hatte - und WEIL nur diese
    eine Zahl nicht passte (Zahlen-Mangling, siehe search_sellable_products),
    flog die KOMPLETTE, sonst korrekte Empfehlung raus und wurde durch eine
    thematisch voellig unpassende Ausweichantwort ersetzt ("kein Preis" auf
    eine Frage, die nie nach einem Preis gefragt hatte). Deshalb jetzt
    absatzweise statt global: nur der <p>-Block mit der fehlerhaften
    Preisangabe wird durch die sichere Ausweichantwort ersetzt, alle anderen
    Absaetze (z.B. die eigentliche Produktempfehlung) bleiben stehen. Nur
    wenn sich gar keine <p>-Absaetze erkennen lassen (untypisches Format)
    oder AUSNAHMSLOS jeder Absatz betroffen ist, wird wie bisher die
    komplette Antwort ersetzt."""
    if not known_prices_by_product:
        return text

    all_known_prices = set().union(*known_prices_by_product.values())
    fallback_note = _SAFE_PRICE_FALLBACK_TEXTS.get(
        lang_code, _SAFE_PRICE_FALLBACK_TEXT_DE,
    )

    # Position jeder Produktnennung im Text sammeln, um beim Pruefen einer
    # Preisangabe das zuletzt zuvor genannte Produkt zu ermitteln.
    name_positions = sorted(
        (m.start(), name)
        for name in known_prices_by_product
        for m in re.finditer(re.escape(name), text, re.IGNORECASE)
    )
    paragraphs = list(_PARAGRAPH_RE.finditer(text))

    def _paragraph_index_for(pos):
        for i, p in enumerate(paragraphs):
            if p.start() <= pos < p.end():
                return i
        return None

    bad_paragraph_idxs = set()
    for match in _PRICE_MENTION_RE.finditer(text):
        raw = match.group(1)
        digits_only = re.sub(r'[.,]', '', raw)
        as_int = int(digits_only) if digits_only.isdigit() else None

        current_product = None
        for name_pos, name in name_positions:
            if name_pos > match.start():
                break
            current_product = name
        allowed_prices = (
            known_prices_by_product[current_product]
            if current_product else all_known_prices
        )

        if as_int not in allowed_prices:
            _logger.warning(
                "Livechat-KI-Bot: Preisangabe in Bot-Antwort (%s EUR%s) "
                "passt zu keinem bekannten Katalogpreis - ersetze den "
                "betroffenen Absatz durch eine sichere Ausweichantwort "
                "statt eine moeglicherweise falsche Zahl zu zeigen.", raw,
                f" fuer '{current_product}'" if current_product else "",
            )
            idx = _paragraph_index_for(match.start())
            if idx is None:
                # Kein erkennbarer <p>-Absatz - komplette Antwort ersetzen,
                # wie vor der absatzweisen Aufteilung.
                return fallback_note
            bad_paragraph_idxs.add(idx)

    if not bad_paragraph_idxs:
        return text
    if len(bad_paragraph_idxs) == len(paragraphs):
        return f'<p>{fallback_note}</p>'

    result = []
    last_end = 0
    for i, p in enumerate(paragraphs):
        result.append(text[last_end:p.start()])
        result.append(f'<p>{fallback_note}</p>' if i in bad_paragraph_idxs else p.group(0))
        last_end = p.end()
    result.append(text[last_end:])
    return ''.join(result)


# Odoo-Sprachcode (Basisteil vor dem '_', z.B. 'en' aus 'en_US') -> deutscher
# Sprachname fuers Prompt-Argument 'customer_language' UND fuer die
# Uebersetzungsanweisung in _translate_to_language. Bewusst eine kleine,
# explizite Liste statt res.lang.name direkt zu verwenden - das liefert
# Werte wie "English (US)" oder "German / Deutsch", die mitten im
# deutschsprachigen Prompt-Satz seltsam wirken. Fehlt der Code hier
# (unbekannte/nicht gelistete Sprache), bleibt customer_language leer und
# der Default im Kundenservice-Prompt (Deutsch, siehe arguments_json)
# greift unveraendert - bisheriges Verhalten fuer alles, was nicht explizit
# unterstuetzt wird.
VISITOR_LANGUAGE_NAMES = {
    'de': 'Deutsch',
    'en': 'Englisch',
    'fr': 'Franzoesisch',
    'es': 'Spanisch',
    'it': 'Italienisch',
    'nl': 'Niederlaendisch',
    'pt': 'Portugiesisch',
    'pl': 'Polnisch',
}


def visitor_language_name(lang_code):
    if not lang_code:
        return None
    return VISITOR_LANGUAGE_NAMES.get(lang_code.split('_')[0])


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    llm_thread_id = fields.Many2one(
        'llm.thread',
        string='KI-Thread',
        copy=False,
        help="Der llm.thread, der den Gespraechsverlauf dieses Livechat-Kanals "
             "gegenueber der KI abbildet.",
    )

    def _llm_bot_visitor_lang_code(self):
        """Odoo-Sprachcode (z.B. 'en_US') des Besucher-Mitglieds dieses Kanals -
        NIE des Bot-Partners oder eines internen Operators. Gast-Besucher
        (mail.guest, kein Login) UND eingeloggte Portal-/Kundenkontakte
        (res.partner mit partner_share=True) werden beruecksichtigt, da
        beide als "Besucher" im Sinne von _llm_bot_try_reply gelten.
        Liefert False, wenn kein Besucher-Mitglied gefunden wird oder dessen
        lang-Feld leer ist."""
        self.ensure_one()
        visitor_member = self.channel_member_ids.filtered(
            lambda m: m.guest_id or (m.partner_id and m.partner_id.partner_share),
        )[:1]
        if not visitor_member:
            return False
        return visitor_member.guest_id.lang or visitor_member.partner_id.lang

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
        visitor_lang_code = self._llm_bot_visitor_lang_code()
        visitor_lang_base = visitor_lang_code.split('_')[0] if visitor_lang_code else None

        # Live beobachtet (26.08.): bei einem sicheren faq_price_lookup-Treffer
        # (matched=True) uebernimmt die Katalog-Antwort direkt, statt dem
        # Modell die Wiedergabe der Zahl zu ueberlassen - siehe
        # llm_thread.py: _llm_bot_faq_override_answer() fuer den Hintergrund
        # (selbst mit expliziter "wortwoertlich"-Anweisung im Tool-Ergebnis
        # hat das Modell Zahlen weiterhin verstuemmelt, der Preis-Guard
        # unten hat das zwar zuverlaessig abgefangen, aber nur durch eine
        # Ausweichfloskel statt der eigentlich schon vorliegenden Antwort).
        #
        # Live beobachtet (26.08., Folgefrage "Und das All-In Bundle
        # Standard?"): das Modell hatte den Preis in DERSELBEN Konversation
        # bereits per Tool ermittelt, rief bei der Folgefrage aber KEIN
        # Tool mehr auf ("kenne ich ja schon") und zitierte stattdessen aus
        # dem Gedaechtnis - dabei "299" zu "29" verstuemmelt. Ein Override,
        # der nur Tool-Aufrufe der aktuellen Runde prueft, greift dann
        # nicht. Deshalb zusaetzlich die rohe Kundenfrage SELBST direkt
        # klassifizieren (faq_catalog_lookup, siehe llm_tool.py) - komplett
        # unabhaengig davon, ob/wie das Modell ein Tool genutzt hat. Nur
        # price_overview bleibt aussen vor (siehe dortiger Docstring: zu
        # generischer Treffer allein ueber ein Preiswort, zu hohes
        # Fehltreffer-Risiko fuer blindes Ersetzen).
        faq_entry = faq_catalog_lookup(body_text, exclude_intent_ids={'price_overview'})
        faq_override = faq_entry['answer'] if faq_entry else thread._llm_bot_faq_override_answer()
        if faq_override:
            _logger.info(
                "Livechat-KI-Bot: Preisantwort durch faq_price_lookup-"
                "Katalogtext ersetzt (Modell-Wiedergabe uebersprungen).",
            )
        source_body = f'<p>{faq_override}</p>' if faq_override else (reply.body or '')

        clean_body = _strip_fabricated_self_urls(source_body)
        clean_body = _ensure_customer_language(
            clean_body, thread.provider_id, thread.model_id,
            visitor_lang_base, visitor_language_name(visitor_lang_code),
        )
        clean_body = _validate_prices(
            clean_body, _get_known_prices_by_product(self.env), visitor_lang_base,
        )

        # Live beobachtet (21.08.): die Bereinigung oben wurde bisher NUR auf
        # typing_message geschrieben - die fuer den Besucher sichtbare Kopie
        # im discuss.channel. Die eigentliche Assistant-Nachricht im
        # llm.thread (reply) blieb unveraendert mit dem ROHEN, halluzinierten
        # Text stehen. Genau reply.body ist aber das, was als Gespraechs-
        # verlauf an das Modell zurueckgegeben wird (siehe
        # llm_ollama/models/mail_message.py: ollama_format_message() liest
        # self.body der Assistant-Nachricht direkt als "content" fuer
        # kuenftige Turns). Ergebnis: der Kunde hat auf Nachfrage
        # ("29 EUR erscheint mir sehr preiswert, bitte bestaetigen") vom
        # Modell den falschen Preis erneut serviert bekommen - das Modell hat
        # schlicht seine eigene ungefilterte erste Antwort aus dem
        # gespeicherten Verlauf zitiert, die Bereinigung war rein kosmetisch
        # fuer die Anzeige und hat das Modellgedaechtnis nie erreicht. Beide
        # Kopien muessen daher denselben bereinigten Text bekommen, sonst
        # bleibt der Fehler im Kontext des laufenden Chats dauerhaft bestehen.
        reply.write({'body': clean_body})

        typing_message.write({'body': clean_body})
        # Live beobachtet (26.08.): die Antwort ersetzte den "tippt..."-
        # Platzhalter beim Besucher zuverlaessig NUR nach einem manuellen
        # Browser-Refresh - vorher blieb sichtbar einfach der Platzhalter
        # (oder eine leer wirkende Antwort) stehen. Ursache: die Bus-
        # Benachrichtigung uebergab bisher rohe ORM-Attributwerte
        # (typing_message.write_date als Python-datetime-Objekt) direkt als
        # "values" an _bus_send_store() - das umgeht Store.add()'s
        # Model-Zweig komplett (siehe mail/tools/discuss.py: "values is not
        # None" ueberspringt den Aufruf von data._to_store()) und damit auch
        # dessen korrekte Feld-Serialisierung (unter anderem write_date als
        # String statt als natives Python-Objekt). Die urspruengliche
        # Platzhalter-Nachricht oben (message_post()) wird dagegen intern
        # ganz normal ueber _to_store() an den Client gemeldet und erscheint
        # deshalb sofort - nur DIESES manuelle Nach-Update nicht. Fix: ohne
        # eigene "values" aufrufen, damit mail.message._to_store() (dieselbe
        # Serialisierung wie bei jedem normalen Nachrichtenabruf) greift.
        typing_message._bus_send_store(typing_message)
        # Live beobachtet (26.08., mistral-small3.2:24b): der Refresh-Bug
        # trat trotz des Serialisierungs-Fixes oben erneut auf, diesmal mit
        # einem deutlich langsameren Modell (12-90s statt 3-15s). Ursache:
        # ab dem fruehen Zwischen-Commit oben (direkt nach dem Platzhalter,
        # noetig damit "tippt..." sofort erscheint - siehe Kommentar dort)
        # laeuft alles Weitere (Generierung + Guards + dieses finale Update)
        # in EINER neuen, aber nie explizit committeten Transaktion auf
        # demselben Cursor - sie wird erst beim normalen Abschluss der
        # urspruenglichen Besucher-HTTP-Anfrage automatisch committet, und
        # Postgres NOTIFY (worueber der Bus laufende Websocket-Verbindungen
        # informiert) feuert erst BEI COMMIT, nicht beim blossen Schreiben.
        # Bei einer 12-90s dauernden Anfrage kann das ausserhalb der
        # Toleranz von Proxy/Browser liegen, wodurch die Meldung zwar
        # irgendwann in der DB landet (siehe write_date), aber der Browser
        # sie nie live mitbekommt - nur ein Refresh fragt den Stand dann
        # neu ab. Derselbe explizite Commit wie beim Platzhalter behebt es
        # auch hier: NOTIFY feuert sofort, unabhaengig davon, wie lange die
        # urspruengliche Anfrage schon laeuft oder noch braucht.
        self.env.cr.commit()

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
