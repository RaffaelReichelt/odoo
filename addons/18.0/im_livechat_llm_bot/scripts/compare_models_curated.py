"""Kuratierter Schnelltest gegen die 13 bekannten Problemfaelle (17./18.08.):
die klassischen Preis-Mangling-Faelle PLUS die Grounding-Fragen, bei denen
verschiedene Modelle im vollen 46er-Katalog (siehe model_benchmark.py) real
schlechter abgeschnitten haben als der bisherige Champion mistral-small3.2:24b
(erfundene Hardware-Specs, ausgelassene Tool-Aufrufe, kapitulierte
Grounding-Antworten). Gedacht fuer schnelle Vergleiche neuer/kandidierender
Modelle (z.B. nach einem Fine-Tuning-Versuch), ohne jedes Mal den vollen
46-Fragen-Katalog laufen lassen zu muessen.

Nutzung (aus dem Odoo-Repo-Root):

    source <venv>/bin/activate
    ./community/odoo-bin shell -c local.conf --no-http \
        < addons/18.0/im_livechat_llm_bot/scripts/compare_models_curated.py

Vor dem Lauf unten in MODEL_NAMES die zu testenden llm.model-Namen eintragen
(muessen bereits unter einem Provider angelegt sein, z.B. "GX10 Ollama" oder
"Mistral AI Cloud" - PROVIDER_NAME entsprechend anpassen). Das Skript setzt
am Ende Provider/Modell des "Kundenservice Bot"-Assistenten zurueck.

Ergebnisse dieser Session (18.08., zur Einordnung neuer Laeufe):
- mistral-small3.2:24b (lokal, + Preis-/Sprach-Sicherheitsnetz): weiterhin
  der zuverlaessigste Gesamtkandidat.
- Cloud-Modelle (mistral-small-latest/medium-3.5/large-latest, gpt-4.1-mini):
  mangeln praktisch nie Zahlen, erfinden dafuer haeufiger unbelegte
  technische Details (z.B. "ASUS Ascent GX10", erfundene Service-SLAs).
- gpt-oss:20b, glm-4.7-flash, gemma3:27b (kein Tool-Support!),
  deepseek-r1:32b (gelegentlicher Sprachwechsel ins Chinesische, dafuer
  jetzt vom Sprach-Guard abgefangen): siehe gx10_lora_finetuning_plan-Memo
  bzw. Session-Historie fuer Details.
"""
import re
import sys
import time

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

MODEL_NAMES = [
    'mistral-small3.2:24b',
]
PROVIDER_NAME = 'GX10 Ollama'
LIVECHAT_CHANNEL_NAME = 'LLM Bot Test'

provider = env['llm.provider'].sudo().search([('name', '=', PROVIDER_NAME)], limit=1)
lc_channel = env['im_livechat.channel'].sudo().search([('name', '=', LIVECHAT_CHANNEL_NAME)], limit=1)
assistant = env['llm.assistant'].sudo().search([('name', '=', 'Kundenservice Bot')], limit=1)
operator_partner = env['res.users'].sudo().search([('login', '=', 'admin')], limit=1).partner_id
original_provider = assistant.provider_id
original_model = assistant.model_id

FRAGEN = [
    ('PREIS-1', 'Was steht auf eurer Preisgestaltungs-Seite? Nenne mir konkrete Details.'),
    ('PREIS-2', 'Was kostet das Starter-Paket ungefaehr?'),
    ('PREIS-3-AMBIG', 'Was kostet die Enterprise-Loesung?'),
    ('PREIS-3-EINDEUTIG', 'Was kostet die Enterprise Hardware-Appliance?'),
    ('ANGEBOT-1', 'Was bietet PrivateMind konkret an?'),
    ('PRODUKT-1', 'Ich interessiere mich fuer Ihr Produkt. Ich habe eine Kanzlei mit 8 Anwaelten. Welche Version koennen sie mir empfehlen?'),
    ('PRODUKT-3', 'Was ist der Unterschied zwischen den Paketen?'),
    ('TECHNIK-1', 'Welche Hardware wird genutzt?'),
    ('IMPLEMENTIERUNG-2', 'Muss ich selbst etwas installieren?'),
    ('VERGLEICH-1', 'Was ist der Unterschied zu ChatGPT Business?'),
    ('KENNTNISSE-1', 'Welche IT Kenntnisse/Faehigkeiten sind zur sinnvollen Nutzung notwendig?'),
    ('MUENCHEN', 'Habt ihr eine Filiale in Muenchen?'),
    ('GRUENDER', 'Wer hat PrivateMind gegruendet?'),
]

for model_name in MODEL_NAMES:
    model = env['llm.model'].sudo().search(
        [('provider_id', '=', provider.id), ('name', '=', model_name)], limit=1)
    if not model:
        print(f'\nUEBERSPRUNGEN: {model_name} nicht gefunden')
        continue
    assistant.provider_id = provider.id
    assistant.model_id = model.id
    env.cr.commit()
    print(f'\n{"=" * 70}\nMODELL: {model_name}\n{"=" * 70}')

    for tid, frage in FRAGEN:
        guest = env['mail.guest'].sudo().create({'name': f'Compare {model_name}'})
        channel = env['discuss.channel'].sudo().create({
            'name': f'Compare {model_name} - {tid}', 'channel_type': 'livechat',
            'livechat_channel_id': lc_channel.id, 'livechat_operator_id': operator_partner.id,
            'anonymous_name': 'Compare Besucher',
        })
        env.cr.commit()
        t0 = time.time()
        try:
            channel._llm_bot_try_reply(env['mail.message'], {
                'message_type': 'comment', 'author_id': False, 'author_guest_id': guest.id,
                'body': f'<p>{frage}</p>',
            })
        except Exception as e:
            print(f'\n  [{tid}] AUSNAHME: {e}')
            continue
        dt = time.time() - t0
        msg = env['mail.message'].sudo().search(
            [('model', '=', 'discuss.channel'), ('res_id', '=', channel.id)], order='id desc', limit=1)
        text = re.sub('<[^>]+>', ' ', msg.body or '').strip()
        print(f'\n  [{tid}] ({dt:.1f}s)')
        print(f'    Frage: {frage}')
        print(f'    Antwort: {text[:350]}')

assistant.provider_id = original_provider.id
assistant.model_id = original_model.id
env.cr.commit()
print('\n\nVERGLEICH FERTIG')
