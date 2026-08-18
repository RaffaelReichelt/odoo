from odoo import models

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
