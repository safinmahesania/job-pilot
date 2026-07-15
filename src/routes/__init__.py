"""Route modules.

api.py was a single 1300-line file holding the app, the middleware, the auth gate, and
every route. The routes are moving here one domain at a time — each an APIRouter that
api.py includes — so api.py can shrink to what only it can own: the app object, the
gate, startup, and the static mount that must stay last. deps.py already holds the
shared helpers these routers need, so a route module never has to reach back into api.py.
"""
