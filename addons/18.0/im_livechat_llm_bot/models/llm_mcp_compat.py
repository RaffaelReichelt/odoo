import inspect
import logging
import sys
import types
from typing import Any, get_type_hints

_logger = logging.getLogger(__name__)

# Kompatibilitaets-Fix: llm_tool.get_input_schema() (odoo-llm, 18.0-Branch)
# importiert fest "from mcp.server.fastmcp.utilities.func_metadata import
# func_metadata", um aus einer Tool-Methodensignatur ein JSON-Schema fuer das
# LLM zu erzeugen. Die im venv installierte "mcp"-SDK-Version (2.0.0) hat
# mcp.server.fastmcp jedoch ersatzlos entfernt/umgebaut - der Import schlaegt
# seitdem mit "No module named 'mcp.server.fastmcp'" fehl.
#
# Folge (live in den Logs beobachtet, siehe odoo.addons.llm_ollama.models.
# ollama_provider: "Error formatting tool X: No module named
# 'mcp.server.fastmcp'"): get_input_schema() bricht fuer JEDES Tool ohne
# manuell gespeichertes input_schema-Feld ab. Dem Modell wird dann GAR KEIN
# echtes Parameter-Schema mehr mitgegeben - es muss Parameternamen wie
# collection_id blind erraten oder weglassen, statt sie aus einer klaren
# Beschreibung abzulesen. Benchmark vom 13.08.: dadurch fehlte collection_id
# in 33 von 10 Tool-Aufrufen bei qwen3.6:27b, 20 bei nemotron-3.5-lightning:
# 30b - kein Modell-Bug, sondern eine direkte Folge dieses fehlenden Imports.
#
# Statt die vendorte odoo-llm-Datei zu patchen oder die mcp-Paketversion im
# gesamten venv zu aendern (Risiko fuer andere mcp-Nutzer im Projekt), wird
# hier nur der fehlende Importpfad nachgebaut - mit derselben
# Pydantic-Modellerzeugung aus der Methodensignatur, die odoo-llm an anderer
# Stelle (llm_tool.get_pydantic_model_from_signature, fuer die
# Argument-Validierung) ohnehin schon verwendet. Ergebnis ist ein
# API-kompatibles Minimalobjekt (.arg_model mit .model_json_schema()),
# sodass der unveraenderte odoo-llm-Code danach wieder normal funktioniert -
# inklusive der collection_id-Beschreibungs-Anreicherung in
# llm_tool_knowledge_retriever.get_input_schema().
try:
    from pydantic import create_model

    class _FuncMetadata:
        def __init__(self, arg_model):
            self.arg_model = arg_model

    def _func_metadata(method):
        type_hints = get_type_hints(method)
        signature = inspect.signature(method)
        fields = {}
        for param_name, param in signature.parameters.items():
            if param_name == 'self':
                continue
            fields[param_name] = (
                type_hints.get(param_name, Any),
                param.default if param.default is not param.empty else ...,
            )
        arg_model = create_model('FuncMetadataArgModel', **fields)
        return _FuncMetadata(arg_model)

    _shim_module = types.ModuleType('mcp.server.fastmcp.utilities.func_metadata')
    _shim_module.func_metadata = _func_metadata

    sys.modules.setdefault('mcp.server.fastmcp', types.ModuleType('mcp.server.fastmcp'))
    sys.modules.setdefault(
        'mcp.server.fastmcp.utilities', types.ModuleType('mcp.server.fastmcp.utilities')
    )
    sys.modules['mcp.server.fastmcp.utilities.func_metadata'] = _shim_module

    _logger.info(
        "im_livechat_llm_bot: Kompatibilitaets-Shim fuer mcp.server.fastmcp.utilities."
        "func_metadata aktiv (Tool-Schemata werden wieder ans LLM uebergeben)."
    )
except ImportError:
    # pydantic nicht installiert - kann eigentlich nicht passieren (Odoo-
    # Kernabhaengigkeit), aber defensiv nichts patchen statt hart abstuerzen.
    _logger.warning(
        "im_livechat_llm_bot: mcp.server.fastmcp-Shim konnte nicht aktiviert werden "
        "(pydantic fehlt) - Tool-Schemata bleiben ggf. unvollstaendig."
    )
