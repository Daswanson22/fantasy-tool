import logging
from functools import lru_cache

import jwt
import requests
from django.conf import settings
from social_core.backends.yahoo import YahooOAuth2

logger = logging.getLogger(__name__)
YAHOO_OIDC_CONFIGURATION_URL = 'https://api.login.yahoo.com/.well-known/openid-configuration'


@lru_cache(maxsize=1)
def _get_yahoo_oidc_config():
    resp = requests.get(YAHOO_OIDC_CONFIGURATION_URL, timeout=5)
    resp.raise_for_status()
    return resp.json()


@lru_cache(maxsize=1)
def _get_yahoo_jwks_client():
    config = _get_yahoo_oidc_config()
    return jwt.PyJWKClient(config['jwks_uri'])


def _validate_id_token(id_token):
    config = _get_yahoo_oidc_config()
    issuer = config.get('issuer', 'https://api.login.yahoo.com')
    audience = settings.SOCIAL_AUTH_YAHOO_OAUTH2_KEY
    if not audience:
        raise ValueError('Missing SOCIAL_AUTH_YAHOO_OAUTH2_KEY.')

    # Retry once with a fresh JWKS cache to handle key rotation.
    for attempt in (1, 2):
        try:
            signing_key = _get_yahoo_jwks_client().get_signing_key_from_jwt(id_token)
            return jwt.decode(
                id_token,
                signing_key.key,
                algorithms=['RS256'],
                audience=audience,
                issuer=issuer,
                options={'require': ['sub', 'exp', 'iat']},
                leeway=5,
            )
        except Exception:
            if attempt == 1:
                _get_yahoo_jwks_client.cache_clear()
                continue
            raise


class YahooFantasyOAuth2(YahooOAuth2):
    """
    Custom Yahoo OAuth2 backend using the correct authorization URLs
    from Yahoo's OAuth2 documentation:
    https://developer.yahoo.com/oauth2/guide/flows_authcode/
    """
    name = 'yahoo-oauth2'

    # Yahoo's documented OAuth2 endpoints
    AUTHORIZATION_URL = 'https://api.login.yahoo.com/oauth2/request_auth'
    ACCESS_TOKEN_URL = 'https://api.login.yahoo.com/oauth2/get_token'
    REFRESH_TOKEN_URL = 'https://api.login.yahoo.com/oauth2/get_token'
    ACCESS_TOKEN_METHOD = 'POST'

    # Do NOT append state to the redirect_uri — Yahoo requires the redirect_uri
    # to match the registered value exactly, with no extra query parameters.
    # The state is sent as a separate parameter instead.
    REDIRECT_STATE = False

    def auth_params(self, state=None):
        params = super().auth_params(state)
        # Yahoo requires response_type=code for authorization code flow
        params['response_type'] = 'code'
        return params

    def user_data(self, access_token, *args, **kwargs):
        """
        Extract user data from a verified id_token in the token response.
        We do not trust unverified JWT payloads.
        """
        response = kwargs.get('response', {})
        id_token = response.get('id_token', '')

        if id_token:
            try:
                claims = _validate_id_token(id_token)
                # Ensure xoauth_yahoo_guid is available for EXTRA_DATA pipeline
                if 'xoauth_yahoo_guid' not in claims:
                    claims['xoauth_yahoo_guid'] = response.get('xoauth_yahoo_guid', '')
                return claims
            except Exception as exc:
                logger.warning('Yahoo id_token validation failed: %s', exc)

        # Fallback: build minimal user data from the token response fields
        guid = response.get('xoauth_yahoo_guid', '')
        return {'sub': guid, 'xoauth_yahoo_guid': guid}

    def get_user_id(self, details, response):
        return response.get('sub') or response.get('xoauth_yahoo_guid')

    def get_user_details(self, response):
        email = response.get('email', '')
        # Prefer email prefix as username; fall back to nickname then sub (hash)
        username = (
            email.split('@')[0] if email
            else response.get('nickname')
            or response.get('preferred_username')
            or response.get('sub', '')
        )
        return {
            'username': username,
            'email': email,
            'fullname': response.get('name', ''),
            'first_name': response.get('given_name', ''),
            'last_name': response.get('family_name', ''),
        }
