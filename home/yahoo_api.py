import time
import base64
import logging
import xml.etree.ElementTree as ET
import requests
from django.conf import settings

YAHOO_FANTASY_BASE = 'https://fantasysports.yahooapis.com/fantasy/v2'
YAHOO_TOKEN_URL = 'https://api.login.yahoo.com/oauth2/get_token'

logger = logging.getLogger(__name__)


class TokenExpiredError(Exception):
    pass


class YahooAPIError(Exception):
    """Raised when Yahoo returns an application-level error (4xx/5xx with XML body)."""
    def __init__(self, message, description=None):
        super().__init__(message)
        self.description = description  # human-readable detail from Yahoo's XML

    def __str__(self):
        if self.description:
            return f'{super().__str__()} — {self.description}'
        return super().__str__()


class YahooFantasyAPI:
    def __init__(self, access_token):
        self.access_token = access_token

    def _headers(self):
        return {'Authorization': f'Bearer {self.access_token}'}

    def _xml_headers(self):
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/xml',
        }

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

    def post(self, path, xml_body):
        """POST XML to the Yahoo Fantasy API. Returns raw response text."""
        resp = requests.post(
            f'{YAHOO_FANTASY_BASE}{path}',
            headers=self._xml_headers(),
            data=xml_body.encode('utf-8'),
            timeout=15,
        )
        return self._handle_write_response(resp)

    def put(self, path, xml_body):
        """PUT XML to the Yahoo Fantasy API. Returns raw response text."""
        resp = requests.put(
            f'{YAHOO_FANTASY_BASE}{path}',
            headers=self._xml_headers(),
            data=xml_body.encode('utf-8'),
            timeout=15,
        )
        return self._handle_write_response(resp)

    def _handle_write_response(self, resp):
        """Validate a write response; raise TokenExpiredError or YahooAPIError on failure."""
        if resp.status_code in (401, 403):
            description = _parse_yahoo_error_xml(resp.text)
            logger.error(
                'Yahoo write API %s — body: %s', resp.status_code, resp.text[:500]
            )
            msg = description or f'Yahoo returned {resp.status_code} — token expired or app lacks write permission.'
            raise TokenExpiredError(msg)
        if not resp.ok:
            description = _parse_yahoo_error_xml(resp.text)
            logger.error(
                'Yahoo write API %s — body: %s', resp.status_code, resp.text[:500]
            )
            raise YahooAPIError(
                f'Yahoo API error {resp.status_code}',
                description=description or resp.text[:300],
            )
        return resp.text

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

    def get_team_player_stats(self, team_key, stat_type='season'):
        """Return a dict mapping player_key → total fantasy points.

        Yahoo pre-computes fantasy points using the league's scoring settings and
        returns them in the player_points.total field.
        """
        data = self.get(f'/team/{team_key}/players/stats;type={stat_type}')
        return _parse_player_stats(data)


    def get_league(self, league_key):
        """Return basic league metadata."""
        data = self.get(f'/league/{league_key}')
        return _parse_league(data)

    def get_league_settings(self, league_key):
        """Return parsed league settings dict for caching into LeagueSettings model."""
        data = self.get(f'/league/{league_key}/settings')
        return _parse_league_settings(data)

    def get_league_available_players(self, league_key, count=100, start=0, position=None):
        """Return free agents sorted by season pts with full player info and points.

        Each entry mirrors the roster player dict shape so the same templates work.
        position: optional Yahoo position filter (e.g. 'SP', 'RP', 'OF', 'P')
        """
        pos_filter = f';position={position}' if position else ''
        path = (
            f'/league/{league_key}/players;status=A{pos_filter};sort=PTS;sort_type=season'
            f';start={start};count={count}/stats;type=season'
        )
        data = self.get(path)
        return _parse_league_players(data)

    def get_player_starting_statuses(self, league_key, player_keys, date=None):
        """Return {player_key: bool} indicating whether each player is starting.

        Uses /league/{key}/players;player_keys=.../starting_status which Yahoo
        populates once MLB lineups are officially submitted (~1-3 hrs before first pitch).
        Pass date='YYYY-MM-DD' to check a specific date; defaults to today.
        """
        if not player_keys:
            return {}
        keys_str = ','.join(player_keys)
        date_filter = f';date={date}' if date else ''
        path = f'/league/{league_key}/players;player_keys={keys_str}{date_filter}/starting_status'
        logger.debug('get_player_starting_statuses url: %s%s', YAHOO_FANTASY_BASE, path)
        data = self.get(path)
        result = _parse_player_starting_statuses(data)
        logger.debug('get_player_starting_statuses date=%s result=%s', date, result)
        return result

    def get_available_starting_pitchers_by_date(self, league_key, dates):
        """Return available pitchers grouped by date.

        Yahoo's starting_status API only returns confirmed lineup data for the
        current day — the date filter on the league/players endpoint is silently
        ignored for future dates (the response always embeds today's date).

        Strategy:
          • today   → fetch available pitchers, filter to confirmed starters via
                      starting_status (accurate once lineups are posted).
          • future  → return all available pitchers unfiltered so the user can
                      see the pool; 'confirmed' flag is False for these days.

        Returns: {date_str: {'players': [...], 'confirmed': bool}}
        """
        pitchers = self.get_league_available_players(league_key, count=50, position='P')
        if not pitchers:
            return {d: {'players': [], 'confirmed': False} for d in dates}

        player_keys = [p['player_key'] for p in pitchers if p.get('player_key')]
        pitcher_map = {p['player_key']: p for p in pitchers}

        today_str = dates[0] if dates else None
        today_starters = []
        if today_str:
            try:
                starting_map = self.get_player_starting_statuses(league_key, player_keys)
                today_starters = [
                    pitcher_map[k] for k in player_keys if starting_map.get(k) is True
                ]
            except Exception as e:
                logger.warning('get_available_starting_pitchers_by_date today error: %s', e)

        result = {}
        for i, date_str in enumerate(dates):
            if i == 0:
                result[date_str] = {'players': today_starters, 'confirmed': True}
            else:
                # Return all available pitchers for future days — starting status
                # cannot be determined until lineups are posted on that day.
                result[date_str] = {'players': list(pitchers), 'confirmed': False}

        return result

    def get_league_standings(self, league_key):
        """Return all teams' season standings for the league.

        Each entry: {team_key, team_name, rank, points_for, wins, losses, ties}
        """
        data = self.get(f'/league/{league_key}/standings')
        return _parse_league_standings(data)

    def get_league_scoreboard(self, league_key, week=None):
        """Return current week points for each team in the league.

        Returns {team_key: week_points (float)}.
        Optionally pass week (int) to fetch a specific week's scoreboard.
        """
        week_filter = f';week={week}' if week else ''
        data = self.get(f'/league/{league_key}/scoreboard{week_filter}')
        return _parse_league_scoreboard(data)

    def get_league_player_stats(self, league_key, player_keys, stat_type):
        """Return {player_key: total_points} for a specific set of player_keys.

        Used to fetch lastweek/lastmonth totals for trend computation without
        re-fetching the full free-agent list.
        """
        keys_str = ','.join(player_keys)
        path = f'/league/{league_key}/players;player_keys={keys_str}/stats;type={stat_type}'
        data = self.get(path)
        return _parse_league_player_stats(data)

    def get_player_ownership(self, league_key, player_keys, debug=False):
        """Return ownership data for the given player keys within a league.

        Yahoo endpoint: /league/{key}/players;player_keys=.../ownership

        Returns: {player_key: {'percent_owned': float|None, 'percent_owned_change': float|None}}
        Yahoo's percent_owned_change is the 7-day delta (positive = trending up).
        Batches automatically for Yahoo's ~25-key-per-request limit.
        Pass debug=True to log the raw Yahoo response for schema inspection.
        """
        if not player_keys:
            return {}

        result = {}
        batch_size = 25
        for i in range(0, len(player_keys), batch_size):
            batch = player_keys[i:i + batch_size]
            keys_str = ','.join(batch)
            path = f'/league/{league_key}/players;player_keys={keys_str}/ownership'
            try:
                data = self.get(path)
                if debug:
                    logger.warning('get_player_ownership RAW batch %d: %s', i // batch_size, data)
                result.update(_parse_player_ownership(data))
            except Exception as e:
                logger.warning('get_player_ownership batch %d error: %s', i // batch_size, e)

        return result

    # ------------------------------------------------------------------
    # Write operations (POST / PUT)
    # ------------------------------------------------------------------

    def add_drop_player(self, league_key, team_key, add_player_key, drop_player_key,
                        faab_bid=None):
        """Execute a combined add/drop transaction atomically.

        Drops drop_player_key and adds add_player_key in a single API call.
        For FAAB waiver leagues pass faab_bid (int) to include a bid amount.

        Raises:
            TokenExpiredError: OAuth token needs refresh.
            YahooAPIError: Yahoo rejected the transaction (e.g. player on IL,
                           waiver wire, roster full, budget exceeded).
        """
        faab_element = f'    <faab_bid>{int(faab_bid)}</faab_bid>\n' if faab_bid is not None else ''
        xml_body = (
            "<?xml version='1.0'?>\n"
            "<fantasy_content>\n"
            "  <transaction>\n"
            "    <type>add/drop</type>\n"
            f"{faab_element}"
            "    <players>\n"
            "      <player>\n"
            f"        <player_key>{add_player_key}</player_key>\n"
            "        <transaction_data>\n"
            "          <type>add</type>\n"
            f"          <destination_team_key>{team_key}</destination_team_key>\n"
            "        </transaction_data>\n"
            "      </player>\n"
            "      <player>\n"
            f"        <player_key>{drop_player_key}</player_key>\n"
            "        <transaction_data>\n"
            "          <type>drop</type>\n"
            f"          <source_team_key>{team_key}</source_team_key>\n"
            "        </transaction_data>\n"
            "      </player>\n"
            "    </players>\n"
            "  </transaction>\n"
            "</fantasy_content>"
        )
        logger.info(
            'add_drop_player league=%s team=%s add=%s drop=%s faab=%s',
            league_key, team_key, add_player_key, drop_player_key, faab_bid,
        )
        return self.post(f'/league/{league_key}/transactions', xml_body)

    def set_roster_position(self, team_key, player_key, position, date=None):
        """Move a player to a new roster slot (e.g. BN → SP, SP → BN).

        Args:
            team_key:   Team key string.
            player_key: Player key string.
            position:   Target position ('SP', 'RP', 'P', 'BN', 'OF', etc.)
            date:       'YYYY-MM-DD' to act on a specific date; defaults to today.

        Raises:
            TokenExpiredError: OAuth token needs refresh.
            YahooAPIError: Yahoo rejected the roster change.
        """
        from datetime import date as date_cls
        date_str = date or date_cls.today().isoformat()
        xml_body = (
            "<?xml version='1.0'?>\n"
            "<fantasy_content>\n"
            "  <roster>\n"
            "    <coverage_type>date</coverage_type>\n"
            f"    <date>{date_str}</date>\n"
            "    <players>\n"
            "      <player>\n"
            f"        <player_key>{player_key}</player_key>\n"
            f"        <position>{position}</position>\n"
            "      </player>\n"
            "    </players>\n"
            "  </roster>\n"
            "</fantasy_content>"
        )
        logger.info(
            'set_roster_position team=%s player=%s position=%s date=%s',
            team_key, player_key, position, date_str,
        )
        return self.put(f'/team/{team_key}/roster', xml_body)


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

            # Name
            name_data = player_info.get('name', {})
            name = name_data.get('full', '') if isinstance(name_data, dict) else str(name_data)

            # Search all tail elements of player_arr for selected_position /
            # starting_status. Yahoo sometimes puts them in a single dict at
            # index 1 and sometimes as separate dicts at indices 1, 2, etc.
            selected_pos = ''
            is_starting = None
            arr_len = (len(player_arr) if isinstance(player_arr, list)
                       else int(player_arr.get('count', 0)) + 1)
            for idx in range(1, max(arr_len, 5)):
                elem = _arr_get(player_arr, idx)
                if not isinstance(elem, dict):
                    continue
                if not selected_pos and 'selected_position' in elem:
                    selected_pos = _extract_position(elem['selected_position'])
                if is_starting is None and 'starting_status' in elem:
                    is_starting = _extract_is_starting(elem['starting_status'])

            # Fallback: starting_status may be flattened into player_info (index 0)
            if is_starting is None:
                ss = player_info.get('starting_status')
                if ss:
                    is_starting = _extract_is_starting(ss)

            logger.debug(
                '_parse_roster player=%s selected_pos=%r is_starting=%r arr_len=%s',
                name, selected_pos, is_starting, arr_len,
            )

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
                'bats':         player_info.get('bats', ''),
                'throws':       player_info.get('throws', ''),
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


def _extract_is_starting(ss):
    """
    Extract is_starting bool from Yahoo's starting_status value.

    Yahoo returns starting_status in three possible shapes:
      • list of single-key dicts: [{"coverage_type":"date"}, ..., {"is_starting":"1"}]
      • count-keyed dict:         {"0":{"coverage_type":"date"}, ..., "count": N}
      • plain dict:               {"coverage_type":"date", "is_starting":"1"}
    All three must be normalised to a flat dict before reading is_starting.
    """
    if isinstance(ss, list):
        flat = _flatten_array(ss)
    elif isinstance(ss, dict):
        # count-keyed: keys are "0", "1", ..., "count"
        if '0' in ss or 'count' in ss:
            flat = _flatten_array(_get_list_value(ss))
        else:
            flat = ss
    else:
        return None

    val = flat.get('is_starting') if isinstance(flat, dict) else None
    if val is None:
        return None
    try:
        return bool(int(val))
    except (ValueError, TypeError):
        return None


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


def _parse_player_stats(data, field='total'):
    """
    Parse /team/{key}/players/stats response.
    Returns dict: { player_key: value (float) }

    field='total'   → player_points.total (season points or lastweek points)
    field='average' → player_points.average (per-game season average)
    """
    points_map = {}
    try:
        team_arr = data['fantasy_content']['team']
        players_container = _arr_get(team_arr, 1)
        if not isinstance(players_container, dict):
            return points_map

        players_obj = players_container.get('players')
        if not players_obj:
            inner = players_container.get('0', {})
            if isinstance(inner, dict):
                players_obj = inner.get('players', {})
        if not players_obj:
            logger.warning('_parse_player_stats: no players in response. keys=%s',
                           list(players_container.keys()))
            return points_map
    except (KeyError, TypeError) as e:
        logger.warning('_parse_player_stats: failed to reach players – %s', e)
        return points_map

    for player_wrapper in _get_list_value(players_obj):
        try:
            player_arr = player_wrapper['player']
            info_raw = _arr_get(player_arr, 0)
            player_info = _flatten_array(info_raw) if isinstance(info_raw, list) else (info_raw if isinstance(info_raw, dict) else {})
            player_key = player_info.get('player_key', '')
            if not player_key:
                continue

            extra = _arr_get(player_arr, 1)
            if not isinstance(extra, dict):
                extra = {}

            # player_points may be a dict or a list of single-key dicts
            pp = extra.get('player_points', {})
            if isinstance(pp, list):
                pp = _flatten_array(pp)
            value = pp.get(field) if isinstance(pp, dict) else None

            if value is not None:
                try:
                    points_map[player_key] = float(value)
                except (ValueError, TypeError):
                    pass
        except (KeyError, IndexError, TypeError):
            continue

    return points_map


def _parse_league_players(data):
    """
    Parse /league/{key}/players/.../stats response.

    Returns a list of player dicts matching the roster player shape so the
    same templates work for both roster and available players.
    """
    players = []
    try:
        league_arr = data['fantasy_content']['league']
        players_container = _arr_get(league_arr, 1)
        if not isinstance(players_container, dict):
            return players
        players_obj = players_container.get('players', {})
        if not players_obj:
            return players
    except (KeyError, TypeError):
        return players

    for player_wrapper in _get_list_value(players_obj):
        try:
            player_arr = player_wrapper['player']

            info_raw = _arr_get(player_arr, 0)
            player_info = _flatten_array(info_raw) if isinstance(info_raw, list) else (info_raw if isinstance(info_raw, dict) else {})

            name_data = player_info.get('name', {})
            name = name_data.get('full', '') if isinstance(name_data, dict) else str(name_data)

            eligible = _parse_eligible_positions(player_info.get('eligible_positions', {}))

            # Yahoo places player_points at index 1 normally, but inserts player_stats
            # at index 1 when a position filter is applied, pushing player_points to index 2+.
            # Search all indices to be resilient to either layout.
            pp = {}
            for idx in range(1, 5):
                elem = _arr_get(player_arr, idx)
                if isinstance(elem, dict) and 'player_points' in elem:
                    pp_raw = elem['player_points']
                    pp = _flatten_array(pp_raw) if isinstance(pp_raw, list) else pp_raw
                    break

            total = None
            if isinstance(pp, dict):
                try:
                    total = float(pp.get('total') or 0)
                except (ValueError, TypeError):
                    pass

            status = player_info.get('status', '')

            players.append({
                'name': name,
                'player_key': player_info.get('player_key', ''),
                'uniform_number': player_info.get('uniform_number', ''),
                'position': player_info.get('display_position', ''),
                'eligible_positions': eligible,
                'mlb_team': player_info.get('editorial_team_abbr', ''),
                'mlb_team_full': player_info.get('editorial_team_full_name', ''),
                'image_url': player_info.get('image_url', ''),
                'position_type': player_info.get('position_type', 'B'),
                'status': status if status else 'Active',
                'on_il': False,
                'is_starting': None,
                'total_points': total,
                'trending': None,
                'trending_delta': None,
            })
        except (KeyError, IndexError, TypeError):
            continue

    return players


def _parse_league_player_stats(data):
    """
    Parse /league/{key}/players;player_keys=.../stats response.
    Returns {player_key: total_points (float)}.

    Same shape as _parse_player_stats but rooted under league[1] instead of team[1].
    """
    points_map = {}
    try:
        league_arr = data['fantasy_content']['league']
        players_container = _arr_get(league_arr, 1)
        if not isinstance(players_container, dict):
            return points_map
        players_obj = players_container.get('players', {})
        if not players_obj:
            return points_map
    except (KeyError, TypeError):
        return points_map

    for player_wrapper in _get_list_value(players_obj):
        try:
            player_arr = player_wrapper['player']
            info_raw = _arr_get(player_arr, 0)
            player_info = _flatten_array(info_raw) if isinstance(info_raw, list) else (info_raw if isinstance(info_raw, dict) else {})
            player_key = player_info.get('player_key', '')
            if not player_key:
                continue

            extra = _arr_get(player_arr, 1)
            if not isinstance(extra, dict):
                extra = {}

            pp = extra.get('player_points', {})
            if isinstance(pp, list):
                pp = _flatten_array(pp)
            total = pp.get('total') if isinstance(pp, dict) else None
            if total is not None:
                try:
                    points_map[player_key] = float(total)
                except (ValueError, TypeError):
                    pass
        except (KeyError, IndexError, TypeError):
            continue

    return points_map


def _parse_player_starting_statuses(data):
    """
    Parse /league/{key}/players;player_keys=.../starting_status response.
    Returns {player_key: bool}.
    """
    result = {}
    try:
        league_arr = data['fantasy_content']['league']
        players_container = _arr_get(league_arr, 1)
        if not isinstance(players_container, dict):
            logger.debug('_parse_player_starting_statuses: players_container not a dict: %s', type(players_container))
            return result
        players_obj = players_container.get('players', {})
        if not players_obj:
            logger.debug('_parse_player_starting_statuses: no players key. container keys: %s', list(players_container.keys()))
            return result
    except (KeyError, TypeError) as e:
        logger.debug('_parse_player_starting_statuses: failed to reach players – %s', e)
        return result

    for player_wrapper in _get_list_value(players_obj):
        try:
            player_arr = player_wrapper['player']
            info_raw = _arr_get(player_arr, 0)
            player_info = (
                _flatten_array(info_raw) if isinstance(info_raw, list)
                else (info_raw if isinstance(info_raw, dict) else {})
            )
            player_key = player_info.get('player_key', '')
            player_name = player_info.get('name', {})
            if isinstance(player_name, dict):
                player_name = player_name.get('full', player_key)
            if not player_key:
                continue

            arr_len = (len(player_arr) if isinstance(player_arr, list)
                       else int(player_arr.get('count', 0)) + 1)
            found_ss = False
            for idx in range(1, max(arr_len, 5)):
                elem = _arr_get(player_arr, idx)
                if isinstance(elem, dict) and 'starting_status' in elem:
                    raw_ss = elem['starting_status']
                    val = _extract_is_starting(raw_ss)
                    logger.debug(
                        '_parse_player_starting_statuses player=%s key=%s raw_ss=%s parsed=%s',
                        player_name, player_key, raw_ss, val,
                    )
                    if val is not None:
                        result[player_key] = val
                    found_ss = True
                    break
            if not found_ss:
                logger.debug(
                    '_parse_player_starting_statuses player=%s key=%s: no starting_status found in arr (len=%s)',
                    player_name, player_key, arr_len,
                )
        except (KeyError, IndexError, TypeError) as e:
            logger.debug('_parse_player_starting_statuses: error parsing player – %s', e)
            continue

    return result


def _parse_player_ownership(data):
    """
    Parse /league/{key}/players;player_keys=.../ownership response.
    Returns {player_key: {'percent_owned': float|None, 'percent_owned_change': float|None}}.

    Yahoo's ownership sub-resource shape (per player):
      player[1]['ownership']['percent_owned']        → float string e.g. "73.45"
      player[1]['ownership']['percent_owned_change'] → float string e.g. "+4.12" or "-1.5"
    """
    result = {}
    try:
        league_arr = data['fantasy_content']['league']
        players_container = _arr_get(league_arr, 1)
        if not isinstance(players_container, dict):
            return result
        players_obj = players_container.get('players', {})
        if not players_obj:
            return result
    except (KeyError, TypeError):
        return result

    def _float(val):
        try:
            return float(val) if val not in (None, '', '-') else None
        except (ValueError, TypeError):
            return None

    for player_wrapper in _get_list_value(players_obj):
        try:
            player_arr = player_wrapper['player']
            info_raw = _arr_get(player_arr, 0)
            player_info = (
                _flatten_array(info_raw) if isinstance(info_raw, list)
                else (info_raw if isinstance(info_raw, dict) else {})
            )
            player_key = player_info.get('player_key', '')
            if not player_key:
                continue

            arr_len = (len(player_arr) if isinstance(player_arr, list)
                       else int(player_arr.get('count', 0)) + 1)
            for idx in range(1, max(arr_len, 5)):
                elem = _arr_get(player_arr, idx)
                if not isinstance(elem, dict):
                    continue
                ownership = elem.get('ownership')
                if ownership is None:
                    continue
                if isinstance(ownership, list):
                    ownership = _flatten_array(ownership)
                result[player_key] = {
                    'percent_owned':        _float(ownership.get('percent_owned')),
                    'percent_owned_change': _float(ownership.get('percent_owned_change')),
                }
                break
        except (KeyError, IndexError, TypeError) as e:
            logger.debug('_parse_player_ownership: error parsing player – %s', e)
            continue

    return result


def _parse_league_standings(data):
    """
    Parse /league/{key}/standings response.

    Returns a list of team dicts:
      { team_key, team_name, rank, points_for, wins, losses, ties }

    points_for is the season total fantasy points (present in all scoring types).
    wins/losses/ties are only meaningful in H2H leagues; they'll be 0 for roto.
    """
    teams = []
    try:
        league_arr = data['fantasy_content']['league']

        standings_block = None
        for item in (league_arr if isinstance(league_arr, list) else _get_list_value(league_arr)):
            if isinstance(item, dict) and 'standings' in item:
                standings_block = item['standings']
                break
        if standings_block is None:
            return teams

        # standings is a list with one element: {"teams": {count-keyed}}
        if isinstance(standings_block, list):
            standings_block = standings_block[0] if standings_block else {}

        teams_obj = standings_block.get('teams', {})
    except (KeyError, TypeError):
        return teams

    for team_wrapper in _get_list_value(teams_obj):
        try:
            team_arr = team_wrapper['team']
            info_raw = _arr_get(team_arr, 0)
            team_info = _flatten_array(info_raw) if isinstance(info_raw, list) else (info_raw if isinstance(info_raw, dict) else {})

            name_data = team_info.get('name', '')
            team_name = name_data if isinstance(name_data, str) else str(name_data)
            team_key = team_info.get('team_key', '')

            extra = _arr_get(team_arr, 1)
            ts = extra.get('team_standings', {}) if isinstance(extra, dict) else {}
            if isinstance(ts, list):
                ts = _flatten_array(ts)

            def _flt(val, default=0.0):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return default

            def _int(val, default=0):
                try:
                    return int(val)
                except (TypeError, ValueError):
                    return default

            rank = _int(ts.get('rank', 0))
            points_for = _flt(ts.get('points_for', 0))

            outcome = ts.get('outcome_totals', {})
            if isinstance(outcome, list):
                outcome = _flatten_array(outcome)
            wins   = _int(outcome.get('wins',   0)) if isinstance(outcome, dict) else 0
            losses = _int(outcome.get('losses', 0)) if isinstance(outcome, dict) else 0
            ties   = _int(outcome.get('ties',   0)) if isinstance(outcome, dict) else 0

            teams.append({
                'team_key':   team_key,
                'team_name':  team_name,
                'rank':       rank,
                'points_for': points_for,
                'wins':       wins,
                'losses':     losses,
                'ties':       ties,
            })
        except (KeyError, IndexError, TypeError):
            continue

    return teams


def _parse_league_scoreboard(data):
    """
    Parse /league/{key}/scoreboard response.

    Returns {team_key: week_points (float)} for all teams currently in a matchup.
    """
    result = {}
    try:
        league_arr = data['fantasy_content']['league']

        scoreboard_block = None
        for item in (league_arr if isinstance(league_arr, list) else _get_list_value(league_arr)):
            if isinstance(item, dict) and 'scoreboard' in item:
                scoreboard_block = item['scoreboard']
                break
        if scoreboard_block is None:
            return result

        # scoreboard: {"0": {"matchups": {count-keyed}}, "count": N}
        inner = scoreboard_block.get('0', {}) if isinstance(scoreboard_block, dict) else {}
        matchups_obj = inner.get('matchups', {}) if isinstance(inner, dict) else {}
    except (KeyError, TypeError):
        return result

    for matchup_wrapper in _get_list_value(matchups_obj):
        try:
            matchup_arr = matchup_wrapper.get('matchup', [])
            teams_block = None
            for elem in (matchup_arr if isinstance(matchup_arr, list) else _get_list_value(matchup_arr)):
                if isinstance(elem, dict) and 'teams' in elem:
                    teams_block = elem['teams']
                    break
            if teams_block is None:
                continue

            for team_wrapper in _get_list_value(teams_block):
                team_arr = team_wrapper.get('team', [])
                info_raw = _arr_get(team_arr, 0)
                team_info = _flatten_array(info_raw) if isinstance(info_raw, list) else (info_raw if isinstance(info_raw, dict) else {})
                team_key = team_info.get('team_key', '')

                extra = _arr_get(team_arr, 1)
                tp = extra.get('team_points', {}) if isinstance(extra, dict) else {}
                if isinstance(tp, list):
                    tp = _flatten_array(tp)
                total = tp.get('total') if isinstance(tp, dict) else None
                try:
                    week_pts = float(total) if total is not None else 0.0
                except (TypeError, ValueError):
                    week_pts = 0.0

                if team_key:
                    result[team_key] = week_pts
        except (KeyError, IndexError, TypeError):
            continue

    return result


def _parse_league(data):
    try:
        league_arr = data['fantasy_content']['league']
        return _flatten_array(league_arr)
    except (KeyError, IndexError, TypeError):
        return {}


def _parse_league_settings(data):
    """
    Parse /league/{key}/settings response into a flat dict suitable for
    populating a LeagueSettings model instance.

    Top-level league fields (name, num_teams, etc.) live in league[0..N-2].
    The settings sub-resource lives in the last element: {'settings': [...]}.
    """
    result = {}
    try:
        league_arr = data['fantasy_content']['league']

        # Separate the settings block from the top-level info items
        settings_block = {}
        info_items = []
        for item in (league_arr if isinstance(league_arr, list) else _get_list_value(league_arr)):
            if isinstance(item, dict) and 'settings' in item:
                raw = item['settings']
                # settings value is either a list of dicts or a count-keyed dict
                if isinstance(raw, list):
                    settings_block = _flatten_array(raw)
                elif isinstance(raw, dict):
                    settings_block = _flatten_array(_get_list_value(raw)) if 'count' in raw else raw
            else:
                info_items.append(item)

        league_info = _flatten_array(info_items)

        def _int(val, default=None):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        def _bool(val):
            try:
                return bool(int(val))
            except (TypeError, ValueError):
                return False

        # Yahoo uses several field names for the season-total add limit
        max_season_adds = (
            _int(settings_block.get('max_adds')) or
            _int(settings_block.get('max_season_adds')) or
            _int(settings_block.get('max_moves'))
        )

        result = {
            'league_key':     league_info.get('league_key', ''),
            'name':           league_info.get('name', ''),
            'season':         _int(league_info.get('season')),
            'num_teams':      _int(league_info.get('num_teams')),
            'scoring_type':   league_info.get('scoring_type', ''),
            'current_week':   _int(league_info.get('current_week')),
            'start_week':     _int(league_info.get('start_week')),
            'end_week':       _int(league_info.get('end_week')),
            # From the settings sub-block
            'draft_type':      settings_block.get('draft_type', ''),
            'uses_faab':       _bool(settings_block.get('uses_faab', 0)),
            'max_weekly_adds': _int(settings_block.get('max_weekly_adds')),
            'max_season_adds': max_season_adds,
            'trade_end_date':  settings_block.get('trade_end_date') or None,
        }
    except (KeyError, IndexError, TypeError) as e:
        logger.warning('_parse_league_settings error: %s', e)

    return result


def _flatten_array(arr):
    """Merge a list of single-key dicts into one flat dict."""
    result = {}
    for item in arr:
        if isinstance(item, dict):
            result.update(item)
    return result


def _parse_yahoo_error_xml(text):
    """Extract a human-readable description from Yahoo's XML error response.

    Yahoo error bodies look like:
        <yahoo:error xmlns:yahoo="http://www.yahooapis.com/v1/base.rng">
          <yahoo:description>...</yahoo:description>
        </yahoo:error>

    Returns the description string, or None if the body can't be parsed.
    """
    if not text:
        return None
    try:
        root = ET.fromstring(text)
        ns = {'yahoo': 'http://www.yahooapis.com/v1/base.rng'}
        desc = root.find('yahoo:description', ns)
        if desc is not None and desc.text:
            return desc.text.strip()
        # Fallback: search without namespace prefix
        desc = root.find('.//{http://www.yahooapis.com/v1/base.rng}description')
        if desc is not None and desc.text:
            return desc.text.strip()
    except ET.ParseError:
        pass
    return None
