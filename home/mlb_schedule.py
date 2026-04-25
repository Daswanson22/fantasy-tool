"""
home/mlb_schedule.py

Builds hitter vs pitcher matchup data using the ESPN scoreboard API,
which we already call in espn_api.py for probable starters.

ESPN returns team abbreviations directly (e.g. "ATL", "NYM") and
ESPN athlete IDs we can use for headshots.
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

ESPN_HEADSHOT = 'https://a.espncdn.com/i/headshots/mlb/players/full/{espn_id}.png'

# Yahoo editorial_team_abbr → ESPN/canonical abbreviation
_YAHOO_CANON = {
    'ARI': 'ARI', 'AZ':  'ARI',
    'ATL': 'ATL',
    'BAL': 'BAL',
    'BOS': 'BOS',
    'CHC': 'CHC',
    'CWS': 'CWS', 'CHW': 'CWS',
    'CIN': 'CIN',
    'CLE': 'CLE',
    'COL': 'COL',
    'DET': 'DET',
    'HOU': 'HOU',
    'KC':  'KC',  'KCR': 'KC',
    'LAA': 'LAA',
    'LAD': 'LAD',
    'MIA': 'MIA', 'FLA': 'MIA',
    'MIL': 'MIL',
    'MIN': 'MIN',
    'NYM': 'NYM',
    'NYY': 'NYY',
    'OAK': 'OAK', 'ATH': 'OAK', 'LVA': 'OAK',
    'PHI': 'PHI',
    'PIT': 'PIT',
    'SD':  'SD',  'SDP': 'SD',
    'SF':  'SF',  'SFG': 'SF',
    'SEA': 'SEA',
    'STL': 'STL',
    'TB':  'TB',  'TBR': 'TB',
    'TEX': 'TEX',
    'TOR': 'TOR',
    'WSH': 'WSH', 'WAS': 'WSH', 'WSN': 'WSH',
}


def _canon(abbr: str) -> str:
    return _YAHOO_CANON.get(abbr.upper().strip(), abbr.upper().strip())


def get_pitcher_matchups(days: int = 7) -> dict:
    """
    Return (matchups, debug_sample).

    matchups: {(date_str, canonical_hitting_team_abbrev) → pitcher_info}

    Each pitcher_info:
        {
            'pitcher_name':  str,
            'pitcher_image': str | None,  # ESPN headshot URL
            'handedness':    str,          # '' (ESPN probables don't include hand)
            'home_away':     str,          # 'vs' or '@' from the hitter's perspective
        }
    """
    from home.espn_api import get_probable_starters_by_date

    today = date.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]

    by_date = get_probable_starters_by_date(dates)

    matchups: dict = {}

    for date_str, starters in by_date.items():
        for starter in starters:
            opponent_canon = _canon(starter.get('opponent', ''))
            if not opponent_canon:
                continue

            espn_id = starter.get('espn_id', '')
            hitter_loc = 'vs' if starter.get('home_away') == 'away' else '@'

            matchups[(date_str, opponent_canon)] = {
                'pitcher_name':  starter.get('name', 'TBD'),
                'pitcher_image': ESPN_HEADSHOT.format(espn_id=espn_id) if espn_id else None,
                'throws':        starter.get('throws', ''),
                'home_away':     hitter_loc,
            }

    logger.debug('ESPN matchups built: %d entries', len(matchups))
    return matchups
