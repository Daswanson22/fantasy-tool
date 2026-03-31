import time
import base64
import logging
import requests
from django.conf import settings

YAHOO_FANTASY_BASE = 'https://fantasysports.yahooapis.com/fantasy/v2'
YAHOO_TOKEN_URL = 'https://api.login.yahoo.com/oauth2/get_token'

logger = logging.getLogger(__name__)


class TokenExpiredError(Exception):
    pass


class YahooFantasyAPI:
    def __init__(self, access_token):
        self.access_token = access_token

    def _headers(self):
        return {'Authorization': f'Bearer {self.access_token}'}

    def get(self, path, params=None):
        params = dict(params or {})
        params['format'] = 'json'
        resp = requests.get(
            f'{YAHOO_FANTASY_BASE}{path}',
            headers=self._headers(),
            params=params,
            timeout=15,
        )
        if resp.status_code == 401:
            raise TokenExpiredError('Access token expired.')
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_mlb_leagues(self):
        """
        Return the user's MLB Fantasy leagues using the Games collection
        filtered to game_codes=mlb.
        Each entry: { league_key, league_name, num_teams, season,
                      draft_status, scoring_type, game_name }
        """
        data = self.get('/users;use_login=1/games;game_codes=mlb/leagues')
        return _parse_user_leagues(data)

    def get_mlb_teams(self):
        """
        Return the user's MLB Fantasy teams (used to resolve team_key
        from a league_key when loading a roster).
        Each entry: { team_key, team_name, league_key, game_name }
        """
        data = self.get('/users;use_login=1/games;game_codes=mlb/teams')
        return _parse_user_teams(data)

    def get_user_teams(self):
        """Return all teams across all sports (kept for compatibility)."""
        data = self.get('/users;use_login=1/games/teams')
        return _parse_user_teams(data)

    def get_team_roster(self, team_key, date=None):
        """Return a list of player dicts for the given team_key.

        For MLB, pass date='YYYY-MM-DD' to retrieve a specific date's roster.
        Defaults to today's roster when date is None.
        """
        if date:
            path = f'/team/{team_key}/roster;date={date}/players'
        else:
            path = f'/team/{team_key}/roster/players'
        data = self.get(path)
        return _parse_roster(data)

    def get_league(self, league_key):
        """Return basic league metadata."""
        data = self.get(f'/league/{league_key}')
        return _parse_league(data)


# ------------------------------------------------------------------
# Token refresh
# ------------------------------------------------------------------

