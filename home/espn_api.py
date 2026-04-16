import unicodedata
import logging
import requests
from datetime import date as date_cls
from django.core.cache import cache

logger = logging.getLogger(__name__)

ESPN_MLB_SCOREBOARD = 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard'


def normalize_name(name):
    """Normalize a player name for fuzzy matching across ESPN and Yahoo.

    Decomposes unicode (strips accents), lowercases, and collapses whitespace.
    Example: 'Luís Gárcia Jr.' → 'luis garcia jr.'
    """
    nfkd = unicodedata.normalize('NFKD', name or '')
    ascii_name = nfkd.encode('ascii', 'ignore').decode('ascii')
    return ' '.join(ascii_name.lower().split())


def get_probable_starters_by_date(dates):
    """Return ESPN probable starting pitchers for each date.

    Calls the MLB scoreboard endpoint once per date and parses the
    `probables` array on each competitor. Results are cached:
      • today      → 30 minutes (lineup could change)
      • future     → 4 hours    (probables rarely updated intra-day)

    Args:
        dates: list of 'YYYY-MM-DD' strings

    Returns:
        {date_str: [{'name': str, 'team': str, 'espn_id': str}, ...]}
        Missing / errored dates map to [].
    """
    today_str = date_cls.today().isoformat()
    result = {}

    for date_str in dates:
        cache_key = f'espn_probables:{date_str}'
        cached = cache.get(cache_key)
        if cached is not None:
            result[date_str] = cached
            continue

        espn_date = date_str.replace('-', '')   # YYYYMMDD
        try:
            resp = requests.get(
                ESPN_MLB_SCOREBOARD,
                params={'dates': espn_date},
                timeout=10,
            )
            resp.raise_for_status()
            starters = _parse_probable_starters(resp.json())
            timeout = 1800 if date_str == today_str else 14400
            cache.set(cache_key, starters, timeout=timeout)
            result[date_str] = starters
            logger.debug('ESPN probables date=%s count=%s', date_str, len(starters))
        except Exception as e:
            logger.warning('ESPN scoreboard error date=%s: %s', date_str, e)
            result[date_str] = []

    return result


def _parse_probable_starters(data):
    """Extract probable starters from an ESPN MLB scoreboard response.

    Each starter includes the opponent team abbreviation and whether the
    pitcher's team is home or away, so the UI can show e.g. "@ NYY" or "vs BOS".
    """
    starters = []
    for event in data.get('events', []):
        for comp in event.get('competitions', []):
            competitors = comp.get('competitors', [])
            # Map homeAway → team abbreviation so we can resolve the opponent
            side_to_team = {
                c.get('homeAway', ''): c.get('team', {}).get('abbreviation', '')
                for c in competitors
            }
            for competitor in competitors:
                home_away = competitor.get('homeAway', '')
                team_abbr = competitor.get('team', {}).get('abbreviation', '')
                opp_side = 'away' if home_away == 'home' else 'home'
                opponent = side_to_team.get(opp_side, '')
                for probable in competitor.get('probables', []):
                    athlete = probable.get('athlete', {})
                    name = athlete.get('displayName', '').strip()
                    if name:
                        starters.append({
                            'name': name,
                            'team': team_abbr,
                            'espn_id': str(athlete.get('id', '')),
                            'opponent': opponent,
                            'home_away': home_away,  # 'home' or 'away'
                        })
    return starters
