"""API 路由模組：每個檔案一個領域，提供 register(app, ctx)。

ctx 是 shared.RouteContext；路由一律透過 ctx 拿 Episode（require_ep）
與設定 helper，不直接 import api.py（避免 circular import）。
"""
