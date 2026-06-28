# -*- coding: utf-8 -*-
# from odoo import http


# class Privatemind(http.Controller):
#     @http.route('/privatemind/privatemind', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/privatemind/privatemind/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('privatemind.listing', {
#             'root': '/privatemind/privatemind',
#             'objects': http.request.env['privatemind.privatemind'].search([]),
#         })

#     @http.route('/privatemind/privatemind/objects/<model("privatemind.privatemind"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('privatemind.object', {
#             'object': obj
#         })

