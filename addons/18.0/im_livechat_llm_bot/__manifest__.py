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
        - search_sellable_products: Produkte/Preise nachschlagen statt zu halluzinieren
        - create_lead_from_livechat: aus einem Gespraech einen CRM-Lead anlegen
    """,
    'author': 'Raffael Reichelt | PrivateMind',
    'website': 'https://privatemind.eu',
    'depends': ['im_livechat', 'llm_thread', 'llm_assistant', 'llm_ollama', 'llm_tool', 'crm', 'product'],
    'data': [
        'data/res_partner_data.xml',
        'data/res_users_data.xml',
        'data/utm_source_data.xml',
        'views/im_livechat_channel_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
