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
