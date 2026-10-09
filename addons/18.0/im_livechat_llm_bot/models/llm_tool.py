import functools
import json
import re

from odoo import models
from odoo.addons.llm_tool.decorators import llm_tool
from odoo.tools import file_open

# Live beobachtet (Benchmark ANGEBOT-2, mistral-small3.2:24b): das Modell darf
# similarity_cutoff selbst waehlen und griff bei einem Folgeaufruf zur selben
# Frage zu einem strengeren Wert (0.7) als unser Standard (0.5) - der Aufruf
# lieferte dadurch 0 Treffer, obwohl ein VORHERIGER Aufruf in derselben
# Konversation mit dem Standardwert die richtige Antwort bereits gefunden
# hatte. Statt das fruehere gute Ergebnis zu nutzen, hat das Modell danach
# einen Gruendernamen frei erfunden. Der Cutoff wird daher serverseitig
# gedeckelt, damit "Modell waehlt zu strengen Filter -> 0 Treffer" strukturell
# nicht mehr passieren kann.
MAX_SIMILARITY_CUTOFF = 0.5

# Live beobachtet (13.08., Frage "Wo werden meine Daten gespeichert?"): die
# inhaltlich richtige Antwort (Homepage-Abschnitt "Data Sovereignty - Your
# data never leaves your company") liegt mit 50-53% Aehnlichkeit nur knapp
# vor thematisch verwandten, aber falschen Treffern (Kontaktformular-
# Datenschutzhinweis 56%, Preisgestaltungs-AGB/DPA-Absatz 54%). Bei top_n=3
# (Standard) ist es reine Zufallssache, ob die korrekte Ressource ueberhaupt
# in die Auswahl kommt, die dem Modell praesentiert wird - unabhaengig von
# Sampling-Temperatur oder Prompt, weil der richtige Chunk dem Modell in
# diesem Fall schlicht nie gezeigt wurde. top_n wird daher serverseitig auf
# einen Mindestwert angehoben, analog zum similarity_cutoff-Deckel oben:
# das Modell darf den Suchraum grosszuegiger, aber nicht enger als sicher
# waehlen.
MIN_TOP_N = 5


def _coerce_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Preis-Katalog-Tool: regelbasiertes Keyword-Matching statt Fuzzy-Aehnlichkeit.
#
# Hintergrund: das bekannte Preis-Mangling-Problem (Zahlen ab ca. 1000 werden
# beim Wiedergeben in Fliesstext verstuemmelt, siehe _validate_prices in
# discuss_channel.py) entsteht, weil das Modell Preise selbst aus einem
# langen Kontext heraussuchen und umformatieren muss. Dieses Tool liefert
# stattdessen die fertige, exakte Antwort aus einem kuratierten Katalog
# (data/faq_catalog.json) - das Modell soll sie nur noch vortragen/
# umformulieren, nicht mehr selbst zusammensuchen. (Ein gezieltes
# LoRA-Fine-Tuning genau dieses Problems wurde am 19.08.2026 nach fuenf
# Trainingsrunden ergebnislos eingestellt, siehe /home/raffael/Projekte/lora
# - das Problem erwies sich als inhaerente Modell-Eigenschaft, kein
# Trainingsdaten-Problem. Dieser Katalog umgeht es strukturell, indem er dem
# Modell die Zahl fertig vorformatiert liefert, statt sie neu erzeugen zu
# lassen.)
#
# Ein erster Prototyp hat die Kundenfrage per Jaccard-Aehnlichkeit (Wort-
# ueberlappung, wie knowledge_retriever es per Embeddings macht) gegen die
# Katalog-Fragen/Paraphrasen verglichen. Test gegen 233 echte Kunden-
# Paraphrasen (aus dem oben genannten LoRA-Projekt): 80% korrekt, aber die
# restlichen 20% waren oft KONFIDENT FALSCH (z.B. 71% "Aehnlichkeit" fuer
# "Standard", obwohl "Professional" gefragt war) - genau das Risiko, das
# dieses Tool eigentlich vermeiden soll. Grund: generische Woerter ("kosten",
# "monatlich") verwaessern die Aehnlichkeit, obwohl nur 1-2 Woerter
# (Produktname, Kategorie) tatsaechlich unterscheiden.
#
# Ersetzt durch eine explizite Slot-Erkennung (Produkt-Tier x Kategorie), da
# der Wortschatz hier klein und geschlossen ist (4 Tiers x 3 Kategorien plus
# ein paar Sonderfaelle wie MwSt./Rabatt/Starter-Paket). Gegen denselben
# Testsatz: 84% korrekt, 9% ehrlich "kein Treffer" (loest sicher den
# knowledge_retriever/search_sellable_products-Pfad aus), nur noch ~1%
# tatsaechlich riskante Fehlzuordnung - und die verbleibenden Faelle sind
# durchweg stark verstuemmelte Paraphrasen ohne jeden Produktnamen (z.B.
# "Professional hat?"), die auch ein Mensch ohne Gespraechskontext nicht
# zuordnen koennte. Kein Treffer ist hier immer die sicherere Wahl als ein
# falscher, konkreter Preis - bei Unsicherheit gibt _classify_faq_query
# daher lieber None zurueck, statt zu raten.
# ---------------------------------------------------------------------------

