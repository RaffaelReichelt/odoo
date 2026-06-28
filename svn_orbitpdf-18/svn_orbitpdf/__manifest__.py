# -*- coding: utf-8 -*-
# Copyright 2025 Sveltware Solutions

{
    'name': 'OrbitPDF Document Engine',
    'category': 'Extra Tools',
    'summary': 'The next-generation HTML-to-PDF engine, foundation for document rendering in Odoo, bringing next-generation PDF engines into real-world deployments without disrupting existing QWeb templates, built on PlutoPrint. PDF reports, pdf watermark, pdf print, export pdf, direct print, template report, accounting reports, financial reports, account financial reports, general ledger, cash book, day book, bank book financial reports, VAT reports, POS reports, POS print, POS receipt design, payslip report, print journal entries, attendance dashboard.',
    'version': '1.0.8',
    'license': 'Other OSI approved licence',
    'author': 'Sveltware Solutions, PlutoPrint',
    'website': 'https://www.linkedin.com/in/sveltware',
    'live_test_url': 'https://o18-omux.sveltware.com/web/login',
    'images': [
        'static/description/banner.png',
    ],
    'depends': [
        'web',
        'omux_shared_lib',
    ],
    'external_dependencies': {
        'python': ['plutoprint'],
    },
    'data': [
        'data/paperformat_data.xml',
        'views/report_templates.xml',
        'views/report_paperformat.xml',
        'views/ir_actions.xml',
    ],
    'assets': {
        'web.report_assets_common': [
            ('replace', 'web/static/fonts/fonts.scss', 'svn_orbitpdf/static/fonts.scss'),
        ],
    },
}
