{
    'name': 'Privatemind',
    'version': '1.0',
    'category': 'Customizations',
    'summary': 'Modifikation des Berichtstemplats nach DIN 5008',
    'description': """
        Dieses Modul passt das Standard-Berichtstemplate nach DIN 5008 an.
    """,
    'author': 'Raffael Reichelt | PrivateMind',
    'website': 'https://privatemind.eu',
    'depends': ['base', 'l10n_din5008'],
    'data': [
        'data/report_layout.xml',
        'views/privatemind_cd_layout.xml',
        'views/external_layout_din5008_custom.xml',
    ],
    'installable': True,
    'application': False,
}