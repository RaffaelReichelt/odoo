import logging

from odoo import tools

_logger = logging.getLogger(__name__)

try:
    import json

    from odoo.addons.llm_ollama.models.mail_message import MailMessage
    from odoo.addons.llm_ollama.utils.ollama_tool_call_id_utils import (
        OllamaToolCallIdUtils,
    )

    # Kompatibilitaets-Fix: ollama_format_message() (llm_ollama, odoo-llm 18.0)
    # prueft "if self.is_llm_user_message():" / "elif self.is_llm_assistant_message():"
    # OHNE das bei den drei anderen Providern (llm_openai, llm_anthropic, llm_letta)
    # konsequent verwendete "[self]"-Indexing (z.B. "if self.is_llm_user_message()[self]:").
    # is_llm_user_message() gibt fuer ein Recordset ein Dict {message: bool} zurueck -
    # bei self.ensure_one() also IMMER genau einen Eintrag, und ein nicht-leeres Dict
    # ist in Python immer wahr, unabhaengig vom enthaltenen bool-Wert. Der erste Zweig
    # ("user") wird dadurch fuer AUSNAHMSLOS JEDE Nachricht genommen - System-Prompts,
    # Assistant-Tool-Calls und Tool-Ergebnisse eingeschlossen.
    #
    # Live beobachtet (23.08.): eine Kundenanfrage ("Kanzlei mit 4 Steuerberatern und
    # 2 Assistenten") loeste 3 identische search_sellable_products-Aufrufe hintereinander
    # aus, obwohl jeder einzelne ein vollstaendiges, passendes Ergebnis lieferte (5
    # Produkte inkl. Preis/Nutzerzahl) - das Modell hat am Ende trotzdem behauptet,
    # keine Informationen erhalten zu haben. Grund: der "user"-Zweig liest nur
    # self.body (html2plaintext), NIE body_json - bei einer Tool-Nachricht steht dort
    # lediglich der Platzhaltertext "Executing search_sellable_products", nie das
    # eigentliche Tool-Ergebnis. Das Modell hat also buchstaeblich nie ein Tool-Ergebnis
    # gesehen, ungeachtet dessen, was tatsaechlich im Thread gespeichert war - daher
    # der Wiederholungs-Loop UND die (aus Modellsicht ehrliche) "keine Info"-Antwort.
    #
    # Fix: 1:1-Kopie der Originalmethode mit korrektem [self]-Indexing, wie es die
    # anderen drei Provider bereits richtig machen.
    def _coerce_tool_arguments(arguments):
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except (TypeError, ValueError):
                _logger.warning(
                    "Ollama Format: tool_call arguments not valid JSON, passing "
                    "through as-is: %r", arguments,
                )
        return arguments

    def _ollama_format_message(self):
        self.ensure_one()
        body = self.body
        if body:
            body = tools.html2plaintext(body)

        if self.is_llm_user_message()[self]:
            formatted_message = {"role": "user"}
            if body:
                formatted_message["content"] = body
            return formatted_message

        elif self.is_llm_assistant_message()[self]:
            formatted_message = {"role": "assistant"}
            content = tools.html2plaintext(self.body) if self.body else ""
            if content:
                formatted_message["content"] = content

            tool_calls = self.get_tool_calls()
            if tool_calls:
                formatted_message["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": tc["function"]["name"],
                            # Zweiter, bisher maskierter Bug (erst durch den [self]-Fix
                            # oben ueberhaupt erreichbar): body_json speichert
                            # "arguments" als JSON-STRING (z.B. '{"query": "..."}'),
                            # aber der installierte ollama-Python-Client validiert
                            # tool_calls[].function.arguments per Pydantic streng als
                            # dict - ein String wirft "Input should be a valid
                            # dictionary" und reisst die gesamte Antwort ab (live
                            # beobachtet: kompletter Chat-Turn brach mit roher
                            # Fehlermeldung im Chat-Fenster ab, sobald die erste
                            # Tool-Call-Nachricht in Runde 2 erneut formatiert wurde).
                            # Deshalb hier zurueck in ein dict parsen; faellt der
                            # String aus irgendeinem Grund nicht als JSON zu parsen,
                            # lieber den rohen String durchreichen als die ganze
                            # Antwort abstuerzen zu lassen.
                            "arguments": _coerce_tool_arguments(
                                tc["function"]["arguments"]
                            ),
                        },
                    }
                    for tc in tool_calls
                ]

            return formatted_message

        elif self.llm_role == "tool":
            tool_data = self.body_json
            if not tool_data:
                _logger.warning(
                    "Ollama Format: Skipping tool message %s: no tool data found.",
                    self.id,
                )
                return None

            tool_name = tool_data.get("tool_name")
            if not tool_name:
                tool_call_id = tool_data.get("tool_call_id")
                if tool_call_id:
                    tool_name = OllamaToolCallIdUtils.extract_tool_name_from_id(
                        tool_call_id
                    )

            if not tool_name:
                _logger.warning(
                    "Ollama Format: Skipping tool message %s: missing tool_name.",
                    self.id,
                )
                return None

            if "result" in tool_data:
                content = json.dumps(tool_data["result"])
            elif "error" in tool_data:
                content = json.dumps({"error": tool_data["error"]})
            else:
                content = ""

            return {"role": "tool", "name": tool_name, "content": content}
        else:
            return None

    MailMessage.ollama_format_message = _ollama_format_message
    _logger.info(
        "im_livechat_llm_bot: Kompatibilitaets-Patch fuer llm_ollama.MailMessage."
        "ollama_format_message aktiv (fehlendes [self]-Indexing bei "
        "is_llm_user_message()/is_llm_assistant_message() behoben - ohne diesen Fix "
        "wird JEDE Nachricht als 'user' formatiert und Tool-Ergebnisse erreichen das "
        "Modell nie)."
    )
except ImportError:
    # llm_ollama nicht installiert - nichts zu patchen.
    pass
