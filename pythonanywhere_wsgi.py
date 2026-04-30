"""
PythonAnywhere WSGI configuration for fantasy-tool.

Paste this file's contents into the WSGI configuration file shown in your
PythonAnywhere web app dashboard (Web tab → WSGI configuration file link).
The file is usually at:
    /var/www/daswanson22_pythonanywhere_com_wsgi.py
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# ── Project path ───────────────────────────────────────────────────────────────
PROJECT_HOME = '/home/daswanson22/fantasy-tool'
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

# ── Load environment variables ─────────────────────────────────────────────────
load_dotenv(Path(PROJECT_HOME) / '.env')

# ── Django settings ────────────────────────────────────────────────────────────
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fantasy_tool.settings')

from django.core.wsgi import get_wsgi_application  # noqa: E402
application = get_wsgi_application()
