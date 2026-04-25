import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-change-me-in-production')

DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')
    if host.strip()
]

_USING_DEFAULT_SECRET = SECRET_KEY == 'django-insecure-change-me-in-production'
if not DEBUG and _USING_DEFAULT_SECRET:
    raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set when DEBUG is False.')
if not DEBUG and ('*' in ALLOWED_HOSTS or not ALLOWED_HOSTS):
    raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS must be explicit when DEBUG is False.')


def _env_int(name, default, *, min_value=1, max_value=100000):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))

TRUST_PROXY_SSL_HEADER = os.environ.get('DJANGO_TRUST_PROXY_SSL_HEADER', 'False').lower() in ('true', '1', 'yes')
USE_FORWARDED_HOST = os.environ.get('DJANGO_USE_X_FORWARDED_HOST', 'False').lower() in ('true', '1', 'yes')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'social_django',
    'home',
    'accounts',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.http.ConditionalGetMiddleware',
]

ROOT_URLCONF = 'fantasy_tool.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'social_django.context_processors.backends',
                'social_django.context_processors.login_redirect',
            ],
        },
    },
]

WSGI_APPLICATION = 'fantasy_tool.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'accounts.backends.YahooFantasyOAuth2',
    'django.contrib.auth.backends.ModelBackend',
]

# Yahoo OAuth2 credentials (loaded from .env)
SOCIAL_AUTH_YAHOO_OAUTH2_KEY = os.environ.get('YAHOO_CLIENT_ID', '')
SOCIAL_AUTH_YAHOO_OAUTH2_SECRET = os.environ.get('YAHOO_CLIENT_SECRET', '')
SOCIAL_AUTH_YAHOO_OAUTH2_SCOPE = ['openid', 'fspt-r']

# Force HTTPS in the redirect URI sent to Yahoo
SOCIAL_AUTH_REDIRECT_IS_HTTPS = True

SOCIAL_AUTH_URL_NAMESPACE = 'social'

# Store access + refresh tokens so we can call the Yahoo Fantasy API later
SOCIAL_AUTH_YAHOO_OAUTH2_EXTRA_DATA = [
    ('access_token', 'access_token'),
    ('refresh_token', 'refresh_token'),
    ('token_type', 'token_type'),
    ('expires_in', 'expires_in'),
    ('xoauth_yahoo_guid', 'yahoo_guid'),
]

SOCIAL_AUTH_PIPELINE = (
    'social_core.pipeline.social_auth.social_details',
    'social_core.pipeline.social_auth.social_uid',
    'social_core.pipeline.social_auth.auth_allowed',
    'social_core.pipeline.social_auth.social_user',
    # For new Yahoo users: collect username/email/password before account creation
    'accounts.pipeline.require_registration',
    'social_core.pipeline.user.get_username',
    'social_core.pipeline.user.create_user',
    'social_core.pipeline.social_auth.associate_user',
    'social_core.pipeline.social_auth.load_extra_data',
    'social_core.pipeline.user.user_details',
    'accounts.pipeline.fix_username_from_email',
)

# Redirect URLs
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Security defaults. Local development keeps relaxed settings.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
X_FRAME_OPTIONS = 'DENY'

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    if TRUST_PROXY_SSL_HEADER:
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = USE_FORWARDED_HOST
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Home endpoint rate limits (requests, per window seconds).
HOME_RATE_LIMITS = {
    'select_league': (
        _env_int('HOME_RL_SELECT_LEAGUE_LIMIT', 20),
        _env_int('HOME_RL_SELECT_LEAGUE_WINDOW', 60, min_value=5, max_value=3600),
    ),
    'available_sp_api': (
        _env_int('HOME_RL_AVAILABLE_SP_LIMIT', 30),
        _env_int('HOME_RL_AVAILABLE_SP_WINDOW', 60, min_value=5, max_value=3600),
    ),
    'waiver_players_api': (
        _env_int('HOME_RL_WAIVER_PLAYERS_LIMIT', 90),
        _env_int('HOME_RL_WAIVER_PLAYERS_WINDOW', 60, min_value=5, max_value=3600),
    ),
    'toggle_keeper': (
        _env_int('HOME_RL_TOGGLE_KEEPER_LIMIT', 120),
        _env_int('HOME_RL_TOGGLE_KEEPER_WINDOW', 60, min_value=5, max_value=3600),
    ),
    'save_ai_config': (
        _env_int('HOME_RL_SAVE_AI_CONFIG_LIMIT', 60),
        _env_int('HOME_RL_SAVE_AI_CONFIG_WINDOW', 60, min_value=5, max_value=3600),
    ),
    'toggle_ai_manager': (
        _env_int('HOME_RL_TOGGLE_AI_MANAGER_LIMIT', 30),
        _env_int('HOME_RL_TOGGLE_AI_MANAGER_WINDOW', 60, min_value=5, max_value=3600),
    ),
    'ai_recommendation': (
        _env_int('HOME_RL_AI_REC_LIMIT', 5),
        _env_int('HOME_RL_AI_REC_WINDOW', 600, min_value=60, max_value=3600),
    ),
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
WHITENOISE_USE_FINDERS = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Stripe
STRIPE_SECRET_KEY      = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
STRIPE_WEBHOOK_SECRET  = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

# Stripe Price IDs — set these in .env once created in the Stripe dashboard
STRIPE_PRICE_PRO   = os.environ.get('STRIPE_PRICE_PRO', '')
STRIPE_PRICE_ELITE = os.environ.get('STRIPE_PRICE_ELITE', '')

# Email — auto-switch to SMTP when credentials are present, console otherwise
_email_host_user = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend' if _email_host_user
    else 'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST          = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT          = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS       = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_HOST_USER     = _email_host_user
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL  = os.environ.get('DEFAULT_FROM_EMAIL', 'thefantasylab@swantech.org')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'home': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'DEBUG' if DEBUG else 'INFO'),
            'propagate': False,
        },
    },
}
