from . import models
from . import controllers


def _set_knowledge_retriever_description(env):
    # llm_tool_knowledge legt seinen "knowledge_retriever"-Tool-Datensatz mit
    # noupdate="1" an - eine XML-Datenueberschreibung aus diesem Modul wuerde
    # bei jedem -u-Upgrade stillschweigend ignoriert (noupdate schuetzt den
    # Datensatz vor allen spaeteren XML-Data-Anwendungen, nicht nur vor denen
    # des eigenen Moduls). Deshalb hier per post_init_hook direkt schreiben.
    tool = env.ref('llm_tool_knowledge.llm_tool_knowledge_retriever', raise_if_not_found=False)
    if not tool:
        return
    tool.sudo().write({
        'description': (
            "Durchsucht die Wissensbasis der Firmen-Website (Firmeninfo, "
            "Dienstleistungen, Kontakt, rechtliche Seiten) per semantischer "
            "Suche. Nutze dieses Tool fuer allgemeine Fragen zum Unternehmen, "
            "zu Dienstleistungen, zum Standort oder zu sonstigen Website-"
            "Inhalten. NICHT nutzen fuer Preisfragen (Preisseiten sind "
            "absichtlich nicht in der Wissensbasis enthalten - bei "
            "Preisfragen immer an einen Mitarbeiter verweisen) und NICHT "
            "fuer die Suche nach konkreten verkaeuflichen Produkten (dafuer: "
            "search_sellable_products)."
        ),
    })
