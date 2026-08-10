from odoo import _, fields, models

BOT_USER_XMLID = 'im_livechat_llm_bot.user_llm_bot'


class ImLivechatChannel(models.Model):
    _inherit = 'im_livechat.channel'

    llm_assistant_id = fields.Many2one(
        'llm.assistant',
        string='KI-Assistent',
        help="Wenn gesetzt, beantwortet dieser Assistent eingehende Besuchernachrichten "
             "automatisch, solange kein interner Operator im Gespraech geantwortet hat "
             "und kein Skript-Chatbot aktiv ist.",
    )

    def get_livechat_info(self, username=None):
        # Odoo haelt einen Kanal nur fuer "verfuegbar" (Widget zeigt ueberhaupt
        # eine Eingabemoeglichkeit), wenn ein menschlicher Operator online ist
        # oder ein regelbasiertes Chatbot-Script konfiguriert ist. Mit einem
        # KI-Assistenten soll der Kanal auch ohne Menschen 24/7 verfuegbar sein.
        # info['options'] (u.a. channel_id, den das Frontend fuer /im_livechat/init
        # braucht) wird von super() nur gesetzt, wenn 'available' zu diesem
        # Zeitpunkt schon True war - deshalb hier nachtraeglich selbst befuellen.
        info = super().get_livechat_info(username=username)
        if not info.get('available') and self.llm_assistant_id:
            info['available'] = True
            if 'options' not in info:
                info['options'] = self._get_channel_infos()
                info['options']['default_username'] = username or _('Visitor')
        return info

    def _get_operator(self, previous_operator_id=None, lang=None, country_id=None):
        # Ist ein Mensch online, hat er weiterhin Vorrang (Standardverhalten).
        # Ist niemand verfuegbar, aber ein KI-Assistent konfiguriert, tritt der
        # KI-Bot-User als Operator auf - sonst kann gar keine Session erstellt
        # werden (_get_livechat_discuss_channel_vals gibt sonst False zurueck).
        operator = super()._get_operator(
            previous_operator_id=previous_operator_id, lang=lang, country_id=country_id,
        )
        if operator or not self.llm_assistant_id:
            return operator
        return self.env.ref(BOT_USER_XMLID, raise_if_not_found=False)
