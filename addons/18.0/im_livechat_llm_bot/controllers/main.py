from odoo import http
from odoo.http import request

from odoo.addons.im_livechat.controllers.main import LivechatController


class LivechatControllerLLMBot(LivechatController):

    @http.route()
    def livechat_init(self, channel_id):
        result = super().livechat_init(channel_id)
        # Die Basismethode berechnet 'available_for_me' nur aus Online-Operatoren
        # bzw. einem regelbasierten Chatbot-Script und kennt unseren KI-Assistenten
        # nicht - ohne diesen Fix bleibt der Kanal fuer den Besucher "nicht
        # verfuegbar" und das Widget oeffnet sich nicht, obwohl der KI-Bot
        # antworten wuerde (siehe auch ImLivechatChannel.get_livechat_info()).
        if not result.get('available_for_me'):
            channel = request.env['im_livechat.channel'].sudo().browse(channel_id)
            if channel.llm_assistant_id:
                result['available_for_me'] = True
        return result
