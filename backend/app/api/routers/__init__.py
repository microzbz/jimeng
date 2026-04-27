"""
API Routers Module
"""
from app.api.routers import tasks, accounts, proxies, domains, settings, websocket, content_generation, fast_reference

__all__ = [
    "tasks",
    "accounts",
    "proxies",
    "domains",
    "settings",
    "websocket",
    "content_generation",
    "fast_reference",
]
