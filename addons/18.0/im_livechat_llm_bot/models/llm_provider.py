from odoo import models


class LLMProvider(models.Model):
    _inherit = 'llm.provider'

    def ollama_format_messages(self, messages, system_prompt=None, model=None):
        # Kompatibilitaets-Fix: llm_provider.format_messages() reicht seit dem
        # Multimodal-Support fuer OpenAI/Anthropic immer ein model= Kwarg durch,
        # aber llm_ollama.ollama_format_messages() (odoo-llm 18.0) akzeptiert es
        # nicht, was mit einem TypeError abbricht. Ollama braucht das model hier
        # nicht (keine multimodale Unterscheidung), daher wird es nur verschluckt.
        return super().ollama_format_messages(messages, system_prompt=system_prompt)
