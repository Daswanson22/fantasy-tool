import base64
import json

from social_core.backends.yahoo import YahooOAuth2


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
        Extract user data from the id_token JWT in the token response instead
        of calling the userinfo endpoint, which returns 403 unless the Yahoo
        app has explicit profile/OpenID permissions enabled.
        """
        response = kwargs.get('response', {})
        id_token = response.get('id_token', '')

        if id_token:
            try:
                # JWT is three base64url segments: header.payload.signature
                payload_b64 = id_token.split('.')[1]
                # Restore padding stripped by base64url encoding
                payload_b64 += '=' * (4 - len(payload_b64) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload_b64))
                # Ensure xoauth_yahoo_guid is available for EXTRA_DATA pipeline
                if 'xoauth_yahoo_guid' not in claims:
                    claims['xoauth_yahoo_guid'] = response.get('xoauth_yahoo_guid', '')
                return claims
            except Exception:
                pass

        # Fallback: build minimal user data from the token response fields
        guid = response.get('xoauth_yahoo_guid', '')
        return {'sub': guid, 'xoauth_yahoo_guid': guid}

    def get_user_id(self, details, response):
        return response.get('sub') or response.get('xoauth_yahoo_guid')