def refresh_access_token(social_auth):
    """
    Use the stored refresh_token to get a new access_token and update
    the UserSocialAuth record in place. Returns the new access_token.
    """
    refresh_token = social_auth.extra_data.get('refresh_token', '')
    if not refresh_token:
        raise TokenExpiredError('No refresh token available.')

    client_id = settings.SOCIAL_AUTH_YAHOO_OAUTH2_KEY
    client_secret = settings.SOCIAL_AUTH_YAHOO_OAUTH2_SECRET
    credentials = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()

    resp = requests.post(
        YAHOO_TOKEN_URL,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        data={
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()

    # Persist new tokens
    social_auth.extra_data['access_token'] = token_data['access_token']
    if 'refresh_token' in token_data:
        social_auth.extra_data['refresh_token'] = token_data['refresh_token']
    social_auth.extra_data['expires_in'] = token_data.get('expires_in', 3600)
    social_auth.extra_data['auth_time'] = int(time.time())
    social_auth.save()

    return token_data['access_token']


def get_api_for_user(social_auth):
    """
    Return a YahooFantasyAPI instance for the given UserSocialAuth,
    refreshing the access token first if it has expired.
    """
    extra = social_auth.extra_data
    auth_time = extra.get('auth_time', 0)
    expires_in = extra.get('expires_in', 3600)
    token_age = int(time.time()) - auth_time

    if token_age >= expires_in - 60:          # Refresh 60 s before expiry
        access_token = refresh_access_token(social_auth)
    else:
        access_token = extra.get('access_token', '')

    return YahooFantasyAPI(access_token)


# ------------------------------------------------------------------
# JSON parsers (Yahoo's nested format is non-trivial)
# ------------------------------------------------------------------

def _get_list_value(obj):
    """Yahoo wraps array values as {'0': val, '1': val, 'count': N}.
    count may be an int or a string depending on the endpoint."""
    try:
        count = int(obj.get('count', 0))
    except (ValueError, TypeError):
        count = 0
    return [obj[str(i)] for i in range(count) if str(i) in obj]


def _parse_user_leagues(data):
    """
    Parse the response from /users;use_login=1/games;game_codes=mlb/leagues.
    Returns a list of league dicts.
    """
    leagues = []
    try:
        users_obj = data['fantasy_content']['users']
        for user_wrapper in _get_list_value(users_obj):
            user_arr = user_wrapper['user']
            games_obj = user_arr[1]['games']
            for game_wrapper in _get_list_value(games_obj):
                game_arr = game_wrapper['game']
                game_info = _flatten_array(game_arr[0])
                if len(game_arr) < 2:
                    continue
                leagues_obj = game_arr[1].get('leagues', {})
                for league_wrapper in _get_list_value(leagues_obj):
                    league_data = league_wrapper['league']
                    # League data is a list of single-key dicts
                    info = _flatten_array(league_data)
                    leagues.append({
                        'league_key': info.get('league_key', ''),
                        'league_name': info.get('name', 'Unknown League'),
                        'num_teams': info.get('num_teams', '?'),
                        'season': info.get('season', ''),
                        'draft_status': info.get('draft_status', ''),
                        'scoring_type': info.get('scoring_type', ''),
                        'game_name': game_info.get('name', 'Baseball'),
                        'url': info.get('url', ''),
                    })
    except (KeyError, IndexError, TypeError):
        pass
    return leagues


def _parse_user_teams(data):
    teams = []
    try:
        users_obj = data['fantasy_content']['users']
        users = _get_list_value(users_obj)
        for user_wrapper in users:
            user_arr = user_wrapper['user']
            # user_arr[1] holds games
            games_obj = user_arr[1]['games']
            games = _get_list_value(games_obj)
            for game_wrapper in games:
                game_arr = game_wrapper['game']
                game_info = _flatten_array(game_arr[0])
                game_name = game_info.get('name', '')
                # game_arr[1] holds teams
                if len(game_arr) < 2:
                    continue
                teams_obj = game_arr[1].get('teams', {})
                for team_wrapper in _get_list_value(teams_obj):
                    team_arr = team_wrapper['team']
                    team_info = _flatten_array(team_arr[0])
                    teams.append({
                        'team_key': team_info.get('team_key', ''),
                        'team_name': team_info.get('name', 'Unknown Team'),
                        'league_key': team_info.get('team_key', '').rsplit('.t.', 1)[0],
                        'game_name': game_name,
                        'waiver_priority': team_info.get('waiver_priority', ''),
                        'number_of_moves': team_info.get('number_of_moves', 0),
                        'number_of_trades': team_info.get('number_of_trades', 0),
                    })
    except (KeyError, IndexError, TypeError):
        pass
    return teams


def _parse_roster(data):
    """
    Parse /team/{key}/roster/players JSON response.

    Yahoo's JSON format uses two possible encodings for arrays:
      • A plain Python list  → access by integer index
      • A count-keyed dict   → {'0': ..., '1': ..., 'count': N}  → access by string key

    We handle both at every level so parsing is resilient to either format.
    """
    players = []

    try:
        team_arr = data['fantasy_content']['team']
    except (KeyError, TypeError) as e:
        logger.warning('_parse_roster: missing fantasy_content.team – %s', e)
        return players

    # Unwrap the second element, which holds {"roster": {...}}
    roster_container = _arr_get(team_arr, 1)
    if not isinstance(roster_container, dict):
        logger.warning('_parse_roster: roster_container is %s, expected dict', type(roster_container))
        return players

    roster_obj = roster_container.get('roster', {})
    if not isinstance(roster_obj, dict):
        logger.warning('_parse_roster: roster_obj is %s, expected dict', type(roster_obj))
        return players

    # Yahoo nests sub-resources in count-keyed format:
    #   roster: { "coverage_type":..., "0": {"players": {...}}, "count": 1 }
    # OR directly:
    #   roster: { "coverage_type":..., "players": {...} }
    players_obj = roster_obj.get('players')
    if not players_obj:
        # Try unwrapping from count-keyed sub-resource at '0'
        inner = roster_obj.get('0', {})
        if isinstance(inner, dict):
            players_obj = inner.get('players', {})
    if not players_obj:
        logger.warning('_parse_roster: no players in roster_obj. Keys: %s', list(roster_obj.keys()))
        return players

    for player_wrapper in _get_list_value(players_obj):
        try:
            player_arr = player_wrapper['player']

            # First element: list of flat info dicts
            info_raw = _arr_get(player_arr, 0)
            player_info = _flatten_array(info_raw) if isinstance(info_raw, list) else (info_raw if isinstance(info_raw, dict) else {})

            # Second element: dict with selected_position / starting_status
            extra = _arr_get(player_arr, 1)
            if not isinstance(extra, dict):
                extra = {}

            # Name
            name_data = player_info.get('name', {})
            name = name_data.get('full', '') if isinstance(name_data, dict) else str(name_data)

            # Selected position
            selected_pos = _extract_position(extra.get('selected_position', []))

            # Starting status — Yahoo only includes this once MLB lineups are
            # officially submitted (~1-3 hrs before first pitch). Absent = None.
            is_starting = None
            ss = extra.get('starting_status') or player_info.get('starting_status')
            if ss:
                ss_flat = _flatten_array(ss) if isinstance(ss, list) else ss
                val = ss_flat.get('is_starting') if isinstance(ss_flat, dict) else None
                if val is not None:
                    is_starting = bool(int(val))

            # Eligible positions → comma-separated string
            eligible = _parse_eligible_positions(player_info.get('eligible_positions', {}))

            status = player_info.get('status', '')
            on_il = str(player_info.get('on_disabled_list', '0')) == '1'
            position_type = player_info.get('position_type', 'B')

            players.append({
                'name': name,
                'position': player_info.get('display_position', ''),
                'selected_position': selected_pos,
                'eligible_positions': eligible,
                'mlb_team': player_info.get('editorial_team_abbr', ''),
                'mlb_team_full': player_info.get('editorial_team_full_name', ''),
                'status': status if status else 'Active',
                'on_il': on_il,
                'is_starting': is_starting,
                'image_url': player_info.get('image_url', ''),
                'player_key': player_info.get('player_key', ''),
                'uniform_number': player_info.get('uniform_number', ''),
                'position_type': position_type,
                'trending': None,  # placeholder — populated in a future feature
            })
        except (KeyError, IndexError, TypeError) as e:
            logger.debug('_parse_roster: skipping player due to %s', e)
            continue

    return players


def _arr_get(arr, idx):
    """Get element at idx from either a Python list or a Yahoo count-keyed dict."""
    if isinstance(arr, list):
        return arr[idx] if idx < len(arr) else {}
    if isinstance(arr, dict):
        return arr.get(str(idx), {})
    return {}


def _extract_position(pos_data):
    """Extract position string from Yahoo's selected_position sub-array."""
    if isinstance(pos_data, list):
        flat = _flatten_array(pos_data)
        return flat.get('position', '') if isinstance(flat, dict) else ''
    if isinstance(pos_data, dict):
        return pos_data.get('position', '')
    return ''


def _parse_eligible_positions(elig):
    """Return comma-separated eligible positions from Yahoo's eligible_positions value."""
    if isinstance(elig, list):
        # Format: [{"position": "C"}, {"position": "Util"}]
        return ', '.join(
            str(item.get('position', ''))
            for item in elig if isinstance(item, dict) and item.get('position')
        )
    if isinstance(elig, dict):
        pos_val = elig.get('position', [])
        if isinstance(pos_val, list):
            return ', '.join(str(p) for p in pos_val if p)
        return str(pos_val) if pos_val else ''
    return ''


def _parse_league(data):
    try:
        league_arr = data['fantasy_content']['league']
        return _flatten_array(league_arr)
    except (KeyError, IndexError, TypeError):
        return {}


def _flatten_array(arr):
    """Merge a list of single-key dicts into one flat dict."""
    result = {}
    for item in arr:
        if isinstance(item, dict):
            result.update(item)
    return result