FAQ_TIER_KEYWORDS = {
    'standard': ['standard'],
    'professional': ['professional', 'pro-version', 'pro version'],
    'premium': ['premium'],
    'enterprise': ['enterprise'],
    'starter': ['starter'],
}
FAQ_CATEGORY_BUNDLE = ['all-in bundle', 'all in bundle', 'allin bundle', 'bundle']
FAQ_CATEGORY_SERVICE_PLAN_STRONG = ['service plan', 'serviceplan', 'service-plan']
# Bewusst NUR "plan" als schwaches Signal, kein "monatlich"/"im monat": beide
# Hardware UND All-In Bundle werden in echten Kundenformulierungen manchmal
# faelschlich mit "im Monat" umschrieben (Paraphrase-Artefakt, siehe
# lora/data/paraphrases.json, slot hw_standard) - "monatlich" allein haette
# sonst Hardware-Fragen faelschlich zu Service-Plan-Antworten geroutet.
FAQ_CATEGORY_SERVICE_PLAN_WEAK = ['plan']
FAQ_CATEGORY_HARDWARE = [
    'hardware-appliance', 'hardware appliance', 'hardware', 'appliance',
    'gerät', 'geraet', 'anschaffung',
]
FAQ_FESTPREIS_KEYWORDS = ['festpreis', 'fixpreis', 'festen preis']
FAQ_STARTER_COMBO_HINTS = [
    'starter-paket', 'starterpaket', 'einstiegspaket', 'einsteigerpaket',
    'klein einsteigen', 'klein anfangen', 'starter set', 'starter-set',
    'einstieg', 'kleinste paket', 'kleines paket', 'basis-paket', 'testen',
    'loszulegen', 'nur gucken', 'der start', 'minimum an kosten', 'klein starten',
]
FAQ_VAT_KEYWORDS = [
    'mehrwertsteuer', 'mwst', 'umsatzsteuer', 'ust.', 'brutto', 'netto',
    'steuer', 'steuern',
]
FAQ_DISCOUNT_KEYWORDS = [
    'rabatt', 'nachlass', 'vergünstigung', 'vergunstigung', 'preisvorteil',
    'staffelpreis', 'mengenrabatt', 'preisnachlass', 'ermäßigung', 'ermassigung',
    'preisreduktion', 'spezialpreis', 'besserer preis', 'sparen', 'entgegenkommen',
    'abhandeln',
]
FAQ_PRESSURE_KEYWORDS = [
    'konkurrenz', 'sonst', 'abspringen', 'wechsel', 'wechseln',
    'anderen anbieter', 'alternative',
]
FAQ_PRICE_WORDS = ['kostet', 'kosten', 'preis', 'bezahlen', 'zahl', 'zahlen', 'gebühren', 'gebuhren']


