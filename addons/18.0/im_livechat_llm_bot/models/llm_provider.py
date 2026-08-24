import contextvars

import httpx
import ollama

from odoo import models

# Live beobachtet (18.08.): mehrfach reproduzierbare Haenger OHNE erkennbaren
# Netzwerkfehler - GX10 blieb laut direktem curl jederzeit sofort erreichbar,
# trotzdem blieb ein einzelner Chat-Aufruf (glm-4.7-flash, gemma4:12b) nach
# einer bereits empfangenen HTTP-200-Antwort mitten im Streaming fuer >20-30
# Minuten haengen, ohne jede weitere Log-Zeile. Ursache: das ollama-Python-
# Paket setzt per Default httpx timeout=None (siehe ollama._client.BaseClient
# - explizit dokumentiert als bewusste Abweichung vom httpx-Standard), also
# WARTET DIE VERBINDUNG UNBEGRENZT auf den naechsten Stream-Chunk. Haengt der
# Server (GPU-Hang, Modell-Loop, o.ae.) mitten in der Generierung, blockiert
# das den kompletten Odoo-Request-Worker fuer diesen Chat unbegrenzt - im
# Live-Betrieb sieht der Kunde ewig "tippt...", nichts erholt sich von
# selbst. Ein Timeout wandelt das in einen sauberen, nach spaetestens 7
# Minuten geloggten Fehler um (siehe discuss_channel.py: der Aufruf ist dort
# bereits in try/except eingebettet) statt eines unbegrenzten Haengers. Die
# 7 Minuten sind bewusst grosszuegig gewaehlt - die laengste beobachtete
# LEGITIME Antwortzeit in dieser Session lag bei 327s (qwen3.6:27b).
_OLLAMA_TIMEOUT = httpx.Timeout(connect=15.0, read=420.0, write=30.0, pool=15.0)

# odoo-llm's ollama_chat() (llm_ollama, 18.0) uebergibt Ollama KEINE "options"
# (z.B. temperature) - das Modell laeuft also immer mit Ollamas Standardwert
# (typischerweise 0.8, eher fuer kreatives Schreiben als fuer Faktentreue).
# Live beobachtet: dieselbe Frage ("welche IT-Kenntnisse braucht man?") wurde
# bei einem Durchlauf korrekt aus dem Tool-Ergebnis beantwortet ("keine
# tiefgreifenden IT-Kenntnisse noetig") und beim naechsten Durchlauf trotz
# identischem Kontext genau umgekehrt ("Sie benoetigen Kenntnisse in
# IT-Sicherheit und Netzwerkverwaltung") - reine Sampling-Streuung, kein
# spezifischer Bug. Niedrigere Temperatur reduziert diese Streuung fuer ALLE
# Fragen gleichzeitig, statt jede Fehlerkategorie einzeln per Prompt-Regel
# abzusichern (skaliert nicht: Preise/Namen/Produkte sind bereits abgesichert,
# aber jede neue Frageart kann theoretisch dieselbe Streuung zeigen).
DEFAULT_TEMPERATURE = 0.1

# Live beobachtet (23.08.): llm_thread.py entzieht dem Modell nach
# tool_calls_max erreichten Tool-Runden die Tools, um eine Text-Antwort zu
# erzwingen - aber ohne Tools UND ohne Laengenlimit generiert das Modell
# manchmal einfach weiter, statt mit einem Stop-Token zu enden (bei
# repeat_penalty=1.0, dem Ollama-Default, keine Bremse gegen Wiederholungs-
# schleifen). Live beobachtet: eine einzelne erzwungene Antwort lief >1200
# Tokens weit ueber jede sinnvolle Chat-Antwort hinaus und wurde nur durch
# einen zufaelligen Server-Neustart beendet - bis dahin volle GPU-Last, der
# Kunde sah nur "tippt...". ollama_chat() (llm_ollama, 18.0) reicht kein
# "options"-Kwarg an self.client.chat() durch, daher kann llm_thread.py kein
# num_predict-Limit ueber den normalen kwargs-Weg setzen. Dieser ContextVar
# ist der Umweg: llm_thread.py setzt ihn direkt vor dem erzwungenen Call,
# _LowTemperatureOllamaClient liest ihn beim tatsaechlichen API-Aufruf.
#
# Zweite Falle, NACH Einbau des num_predict-Limits live beobachtet: das
# Modell (gemma4, chat_template unterstuetzt "thinking") verbrachte das
# GESAMTE Token-Budget in seinem unsichtbaren Denkblock, ohne je zur
# sichtbaren Antwort zu kommen - Ollama trennt "thinking" und "content"
# in response["message"], und ollama_process_*_response() (llm_ollama,
# 18.0) liest nur "content". Ergebnis: Generierung endet sauber nach dem
# Limit, aber response["message"]["content"] bleibt leer, also landet
# NIE eine Nachricht im Chat - fuer den Besucher exakt wie der urspruengliche
# Haenger, nur ohne GPU-Dauerlast. Fix: Denkmodus fuer die erzwungene Runde
# per think=False komplett abschalten, damit das ganze Budget in die
# sichtbare Antwort geht.
FORCED_TEXT_NUM_PREDICT = contextvars.ContextVar(
    'im_livechat_llm_bot_forced_text_num_predict', default=None,
)


class _LowTemperatureOllamaClient(ollama.Client):
    def chat(self, *args, **kwargs):
        options = dict(kwargs.get('options') or {})
        options.setdefault('temperature', DEFAULT_TEMPERATURE)
        num_predict = FORCED_TEXT_NUM_PREDICT.get()
        if num_predict:
            options.setdefault('num_predict', num_predict)
            kwargs.setdefault('think', False)
        kwargs['options'] = options
        return super().chat(*args, **kwargs)


class LLMProvider(models.Model):
    _inherit = 'llm.provider'

    def ollama_get_client(self):
        return _LowTemperatureOllamaClient(
            host=self.api_base or "http://localhost:11434", timeout=_OLLAMA_TIMEOUT,
        )

    def ollama_format_messages(self, messages, system_prompt=None, model=None):
        # Kompatibilitaets-Fix: llm_provider.format_messages() reicht seit dem
        # Multimodal-Support fuer OpenAI/Anthropic immer ein model= Kwarg durch,
        # aber llm_ollama.ollama_format_messages() (odoo-llm 18.0) akzeptiert es
        # nicht, was mit einem TypeError abbricht. Ollama braucht das model hier
        # nicht (keine multimodale Unterscheidung), daher wird es nur verschluckt.
        return super().ollama_format_messages(messages, system_prompt=system_prompt)
