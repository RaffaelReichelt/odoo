import logging

_logger = logging.getLogger(__name__)

try:
    from pgvector import Vector

    from odoo.addons.llm_pgvector.fields import PgVector

    def _convert_to_column(self, value, record, values=None, validate=True):
        # Kompatibilitaets-Fix: llm_pgvector (odoo-llm 18.0) ruft
        # Vector._to_db(value, self.dimension) auf, aber die aktuelle pgvector-
        # PyPI-Version (0.5.x) akzeptiert Vector._to_db() nur noch mit EINEM
        # Argument (kein dimension-Parameter mehr). Das fuehrte zu einem
        # TypeError, der von der Originalmethode abgefangen und still zu NULL
        # degradiert wurde - jeder gespeicherte Chunk-Embedding-Vektor blieb
        # leer, ohne sichtbaren Fehler (Vektorsuche fand dadurch nie Treffer).
        if value is None:
            return None
        try:
            return Vector._to_db(value)
        except (ValueError, TypeError) as e:
            _logger.warning("Error converting vector: %s. Returning NULL.", e)
            return None

    PgVector.convert_to_column = _convert_to_column
    _logger.info(
        "im_livechat_llm_bot: Kompatibilitaets-Patch fuer llm_pgvector.PgVector "
        "(Vector._to_db Signatur) aktiv."
    )
except ImportError:
    # llm_pgvector nicht installiert - nichts zu patchen.
    pass