def _faq_has_word(text, word):
    # Mehrwort-Ausdruecke ("service plan") und Abkuerzungen mit Punkt
    # ("ust.") per einfachem Substring pruefen - Wortgrenzen-Regex wuerde am
    # eingebetteten Leerzeichen/Punkt scheitern.
    if ' ' in word or '.' in word:
        return word in text
    # Live beobachtet (26.08.): das Modell fasst die Kundenfrage beim
    # Tool-Aufruf oft eigenmaechtig zusammen (query="Rabatte" statt der
    # eigentlichen Kundenfrage "Ich will 30% Rabatt..."). Beidseitige
    # Wortgrenze (\bwort\b) hat dadurch GRENZE-2 verpasst: "Rabatte" endet
    # nicht direkt nach "rabatt" (das "e" folgt unmittelbar), also gibt es
    # dort keine rechte Wortgrenze. Nur noch linksseitig ankern (Praefix-
    # Match) faengt deutsche Flexionsformen ab (Rabatt/Rabatte/Rabatten,
    # Appliance/Appliances, ...), waehrend "plan" in "geplant" weiterhin
    # NICHT anschlaegt (dort beginnt "plan" nicht direkt nach einer
    # Wortgrenze - "ge" steht unmittelbar davor).
    return re.search(r'\b' + re.escape(word), text) is not None


def _faq_any(text, words):
    return any(_faq_has_word(text, w) for w in words)


def _classify_faq_query(query):
    """Ordnet eine Kundenfrage einer intent_id aus faq_catalog.json zu, oder
    None, wenn keine Zuordnung sicher genug ist (siehe Kommentar oben - im
    Zweifel lieber kein Treffer als ein falscher)."""
    q = (query or '').lower()

    if _faq_any(q, FAQ_VAT_KEYWORDS):
        return 'vat'

    if _faq_any(q, FAQ_DISCOUNT_KEYWORDS):
        if _faq_any(q, FAQ_PRESSURE_KEYWORDS) or re.search(r'\d+\s?%', q):
            return 'escalation_discount_pressure'
        return 'no_documented_discount'

    if _faq_any(q, FAQ_FESTPREIS_KEYWORDS) and (_faq_any(q, FAQ_CATEGORY_BUNDLE) or 'enterprise' in q):
        return 'bundle_enterprise_on_request'

    if _faq_any(q, FAQ_STARTER_COMBO_HINTS) and not _faq_any(q, FAQ_CATEGORY_SERVICE_PLAN_STRONG):
        return 'combo_starter'

    tier = next((t for t, kws in FAQ_TIER_KEYWORDS.items() if _faq_any(q, kws)), None)
    if tier:
        is_bundle = _faq_any(q, FAQ_CATEGORY_BUNDLE)
        is_hardware = _faq_any(q, FAQ_CATEGORY_HARDWARE)
        is_service_plan = _faq_any(q, FAQ_CATEGORY_SERVICE_PLAN_STRONG) or (
            not is_bundle and not is_hardware and _faq_any(q, FAQ_CATEGORY_SERVICE_PLAN_WEAK)
        )
        if tier == 'starter':
            # Bare "Starter" ohne "Service Plan"-Erwaehnung meint in der
            # Kundenwahrnehmung meist das beworbene Einsteigerpaket (Kombi
            # aus Hardware + Service Plan Starter), nicht den Service Plan
            # allein - siehe PREIS-2 im Testkatalog.
            return 'sp_starter' if is_service_plan else 'combo_starter'
        if is_hardware:
            return f'hw_{tier}'
        if is_bundle:
            return 'bundle_enterprise_on_request' if tier == 'enterprise' else f'bundle_{tier}'
        if is_service_plan:
            return f'sp_{tier}'
        if tier == 'enterprise':
            # "Enterprise" ohne erkennbare Kategorie ist laut Preisuebersicht
            # bewusst dreifach mehrdeutig (Hardware/Service Plan/Bundle) -
            # siehe ambiguous_enterprise im Katalog.
            return 'ambiguous_enterprise'
        return f'hw_{tier}'  # nackter Produktname meint die Hardware-Linie

    if _faq_any(q, FAQ_PRICE_WORDS):
        return 'price_overview'

    return None


