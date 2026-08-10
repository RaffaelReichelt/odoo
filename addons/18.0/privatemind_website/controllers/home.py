from odoo import http
from odoo.addons.website.controllers.main import Website


class PrivateMindHome(Website):

    def _login_redirect(self, uid, redirect=None):
        return '/odoo'

    @http.route(website=True, auth='public', sitemap=False)
    def web_login(self, *args, **kw):
        return super().web_login(*args, **kw)
