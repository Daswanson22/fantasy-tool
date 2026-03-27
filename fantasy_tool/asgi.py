import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fantasy_tool.settings')

_django_app = get_asgi_application()


async def application(scope, receive, send):
    # When uvicorn terminates SSL directly, the ASGI scope may not reflect
    # the https scheme. Force it so request.scheme and OAuth redirect URIs
    # are built with https://.
    if scope.get('type') == 'http':
        scope = dict(scope, scheme='https')
    await _django_app(scope, receive, send)
