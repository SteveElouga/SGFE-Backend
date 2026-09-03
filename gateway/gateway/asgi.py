"""
ASGI config for gateway project.

Routing ASGI :
  - WebSocket /graphql  → strawberry.asgi.GraphQL  (subscriptions)
  - HTTP /graphql       → Django URL dispatcher    (AsyncGraphQLView)
  - Tout le reste       → Django URL dispatcher

Pourquoi ce split : AsyncGraphQLView (Django) ne gère pas les WebSockets ;
strawberry.asgi.GraphQL (Starlette) les gère nativement sans toucher au
contexte HTTP Django (cookies, sessions).
"""

import os
from typing import Any

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gateway.settings")
django.setup()

from django.core.asgi import get_asgi_application  # noqa: E402
from strawberry.asgi import GraphQL  # noqa: E402
from strawberry.subscriptions import (  # noqa: E402
    GRAPHQL_TRANSPORT_WS_PROTOCOL,
    GRAPHQL_WS_PROTOCOL,
)

from schema.schema import schema  # noqa: E402

_django_app = get_asgi_application()
_graphql_ws_app = GraphQL(
    schema=schema,
    subscription_protocols=[GRAPHQL_TRANSPORT_WS_PROTOCOL, GRAPHQL_WS_PROTOCOL],
)


async def application(scope: Any, receive: Any, send: Any) -> None:
    # `Any` : deux implémentations ASGI (asgiref/Django, starlette/strawberry)
    # se partagent cet appel avec des types de scope/receive/send structurellement
    # différents bien que compatibles à l'exécution (spec ASGI, duck-typing) —
    # aucune annotation précise ne satisferait les deux appelées ci-dessous.
    if scope["type"] == "websocket" and scope.get("path") == "/graphql":
        await _graphql_ws_app(scope, receive, send)
    else:
        await _django_app(scope, receive, send)