@functools.lru_cache(maxsize=1)
def _load_faq_catalog():
    # Datei ist statisch (manuelle Kuration, siehe _meta in der JSON-Datei
    # selbst) - einmal pro Odoo-Prozess laden statt bei jedem Tool-Aufruf
    # neu zu parsen.
    with file_open('im_livechat_llm_bot/data/faq_catalog.json', mode='rb') as f:
        data = json.load(f)
    return {entry['intent_id']: entry for entry in data['intents']}


class LLMTool(models.Model):
    _inherit = 'llm.tool'

    def _llm_bot_default_knowledge_collection_id(self):
        # Fallback, falls das Modell collection_id im Tool-Aufruf weglaesst
        # (siehe Kommentar unten). Nur ausfuellen, wenn die Wahl eindeutig
        # ist - bei mehreren aktiven Collections wuerde ein Raten hier
        # stillschweigend die falsche Wissensbasis durchsuchen, was
        # schwerer zu bemerken waere als der urspruengliche Fehler. Dann
        # lieber weiterhin explizit scheitern lassen.
        collections = self.env['llm.knowledge.collection'].sudo().search([('active', '=', True)])
        return collections.id if len(collections) == 1 else None

    def _llm_bot_resolve_collection_id(self, collection_id):
        # Live beobachtet (17.08., glm-4.7-flash): das Modell schickt statt
        # der numerischen ID gelegentlich den ANZEIGENAMEN der Collection als
        # collection_id ("Website-Inhalte" statt z.B. 2) - vermutlich weil
        # das Tool-Schema den Namen irgendwo als Beispiel/Beschreibung
        # erwaehnt. int("Website-Inhalte") schlaegt fehl, und die alte
        # _coerce_int(collection_id, collection_id)-Faelle gab dann denselben
        # kaputten String unveraendert zurueck, der weiter unten mit
        # "Expected singleton: llm.knowledge.collection('W','e','b',...)"
        # abstuerzte (Odoo interpretiert einen String bei .browse() als
        # Zeichen-fuer-Zeichen-Iterable von IDs). Deshalb hier: erst auf int
        # versuchen, sonst per Namen nachschlagen, sonst auf den
        # Single-Collection-Fallback zurueckfallen statt den kaputten Wert
        # durchzureichen.
        as_int = _coerce_int(collection_id, None)
        if as_int is not None:
            return as_int
        if isinstance(collection_id, str) and collection_id.strip():
            match = self.env['llm.knowledge.collection'].sudo().search(
                [('active', '=', True), ('name', '=ilike', collection_id.strip())], limit=1)
            if match:
                return match.id
        return self._llm_bot_default_knowledge_collection_id()

    def knowledge_retriever_execute(self, query, collection_id=None, top_k=5, top_n=3, similarity_cutoff=0.5):
        # Robustheits-Fix Teil 1: Modelle liefern Tool-Argumente nicht immer im
        # erwarteten Python-Typ - live beobachtet, dass collection_id und
        # similarity_cutoff manchmal als String statt int/float ankommen
        # ("1" statt 1, "0.7" statt 0.7). collection_id="1" liess die
        # Collection-Suche in llm_tool_knowledge mit "Datensatz existiert
        # nicht" fehlschlagen (browse() mit String statt int), und
        # similarity_cutoff="0.7" liess den min()-Vergleich unten mit
        # TypeError abstuerzen. In beiden Faellen bekam das Modell einen
        # Tool-Fehler statt eines Ergebnisses zurueck und hat daraufhin
        # frei Fakten erfunden, statt die vorherige (funktionierende)
        # Antwort im selben Gespraech zu nutzen. Deshalb hier defensiv auf
        # die erwarteten Typen casten, bevor der eigentliche Tool-Code laeuft.
        #
        # Robustheits-Fix Teil 2 (Benchmark vom 13.08.: 33x bei qwen3.6:27b,
        # 20x bei nemotron-3.5-lightning:30b, 2x bei mistral-nemo:12b auf nur
        # 10 Fragen): das Modell laesst collection_id im Tool-Aufruf oft
        # komplett weg. llm_tool.execute() (odoo-llm) validiert Tool-Argumente
        # gegen ein Pydantic-Modell, das aus DIESER Methodensignatur erzeugt
        # wird, BEVOR der Methodenkoerper hier je ausgefuehrt wird - stand
        # collection_id ohne Default da, war sie dort ein Pflichtfeld und ein
        # fehlender Wert brach schon in der Validierung mit "Field required"
        # ab. Die Typkorrektur oben griff also nie, weil der Aufruf gar nicht
        # bis hierher kam. Deshalb jetzt: collection_id optional, mit
        # serverseitigem Fallback auf die (aktuell einzige) aktive Collection,
        # statt vom Modell zu verlangen, eine ID korrekt zu wiederholen, die
        # es selbst nie ausgewaehlt hat, sondern nur aus der Tool-Beschreibung
        # abschreiben musste.
        if not collection_id:
            collection_id = self._llm_bot_default_knowledge_collection_id()
        collection_id = self._llm_bot_resolve_collection_id(collection_id)
        top_k = _coerce_int(top_k, 5)
        top_n = _coerce_int(top_n, 3)
        top_n = max(top_n, MIN_TOP_N)
        similarity_cutoff = _coerce_float(similarity_cutoff, 0.5)
        similarity_cutoff = min(similarity_cutoff, MAX_SIMILARITY_CUTOFF)
        result = super().knowledge_retriever_execute(
            query, collection_id, top_k=top_k, top_n=top_n, similarity_cutoff=similarity_cutoff,
        )
        # Live beobachtet (15.08., Frage "Wo werden meine Daten gespeichert?"):
        # mistral-small3.2:24b rief das Tool EINMAL mit einer ungluecklichen
        # Formulierung auf, bekam 0 Treffer zurueck - und behauptete in der
        # Antwort trotzdem selbstbewusst konkrete, falsche Fakten ("Ihre Daten
        # werden ausschliesslich in Deutschland auf sicheren Servern
        # gespeichert"), obwohl der System-Prompt genau das explizit verbietet
        # ("Finden die Tools nichts Passendes, sag das ehrlich..."). Dieselbe
        # Prompt-Regel wird also nicht zuverlaessig befolgt, sobald das Tool
        # leer zurueckkommt - reine Text-Instruktionen reichen hier nicht,
        # analog zur Erfahrung mit Preisen/Namen, wo erst das Entfernen aus
        # der Quelle zuverlaessig half. Da es hier nichts zu entfernen gibt
        # (die Info soll ja gefunden werden), wird stattdessen ein nicht zu
        # uebersehender Hinweis DIREKT ins Tool-Ergebnis eingebettet - das
        # Modell behandelt Tool-Ergebnisse erwiesenermassen als autoritativer
        # als Fliesstext im System-Prompt.
        if isinstance(result, dict) and not result.get('results'):
            result['hinweis'] = (
                'Keine passenden Informationen gefunden. Nenne JETZT KEINE '
                'konkreten Fakten zu dieser Frage (auch nicht aus eigenem '
                'Wissen) - sag ehrlich, dass du es nicht weisst, und biete '
                'einen menschlichen Mitarbeiter an.'
            )
        return result

    @llm_tool(read_only_hint=True, idempotent_hint=True)
    def faq_price_lookup(self, query: str) -> dict:
        """Beantwortet konkrete Preisfragen zu PrivateMind (Hardware-Appliances,
        Service Plans, All-In Bundles, Starter-Paket, Mehrwertsteuer, Rabatte)
        aus einem manuell kuratierten und geprueften Preis-Katalog.

        Nutze dieses Tool IMMER ZUERST bei jeder Preisfrage zu PrivateMind
        Standard/Professional/Premium/Enterprise, zum "Starter-Paket", zur
        Mehrwertsteuer oder zu Rabatten - noch vor search_sellable_products
        oder eigenem Wissen. Die zurueckgegebene answer ist bereits
        vollstaendig und fachlich geprueft: gib sie inhaltlich unveraendert
        wieder (Zahlen exakt uebernehmen, nicht neu ausrechnen oder
        umformatieren), du darfst nur Ton und Satzbau an die Frage anpassen.
        matched=false bedeutet: dieser Katalog deckt die Frage nicht ab -
        nutze dann knowledge_retriever/search_sellable_products, oder sag
        ehrlich, dass du es nicht weisst, statt zu raten.

        Args:
            query: Die Preisfrage des Kunden, moeglichst im Original-Wortlaut

        Returns:
            Dictionary mit matched (bool) und bei einem Treffer answer (der
            fertige Antworttext) sowie facts (strukturierte Zahlen zur
            eigenen Kontrolle, nicht zum Vorlesen gedacht)
        """
        intent_id = _classify_faq_query(query)
        catalog = _load_faq_catalog()
        entry = catalog.get(intent_id) if intent_id else None
        if not entry:
            return {
                'matched': False,
                'hinweis': (
                    'Keine passende Antwort im kuratierten Preis-Katalog '
                    'gefunden - nutze knowledge_retriever oder '
                    'search_sellable_products, oder sag ehrlich, dass du es '
                    'nicht weisst, statt zu raten.'
                ),
            }
        return {
            'matched': True,
            'intent_id': entry['intent_id'],
            'answer': entry['answer'],
            # Live beobachtet (26.08., gemma4:12b): trotz der Anweisung in
            # der Docstring oben ("gib sie inhaltlich unveraendert wieder")
            # hat das Modell "3.800 EUR" beim Vortragen zu "3.80 EUR"
            # verstuemmelt - der Preis-Guard in discuss_channel.py hat die
            # falsche Zahl zwar sicher abgefangen, aber die Antwort landete
            # dadurch nur noch als Ausweichtext beim Kunden statt der
            # eigentlich schon korrekt vorliegenden Antwort. Docstrings
            # werden offenbar nicht immer so zuverlaessig befolgt wie
            # Hinweise DIREKT im Tool-Ergebnis (siehe hinweis-Feld in
            # knowledge_retriever_execute oben) - deshalb dieselbe Anweisung
            # hier zusaetzlich als Daten statt nur als Docstring-Text.
            'hinweis': (
                'WICHTIG: gib den Text in "answer" WORTWOERTLICH wieder, '
                'insbesondere jede Ziffer exakt wie dort geschrieben. Du '
                'darfst nur Anrede/Uebergangssatz an die Frage anpassen, '
                'niemals aber eine Zahl neu aufschreiben, runden oder '
                'umformatieren.'
            ),
            'facts': entry.get('facts'),
        }


