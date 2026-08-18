{
    'name': 'Livechat KI-Bot',
    'version': '18.0.1.0.0',
    'category': 'Website/Live Chat',
    'summary': 'Beantwortet Website-Livechat-Anfragen automatisch ueber eine lokale KI (odoo-llm / Ollama)',
    'description': """
        Bindet einen KI-Assistenten (odoo-llm, z.B. ueber einen lokalen Ollama-Server) an den
        Website-Livechat an. Solange kein interner Operator geantwortet hat und kein
        regelbasierter Chatbot-Script aktiv ist, beantwortet der konfigurierte llm.assistant
        eingehende Besuchernachrichten automatisch.

        Zusaetzlich stellt das Modul dem Assistenten zwei Vertriebs-Tools zur Verfuegung
        (unter KI > Assistenten > Tools dem gewuenschten Assistenten zuweisen):
        - search_sellable_products: passende Produkte nachschlagen (bewusst ohne Preis, Preisfragen werden immer an einen Mitarbeiter eskaliert)
        - create_lead_from_livechat: aus einem Gespraech einen CRM-Lead anlegen

        Macht ausserdem veroeffentlichte website.page-Seiten als llm_knowledge-Ressourcen
        abrufbar, damit der Assistent per RAG (llm_tool_knowledge) echte Website-Inhalte
        statt Vermutungen nutzen kann.
    """,
    'author': 'Raffael Reichelt | PrivateMind',
    'website': 'https://privatemind.eu',
    'depends': [
        'im_livechat', 'llm_thread', 'llm_assistant', 'llm_ollama', 'llm_tool',
        'crm', 'product', 'website', 'llm_knowledge', 'llm_pgvector', 'llm_tool_knowledge',
    ],
    'data': [
        'data/res_partner_data.xml',
        'data/res_users_data.xml',
        'data/utm_source_data.xml',
        'views/im_livechat_channel_views.xml',
    ],
    'external_dependencies': {
        'python': ['bs4'],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
    'post_init_hook': '_set_knowledge_retriever_description',
}
