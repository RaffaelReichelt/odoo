import logging

from odoo import fields, models, tools

_logger = logging.getLogger(__name__)

BOT_PARTNER_XMLID = 'im_livechat_llm_bot.partner_llm_bot'


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    llm_thread_id = fields.Many2one(
        'llm.thread',
        string='KI-Thread',
        copy=False,
        help="Der llm.thread, der den Gespraechsverlauf dieses Livechat-Kanals "
             "gegenueber der KI abbildet.",
    )

    def _message_post_after_hook(self, message, msg_vals):
        result = super()._message_post_after_hook(message, msg_vals)
        try:
            self._llm_bot_try_reply(message, msg_vals)
        except Exception:
            _logger.exception(
                "Livechat-KI-Bot: Fehler beim Verarbeiten der Nachricht in Kanal %s",
                self.id,
            )
        return result

    def _llm_bot_try_reply(self, message, msg_vals):
        self.ensure_one()

        if self.channel_type != 'livechat' or self.chatbot_current_step_id:
            return

        assistant = self.livechat_channel_id.sudo().llm_assistant_id
        if not assistant:
            return

        if msg_vals.get('message_type') != 'comment':
            return

        bot_partner = self.env.ref(BOT_PARTNER_XMLID, raise_if_not_found=False)
        if not bot_partner:
            return

        author_id = msg_vals.get('author_id')
        author_guest_id = msg_vals.get('author_guest_id')

        if author_id == bot_partner.id:
            return  # eigene Antwort des Bots: Endlosschleife vermeiden

        if not author_id and not author_guest_id:
            # Weder Partner noch Gast als Autor: keine echte Besucher-/Operator-Nachricht.
            return

        if author_id:
            # Anonyme Besucher posten ueber mail.guest (author_guest_id) und haben
            # kein author_id. Ist author_id gesetzt, kann es ein eingeloggter
            # interner Operator ODER ein angemeldeter Portal-/Kunden-Besucher sein -
            # nur beim internen Operator soll der Bot nicht antworten. partner_share
            # ist Odoos eigenes Feld dafuer ("hat einen internen, nicht-Portal-User").
            author_partner = self.env['res.partner'].sudo().browse(author_id)
            if not author_partner.partner_share:
                return

        body_text = tools.html2plaintext(msg_vals.get('body') or '').strip()
        if not body_text:
            return

        thread = self.sudo()._llm_bot_get_or_create_thread(assistant)

        for _event in thread.generate(user_message_body=body_text):
            pass

        reply = thread.message_ids.filtered(
            lambda m: m.llm_role == 'assistant' and not m.is_error,
        ).sorted('id')[-1:]
        if not reply:
            return

        self.sudo().message_post(
            body=reply.body,
            author_id=bot_partner.id,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

    def _llm_bot_get_or_create_thread(self, assistant):
        self.ensure_one()

        if self.llm_thread_id:
            return self.llm_thread_id

        thread = self.env['llm.thread'].create({
            'name': f"Livechat #{self.id}",
            'user_id': self.env.user.id,
            'provider_id': assistant.provider_id.id,
            'model_id': assistant.model_id.id,
            'model': self._name,
            'res_id': self.id,
        })
        thread.set_assistant(assistant.id)
        self.llm_thread_id = thread
        return thread