def faq_catalog_lookup(query, *, exclude_intent_ids=frozenset()):
    """Klassifiziert `query` DIREKT gegen den Preis-Katalog, unabhaengig
    davon, ob/wie das Modell faq_price_lookup aufgerufen hat. Liefert den
    Katalog-Eintrag (dict mit u.a. "answer") oder None.

    Live beobachtet (26.08.): bei einer Folgefrage ("Und das All-In Bundle
    Standard?") hat das Modell in derselben Konversation KEIN Tool mehr
    aufgerufen (es hatte den Preis ja "schon mal genannt") und stattdessen
    aus dem Gedaechtnis zitiert - dabei "299" zu "29" verstuemmelt. Ein
    Override, der nur auf tatsaechliche Tool-Aufrufe in der aktuellen Runde
    schaut (siehe llm_thread.py: _llm_bot_faq_override_answer), greift in
    diesem Fall nicht, weil es gar keinen Tool-Aufruf gibt. Diese Funktion
    umgeht das Problem strukturell: sie klassifiziert die rohe Kundenfrage
    selbst, komplett unabhaengig vom Modell/Tool-Aufrufverhalten.

    exclude_intent_ids: bewusst ohne price_overview standardmaessig nutzen
    (siehe discuss_channel.py) - das ist der einzige Intent, der ueber
    einen generischen Kontext-Treffer (blosses Vorkommen eines Preiswortes
    wie "kostet") zustandekommt, nicht ueber einen konkreten Produkt-/
    Kategorie-Namen. Als BLINDER Ersatz fuer JEDE Nachricht mit einem
    Preiswort waere das Risiko falscher Treffer (z.B. "Was kostet die
    Einrichtung, wird die von euch uebernommen?") zu hoch. Alle anderen
    Intents verlangen einen konkreten Produkt-/Themenbegriff und sind
    entsprechend praeziser.
    """
    intent_id = _classify_faq_query(query)
    if not intent_id or intent_id in exclude_intent_ids:
        return None
    return _load_faq_catalog().get(intent_id)
