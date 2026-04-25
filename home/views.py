import logging
from datetime import date as date_cls, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from accounts.models import SelectedLeague, KeptPlayer, AIManagerConfig, LeagueSettings
from .yahoo_api import get_api_for_user, TokenExpiredError

logger = logging.getLogger(__name__)

_TEAM_CACHE_TTL_SECONDS = 300
_RATE_LIMITS = {
    'select_league': (20, 60),        # 20 requests per minute
    'available_sp_api': (30, 60),     # 30 requests per minute
    'waiver_players_api': (90, 60),   # paginated endpoint
    'toggle_keeper': (120, 60),
    'save_ai_config': (60, 60),
    'toggle_ai_manager': (30, 60),
    'league_analytics_api': (30, 60),
}
_RATE_LIMITS = getattr(settings, 'HOME_RATE_LIMITS', _RATE_LIMITS)


def index(request):
    return render(request, 'home/index.html', {
        'stripe_price_pro':   settings.STRIPE_PRICE_PRO,
        'stripe_price_elite': settings.STRIPE_PRICE_ELITE,
    })


def _is_hash_username(username):
    return len(username) >= 30 and all(c in '0123456789abcdef' for c in username)


def _get_tier_info(user):
    try:
        profile = user.profile
        return profile.tier, profile.get_league_limit()
    except Exception:
        return 'free', 1


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _is_rate_limited(request, scope):
    limit, window = _RATE_LIMITS[scope]
    key = f'rl:{scope}:{request.user.pk}:{_client_ip(request)}'
    current = int(cache.get(key, 0) or 0)
    if current >= limit:
        return True
    if cache.add(key, 1, timeout=window):
        return False
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
    return False


def _selected_team_keys(user):
    return set(
        SelectedLeague.objects.filter(user=user).values_list('team_key', flat=True)
    )


def _get_user_mlb_teams(user, api):
    cache_key = f'user_mlb_teams:{user.pk}'
    teams = cache.get(cache_key)
    if teams is None:
        teams = api.get_mlb_teams()
        cache.set(cache_key, teams, timeout=_TEAM_CACHE_TTL_SECONDS)
    return teams


def _is_authorized_team_key(user, team_key, api=None):
    if not team_key or '.t.' not in team_key:
        return False

    saved_keys = _selected_team_keys(user)
    if saved_keys:
        return team_key in saved_keys

    if api is None:
        social = user.social_auth.get(provider='yahoo-oauth2')
        api = get_api_for_user(social)

    tier, league_limit = _get_tier_info(user)
    del tier  # tier itself is not needed; league_limit is.
    teams = _get_user_mlb_teams(user, api)
    if league_limit is not None:
        teams = teams[:league_limit]
    allowed = {t.get('team_key', '') for t in teams}
    return team_key in allowed


@login_required
def dashboard(request):
    user = request.user
    yahoo_connected = False
    yahoo_guid = None
    mlb_teams = []

    tier, league_limit = _get_tier_info(user)

    # Leagues the user has permanently selected
    saved_keys = set(
        SelectedLeague.objects.filter(user=user).values_list('team_key', flat=True)
    )
    slots_remaining = (
        (league_limit - len(saved_keys)) if league_limit is not None
        else float('inf')
    )
    selection_complete = league_limit is not None and len(saved_keys) >= league_limit

    try:
        social = user.social_auth.get(provider='yahoo-oauth2')
        yahoo_connected = True
        yahoo_guid = social.extra_data.get('yahoo_guid', '')

        if _is_hash_username(user.username):
            email = user.email or social.extra_data.get('email', '')
            if email:
                user.username = email.split('@')[0]
                user.save(update_fields=['username'])

        api = get_api_for_user(social)
        mlb_teams = api.get_mlb_teams()

    except TokenExpiredError:
        messages.warning(request, 'Yahoo session expired — please reconnect.')
    except Exception:
        pass

    # Annotate each team with its league name (from cached LeagueSettings when available)
    league_keys = {t['league_key'] for t in mlb_teams}
    league_name_map = {
        ls.league_key: ls.name
        for ls in LeagueSettings.objects.filter(league_key__in=league_keys)
        if ls.name
    }
    for team in mlb_teams:
        team['league_name'] = league_name_map.get(team['league_key'], '')

    # Annotate each team with its display state
    for team in mlb_teams:
        if team['team_key'] in saved_keys:
            team['state'] = 'selected'          # permanently chosen
        elif selection_complete:
            team['state'] = 'locked_permanent'  # selection full, cannot pick
        elif slots_remaining > 0:
            team['state'] = 'available'         # can be selected
        else:
            team['state'] = 'locked_permanent'

    return render(request, 'home/dashboard.html', {
        'yahoo_connected': yahoo_connected,
        'yahoo_guid': yahoo_guid,
        'mlb_teams': mlb_teams,
        'tier': tier,
        'league_limit': league_limit,
        'slots_remaining': slots_remaining,
        'selection_complete': selection_complete,
        'saved_count': len(saved_keys),
    })


@login_required
@require_POST
def select_league(request):
    """Permanently save a league choice for this user."""
    if _is_rate_limited(request, 'select_league'):
        messages.error(request, 'Too many requests. Please wait and try again.')
        return redirect('home:dashboard')

    team_key  = request.POST.get('team_key', '').strip()

    if not team_key:
        return redirect('home:dashboard')
    if '.t.' not in team_key:
        messages.error(request, 'Invalid league selection.')
        return redirect('home:dashboard')

    _, league_limit = _get_tier_info(request.user)
    current_count = SelectedLeague.objects.filter(user=request.user).count()

    # Already selected
    if SelectedLeague.objects.filter(user=request.user, team_key=team_key).exists():
        return redirect('home:dashboard')

    # Tier limit reached
    if league_limit is not None and current_count >= league_limit:
        messages.error(request, 'You have already selected the maximum leagues for your plan.')
        return redirect('home:dashboard')

    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
        api = get_api_for_user(social)
        teams = _get_user_mlb_teams(request.user, api)
    except TokenExpiredError:
        messages.error(request, 'Your Yahoo session expired. Please reconnect.')
        return redirect('home:dashboard')
    except Exception as exc:
        logger.error(
            'select_league verification failed user=%s exc_type=%s',
            request.user.pk,
            type(exc).__name__,
        )
        messages.error(request, 'Could not verify this league right now. Please try again.')
        return redirect('home:dashboard')

    matched = next((t for t in teams if t.get('team_key') == team_key), None)
    if not matched:
        messages.error(request, 'Invalid league selection.')
        return redirect('home:dashboard')

    team_name = matched.get('team_name', '')
    league_key = matched.get('league_key', team_key.rsplit('.t.', 1)[0])

    SelectedLeague.objects.create(
        user=request.user,
        team_key=team_key,
        team_name=team_name,
        league_key=league_key,
    )
    cache.delete(f'user_mlb_teams:{request.user.pk}')
    messages.success(request, f'"{team_name}" has been added to your leagues.')
    return redirect('home:dashboard')




@login_required
def teams(request):
    """
    Show the roster for a specific MLB team identified by ?key=<team_key>.
    Only accessible if the team_key belongs to one of the user's selected leagues.
    """
    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
    except Exception:
        messages.error(request, 'Please connect your Yahoo Fantasy account first.')
        return redirect('home:dashboard')

    tier, league_limit = _get_tier_info(request.user)

    # Enforce: only allow access to selected leagues
    saved = list(SelectedLeague.objects.filter(user=request.user))
    saved_keys = {sl.team_key for sl in saved}

    requested_key = request.GET.get('key', '').strip()

    # If the user hasn't selected leagues yet, fall through using position-based limit
    if saved_keys and requested_key and requested_key not in saved_keys:
        return render(request, 'home/teams.html', {
            'team': None, 'starters': [], 'bench': [],
            'locked': True, 'tier': tier,
        })

    try:
        api = get_api_for_user(social)
        all_mlb_teams = api.get_mlb_teams()
    except TokenExpiredError:
        messages.error(request, 'Your Yahoo session expired. Please reconnect.')
        return redirect('home:dashboard')
    except Exception as e:
        messages.error(request, f'Could not load teams from Yahoo: {e}')
        return redirect('home:dashboard')

    if not all_mlb_teams:
        return render(request, 'home/teams.html', {
            'team': None, 'starters': [], 'bench': [],
            'error': 'No MLB fantasy teams found on your Yahoo account.',
        })

    if requested_key:
        team = next((t for t in all_mlb_teams if t['team_key'] == requested_key), None)
        if not team:
            messages.error(request, 'Team not found.')
            return redirect('home:dashboard')
        # Position-based fallback for users who haven't selected yet
        if not saved_keys:
            idx = next(i for i, t in enumerate(all_mlb_teams) if t['team_key'] == requested_key)
            if league_limit is not None and idx >= league_limit:
                return render(request, 'home/teams.html', {
                    'team': None, 'starters': [], 'bench': [],
                    'locked': True, 'tier': tier,
                })
    else:
        team = all_mlb_teams[0]

    # Load (and cache) league settings — refresh if missing or stale (> 24 h)
    league_key = team['league_key']
    league_name = league_key  # fallback
    league_settings_obj = LeagueSettings.objects.filter(league_key=league_key).first()

    if league_settings_obj is None or league_settings_obj.is_stale():
        try:
            raw = api.get_league_settings(league_key)
            league_name = raw.get('name', league_key) or league_key

            from datetime import date as _date
            trade_end = None
            if raw.get('trade_end_date'):
                try:
                    trade_end = _date.fromisoformat(raw['trade_end_date'])
                except ValueError:
                    pass

            LeagueSettings.objects.update_or_create(
                league_key=league_key,
                defaults={
                    'name':             raw.get('name', ''),
                    'season':           raw.get('season'),
                    'num_teams':        raw.get('num_teams'),
                    'scoring_type':     raw.get('scoring_type', ''),
                    'draft_type':       raw.get('draft_type', ''),
                    'uses_faab':        raw.get('uses_faab', False),
                    'max_weekly_adds':  raw.get('max_weekly_adds'),
                    'max_season_adds':  raw.get('max_season_adds'),
                    'trade_end_date':   trade_end,
                    'current_week':     raw.get('current_week'),
                    'start_week':       raw.get('start_week'),
                    'end_week':         raw.get('end_week'),
                },
            )
            league_settings_obj = LeagueSettings.objects.get(league_key=league_key)
        except Exception as e:
            logger.warning('Could not sync league settings for %s: %s', league_key, e)
            # Fall back to a lightweight league name fetch
            try:
                info = api.get_league(league_key)
                league_name = info.get('name', league_key)
            except Exception:
                pass
    else:
        league_name = league_settings_obj.name or league_key

    # Date handling for MLB (default to today)
    raw_date = request.GET.get('date', '').strip()
    try:
        roster_date = date_cls.fromisoformat(raw_date)
    except ValueError:
        roster_date = date_cls.today()
    roster_date_str = roster_date.isoformat()
    prev_date = (roster_date - timedelta(days=1)).isoformat()
    next_date = (roster_date + timedelta(days=1)).isoformat()

    cache_key = f'roster:{team["team_key"]}:{roster_date_str}'
    roster = cache.get(cache_key)
    if roster is None:
        try:
            roster = api.get_team_roster(team['team_key'], date=roster_date_str)
        except Exception as e:
            roster = []
            messages.warning(request, f'Could not load roster: {e}')

        # Merge season fantasy points
        try:
            points_map = api.get_team_player_stats(team['team_key'], stat_type='season')
            for player in roster:
                player['total_points'] = points_map.get(player['player_key'])
        except Exception as e:
            logger.warning('Could not load season points: %s', e)

        # Compute trending: lastweek per-game vs lastmonth per-game
        # lastweek_per_game  = lastweek_total  / 6  (~6 MLB games/week)
        # lastmonth_per_game = lastmonth_total / 24 (~6 games/week × 4 weeks)
        # delta >= +0.5 → up, <= -0.5 → down, otherwise steady
        try:
            lastweek_map = api.get_team_player_stats(team['team_key'], stat_type='lastweek')
            lastmonth_map = api.get_team_player_stats(team['team_key'], stat_type='lastmonth')
            for player in roster:
                pk = player['player_key']
                lastweek_total = lastweek_map.get(pk)
                lastmonth_total = lastmonth_map.get(pk)
                if lastweek_total is not None and lastmonth_total is not None and lastmonth_total > 0:
                    delta = (lastweek_total / 6) - (lastmonth_total / 24)
                    player['trending_delta'] = round(delta, 1)
                    if delta >= 1.5:
                        player['trending'] = 'up'
                    elif delta < 0:
                        player['trending'] = 'down'
                    else:
                        player['trending'] = 'steady'
        except Exception as e:
            logger.warning('Could not compute trending: %s', e)

        cache.set(cache_key, roster, timeout=300)  # 5 minutes

    batting_lineup, pitching_lineup, bench, il_list = [], [], [], []
    for player in roster:
        sel_pos = player.get('selected_position', '')
        if sel_pos in ('IL', 'DL', 'NA'):
            il_list.append(player)
        elif sel_pos == 'BN':
            bench.append(player)
        elif player.get('position_type') == 'P':
            pitching_lineup.append(player)
        else:
            batting_lineup.append(player)

    kept_keys = set(
        KeptPlayer.objects.filter(user=request.user, team_key=team['team_key'])
        .values_list('player_key', flat=True)
    )
    for player in batting_lineup + pitching_lineup + bench + il_list:
        player['is_kept'] = player.get('player_key', '') in kept_keys

    available_players = _get_available_players(api, team['league_key'])

    try:
        profile = request.user.profile
        can_access_available_sp      = profile.can_access_available_sp
        can_access_matchups          = profile.can_access_matchups
        can_access_ai_gm             = profile.can_access_ai_gm
        can_access_league_analytics  = profile.can_access_league_analytics
    except Exception:
        can_access_available_sp      = False
        can_access_matchups          = False
        can_access_ai_gm             = False
        can_access_league_analytics  = False

    ai_config, _ = AIManagerConfig.objects.get_or_create(
        user=request.user,
        team_key=team['team_key'],
    )

    return render(request, 'home/teams.html', {
        'team': team,
        'league_name': league_name,
        'batting_lineup': batting_lineup,
        'pitching_lineup': pitching_lineup,
        'bench': bench,
        'il_list': il_list,
        'available_players': available_players,
        'roster_date': roster_date_str,
        'prev_date': prev_date,
        'next_date': next_date,
        'tier': tier,
        'locked': False,
        'can_access_available_sp': can_access_available_sp,
        'can_access_matchups': can_access_matchups,
        'can_access_ai_gm': can_access_ai_gm,
        'can_access_league_analytics': can_access_league_analytics,
        'ai_config': ai_config,
        'league_settings': league_settings_obj,
        'remaining_weeks': (
            max(0, (league_settings_obj.end_week or 0) - (league_settings_obj.current_week or 0))
            if league_settings_obj else 0
        ),
    })


_PITCHER_POSITIONS = {'P', 'SP', 'RP'}


def _attach_starting_statuses(api, league_key, players):
    """Fetch starting_status for a list of players and set is_starting in-place."""
    if not players:
        return
    try:
        player_keys = [p['player_key'] for p in players if p.get('player_key')]
        starting_map = api.get_player_starting_statuses(league_key, player_keys)
        for player in players:
            val = starting_map.get(player['player_key'])
            if val is not None:
                player['is_starting'] = val
    except Exception as e:
        logger.warning('Could not load starting statuses: %s', e)


def _compute_player_trends(api, league_key, players):
    """Attach trending/trending_delta to each player dict in-place."""
    if not players:
        return
    try:
        player_keys = [p['player_key'] for p in players if p['player_key']]
        lastweek_map  = api.get_league_player_stats(league_key, player_keys, 'lastweek')
        lastmonth_map = api.get_league_player_stats(league_key, player_keys, 'lastmonth')
        for player in players:
            pk = player['player_key']
            lw = lastweek_map.get(pk)
            lm = lastmonth_map.get(pk)
            if lw is not None and lm and lm > 0:
                delta = (lw / 6) - (lm / 24)
                player['trending_delta'] = round(delta, 1)
                if delta >= 1.5:
                    player['trending'] = 'up'
                elif delta < 0:
                    player['trending'] = 'down'
                else:
                    player['trending'] = 'steady'
    except Exception as e:
        logger.warning('Could not compute trending: %s', e)


def _get_available_players(api, league_key, count=20):
    """Fetch the first page of free agents for initial page render."""
    try:
        players = api.get_league_available_players(league_key, count=count)
    except Exception as e:
        logger.warning('Could not load available players: %s', e)
        return []
    _compute_player_trends(api, league_key, players)
    _attach_starting_statuses(api, league_key, players)
    return players


_WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _day_label(offset, d):
    if offset == 0:
        return 'Today'
    if offset == 1:
        return 'Tomorrow'
    return _WEEKDAY_NAMES[d.weekday()]


@login_required
def available_sp_api(request):
    """AJAX endpoint: returns available starting pitchers for the next 7 days.
    Requires Pro or Elite tier.

    Today    → Yahoo starting_status (confirmed lineups, most accurate).
    Future   → ESPN probable starters cross-referenced against Yahoo's
               available pitcher pool by player name.

    Returns JSON with a 'days' list, each entry:
      date, label, short_date, html, count, confirmed
    """
    try:
        if not request.user.profile.can_access_available_sp:
            return JsonResponse({'error': 'upgrade_required'}, status=403)
    except Exception:
        return JsonResponse({'error': 'upgrade_required'}, status=403)
    if _is_rate_limited(request, 'available_sp_api'):
        return JsonResponse({'error': 'rate_limited'}, status=429)

    from .espn_api import get_probable_starters_by_date, normalize_name

    team_key = request.GET.get('key', '').strip()
    if not team_key or '.t.' not in team_key:
        return JsonResponse({'error': 'invalid_request'}, status=400)

    league_key = team_key.rsplit('.t.', 1)[0]
    today = date_cls.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(7)]

    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
        api = get_api_for_user(social)
        if not _is_authorized_team_key(request.user, team_key, api=api):
            return JsonResponse({'error': 'forbidden'}, status=403)

        # Yahoo: available pitchers supply pts/trending/status data for all days
        pitchers = api.get_league_available_players(league_key, count=100, position='P')
        _compute_player_trends(api, league_key, pitchers)
        yahoo_by_name = {normalize_name(p['name']): p for p in pitchers}

        # ESPN: probable starters for all 7 days (cached)
        espn_by_date = get_probable_starters_by_date(dates)

    except TokenExpiredError:
        return JsonResponse({'error': 'auth_required'}, status=401)
    except Exception as exc:
        logger.error(
            'available_sp_api failed user=%s exc_type=%s',
            request.user.pk,
            type(exc).__name__,
        )
        return JsonResponse({'error': 'service_unavailable'}, status=503)

    days = []
    for i, date_str in enumerate(dates):
        d = today + timedelta(days=i)
        espn_starters = espn_by_date.get(date_str, [])

        players = []
        for starter in espn_starters:
            yahoo_player = yahoo_by_name.get(normalize_name(starter['name']))
            if yahoo_player:
                # Copy so we don't mutate the shared dict
                p = dict(yahoo_player)
                p['opponent'] = starter['opponent']
                p['home_away'] = starter['home_away']
                players.append(p)

        confirmed = bool(espn_starters)
        html = render_to_string(
            'home/partials/_available_sp_rows.html',
            {'players': players, 'confirmed': confirmed},
            request=request,
        )
        days.append({
            'date': date_str,
            'label': _day_label(i, d),
            'short_date': f'{d.month}/{d.day}',
            'html': html,
            'count': len(players),
            'confirmed': confirmed,
        })

    return JsonResponse({'days': days})


@login_required
def waiver_players_api(request):
    """AJAX endpoint: returns rendered waiver-wire rows filtered + paginated."""
    if _is_rate_limited(request, 'waiver_players_api'):
        return JsonResponse({'html': '', 'has_more': False, 'error': 'rate_limited'}, status=429)

    team_key = request.GET.get('key', '').strip()
    if not team_key or '.t.' not in team_key:
        return JsonResponse({'html': '', 'has_more': False, 'error': 'invalid_request'}, status=400)

    league_key      = team_key.rsplit('.t.', 1)[0]
    position        = request.GET.get('position', '').strip()
    team_filter     = request.GET.get('team', '').strip()
    status_filter   = request.GET.get('status', '').strip()
    starting_filter = request.GET.get('starting', '').strip()
    sort            = request.GET.get('sort', 'pts').strip()
    if sort not in ('pts', 'name', 'trending'):
        sort = 'pts'
    try:
        page = max(int(request.GET.get('page', 1)), 1)
    except (TypeError, ValueError):
        page = 1
    per_page = 20

    # Map UI position values to Yahoo API position params.
    # __pitchers → 'P' (Yahoo returns SP+RP natively).
    # __batters  → no Yahoo equivalent; must filter server-side.
    # Specific positions (SP, RP, OF, …) pass through directly.
    yahoo_position = None
    if position == '__pitchers':
        yahoo_position = 'P'
    elif position and not position.startswith('__'):
        yahoo_position = position

    # Any filter/sort that can't be handled by Yahoo's native offset pagination
    # requires fetching a large batch and processing server-side.
    needs_server_filter = bool(
        team_filter or
        position == '__batters' or
        status_filter or
        starting_filter or
        sort != 'pts'
    )

    if needs_server_filter:
        yahoo_start = 0
        fetch_count = 300
    else:
        yahoo_start = (page - 1) * per_page
        fetch_count = per_page + 1  # +1 to detect whether a next page exists

    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
        api    = get_api_for_user(social)
        if not _is_authorized_team_key(request.user, team_key, api=api):
            return JsonResponse({'html': '', 'has_more': False, 'error': 'forbidden'}, status=403)
        players = api.get_league_available_players(
            league_key, count=fetch_count, start=yahoo_start,
            position=yahoo_position,
        )
    except TokenExpiredError:
        return JsonResponse(
            {'html': '', 'has_more': False, 'total_pages': 1, 'error': 'auth_required'},
            status=401,
        )
    except Exception as exc:
        logger.error(
            'waiver_players_api failed user=%s exc_type=%s',
            request.user.pk,
            type(exc).__name__,
        )
        return JsonResponse(
            {'html': '', 'has_more': False, 'total_pages': 1, 'error': 'service_unavailable'},
            status=503,
        )

    if needs_server_filter:
        # __batters has no Yahoo-native equivalent — filter server-side.
        # __pitchers is already handled via yahoo_position='P' above.
        if position == '__batters':
            players = [p for p in players
                       if not any(pos in _PITCHER_POSITIONS
                                  for pos in p['eligible_positions'].split(', '))]

        if team_filter:
            players = [p for p in players if p['mlb_team'] == team_filter]

        # Compute trends + starting statuses for all candidates before sorting/filtering
        _compute_player_trends(api, league_key, players)
        if starting_filter or sort == 'trending':
            _attach_starting_statuses(api, league_key, players)

        # Status filter (injury status, not roster status)
        if status_filter == 'Active':
            players = [p for p in players if p.get('status') == 'Active']
        elif status_filter == 'DTD':
            players = [p for p in players if p.get('status') == 'DTD']
        elif status_filter == 'DL':
            players = [p for p in players
                       if p.get('status', 'Active') not in ('Active', 'DTD')]

        # Starting filter — only meaningful for batters; pitchers don't have batting
        # lineup starting status so they always pass through this filter.
        if starting_filter == 'yes':
            players = [p for p in players
                       if p.get('position_type') == 'P' or p.get('is_starting') is True]
        elif starting_filter == 'no':
            players = [p for p in players
                       if p.get('position_type') == 'P' or p.get('is_starting') is False]

        # Sort
        if sort == 'name':
            players.sort(key=lambda p: p.get('name', '').lower())
        elif sort == 'trending':
            players.sort(
                key=lambda p: p.get('trending_delta') if p.get('trending_delta') is not None else float('-inf'),
                reverse=True,
            )
        # 'pts' is already sorted by Yahoo

        total_filtered = len(players)
        start_idx      = (page - 1) * per_page
        page_players   = players[start_idx:start_idx + per_page]
        has_more       = start_idx + per_page < total_filtered
        total_pages    = max(1, (total_filtered + per_page - 1) // per_page)

        # Attach starting statuses for display on the page slice (only if not already done above)
        if not starting_filter and sort != 'trending':
            _attach_starting_statuses(api, league_key, page_players)
    else:
        page_players = players[:per_page]
        has_more     = len(players) > per_page
        total_pages  = None  # unknown — discovered progressively by the client
        _compute_player_trends(api, league_key, page_players)
        _attach_starting_statuses(api, league_key, page_players)

    html = render_to_string(
        'home/partials/_waiver_rows.html',
        {'players': page_players},
        request=request,
    )
    return JsonResponse({'html': html, 'has_more': has_more, 'page': page, 'total_pages': total_pages})


@login_required
@require_POST
def toggle_keeper(request):
    """Toggle keeper status for a player. Returns {'kept': bool}."""
    if _is_rate_limited(request, 'toggle_keeper'):
        return JsonResponse({'error': 'rate_limited'}, status=429)

    player_key = request.POST.get('player_key', '').strip()
    team_key   = request.POST.get('team_key', '').strip()

    if not player_key or not team_key:
        return JsonResponse({'error': 'invalid_request'}, status=400)
    try:
        if not _is_authorized_team_key(request.user, team_key):
            return JsonResponse({'error': 'forbidden'}, status=403)
    except TokenExpiredError:
        return JsonResponse({'error': 'auth_required'}, status=401)
    except Exception as exc:
        logger.error(
            'toggle_keeper authorization failed user=%s exc_type=%s',
            request.user.pk,
            type(exc).__name__,
        )
        return JsonResponse({'error': 'service_unavailable'}, status=503)

    obj, created = KeptPlayer.objects.get_or_create(
        user=request.user,
        team_key=team_key,
        player_key=player_key,
    )
    if not created:
        obj.delete()
        return JsonResponse({'kept': False})
    return JsonResponse({'kept': True})


@login_required
@require_POST
def save_ai_config(request):
    """Save AI Manager configuration for a team. Returns {'ok': true}."""
    if _is_rate_limited(request, 'save_ai_config'):
        return JsonResponse({'error': 'rate_limited'}, status=429)

    team_key             = request.POST.get('team_key', '').strip()
    league_format        = request.POST.get('league_format', 'roto').strip()  # 'h2h' or 'roto'
    auto_promote_starters = request.POST.get('auto_promote_starters', 'false').strip().lower() == 'true'

    if not team_key:
        return JsonResponse({'error': 'invalid_request'}, status=400)
    try:
        if not _is_authorized_team_key(request.user, team_key):
            return JsonResponse({'error': 'forbidden'}, status=403)
    except TokenExpiredError:
        return JsonResponse({'error': 'auth_required'}, status=401)
    except Exception as exc:
        logger.error(
            'save_ai_config authorization failed user=%s exc_type=%s',
            request.user.pk,
            type(exc).__name__,
        )
        return JsonResponse({'error': 'service_unavailable'}, status=503)

    try:
        if league_format == 'h2h':
            max_total_moves   = max(0, int(request.POST.get('max_total_moves', 0)))
            max_hitter_moves  = 0
            max_pitcher_moves = 0
        else:
            max_hitter_moves  = max(0, int(request.POST.get('max_hitter_moves', 0)))
            max_pitcher_moves = max(0, int(request.POST.get('max_pitcher_moves', 0)))
            max_total_moves   = 0
    except (ValueError, TypeError):
        return JsonResponse({'error': 'invalid_request'}, status=400)

    AIManagerConfig.objects.update_or_create(
        user=request.user,
        team_key=team_key,
        defaults={
            'max_hitter_moves':    max_hitter_moves,
            'max_pitcher_moves':   max_pitcher_moves,
            'max_total_moves':     max_total_moves,
            'auto_promote_starters': auto_promote_starters,
        },
    )
    return JsonResponse({'ok': True})


def _yahoo_team_url(team_key):
    """Return the Yahoo Fantasy Baseball team page URL."""
    try:
        league_part, team_num = team_key.rsplit('.t.', 1)
        league_num = league_part.rsplit('.l.', 1)[1]
        return f'https://baseball.fantasysports.yahoo.com/b1/{league_num}/{team_num}'
    except (ValueError, IndexError):
        return 'https://baseball.fantasysports.yahoo.com'


def _yahoo_add_player_url(team_key, add_player_key, drop_player_key=None):
    """Return Yahoo's add-player deep link with the player pre-selected."""
    try:
        league_part, team_num = team_key.rsplit('.t.', 1)
        league_num = league_part.rsplit('.l.', 1)[1]
        player_num = add_player_key.rsplit('.p.', 1)[1]
        url = (
            f'https://baseball.fantasysports.yahoo.com/b1/{league_num}'
            f'/{team_num}/addplayer?apid={player_num}'
        )
        if drop_player_key:
            drop_num = drop_player_key.rsplit('.p.', 1)[1]
            url += f'&dpid={drop_num}'
        return url
    except (ValueError, IndexError):
        return 'https://baseball.fantasysports.yahoo.com'


@login_required
@require_POST
def toggle_ai_manager(request):
    """Persist the AI Manager enabled/disabled toggle. Returns {'enabled': bool}."""
    if _is_rate_limited(request, 'toggle_ai_manager'):
        return JsonResponse({'error': 'rate_limited'}, status=429)

    team_key = request.POST.get('team_key', '').strip()
    enabled  = request.POST.get('enabled', '').strip().lower()

    if not team_key:
        return JsonResponse({'error': 'invalid_request'}, status=400)
    if enabled not in ('true', 'false'):
        return JsonResponse({'error': 'invalid_request'}, status=400)

    # Authorization: AIManagerConfig is only created when the user successfully
    # loads the teams page, which already verified ownership via _is_authorized_team_key.
    # A missing row means this team_key was never accessed by this user.
    try:
        config = AIManagerConfig.objects.get(user=request.user, team_key=team_key)
    except AIManagerConfig.DoesNotExist:
        return JsonResponse({'error': 'forbidden'}, status=403)

    config.is_enabled = (enabled == 'true')
    config.save(update_fields=['is_enabled'])
    return JsonResponse({'enabled': config.is_enabled})


@login_required
def ai_recommendation_api(request):
    """
    AJAX: run the AI Manager analysis in dry-run mode and return a
    recommendation the user can execute manually on Yahoo.

    Always dry_run=True — Yahoo's standard developer API is read-only,
    so we surface the recommendation with a deep link to Yahoo instead.
    """
    if _is_rate_limited(request, 'ai_recommendation'):
        return JsonResponse({'error': 'rate_limited'}, status=429)

    team_key = request.GET.get('key', '').strip()
    if not team_key or '.t.' not in team_key:
        return JsonResponse({'error': 'invalid_request'}, status=400)

    try:
        if not _is_authorized_team_key(request.user, team_key):
            return JsonResponse({'error': 'forbidden'}, status=403)
    except TokenExpiredError:
        return JsonResponse({'error': 'auth_required'}, status=401)
    except Exception as exc:
        logger.error('ai_recommendation_api auth check failed user=%s: %s',
                     request.user.pk, exc)
        return JsonResponse({'error': 'service_unavailable'}, status=503)

    try:
        social_auth = request.user.social_auth.get(provider='yahoo-oauth2')
    except Exception:
        return JsonResponse({'error': 'auth_required'}, status=401)

    league_key      = team_key.rsplit('.t.', 1)[0]
    league_settings = LeagueSettings.objects.filter(league_key=league_key).first()
    ai_config, _    = AIManagerConfig.objects.get_or_create(
        user=request.user, team_key=team_key,
    )

    from home.ai_manager import run_for_team
    try:
        result = run_for_team(
            user=request.user,
            team_key=team_key,
            social_auth=social_auth,
            league_settings=league_settings,
            ai_config=ai_config,
            dry_run=True,
        )
    except Exception as exc:
        logger.error('ai_recommendation_api run_for_team failed team=%s: %s', team_key, exc)
        return JsonResponse({'error': 'service_unavailable'}, status=503)

    add_player  = result.get('add_player')
    drop_player = result.get('drop_player')

    yahoo_add_url  = None
    yahoo_team_url = _yahoo_team_url(team_key)
    if add_player and drop_player:
        yahoo_add_url = _yahoo_add_player_url(
            team_key,
            add_player['player_key'],
            drop_player['player_key'],
        )

    return JsonResponse({
        'decision':      result['decision'],
        'reason':        result['reason'],
        'drop_player':   drop_player,
        'add_player':    add_player,
        'roster_moves':  result.get('roster_moves', []),
        'yahoo_add_url': yahoo_add_url,
        'yahoo_team_url': yahoo_team_url,
    })


@login_required
def matchups_api(request):
    """
    AJAX: return pitcher matchup data for all hitters over the next 7 days.
    """
    from datetime import date as date_cls, timedelta
    from home.mlb_schedule import get_pitcher_matchups, _canon

    team_key = request.GET.get('key', '').strip()
    if not team_key or '.t.' not in team_key:
        return JsonResponse({'error': 'invalid_request'}, status=400)

    try:
        social_auth = request.user.social_auth.get(provider='yahoo-oauth2')
        api = get_api_for_user(social_auth)
    except Exception:
        return JsonResponse({'error': 'auth_required'}, status=401)

    try:
        roster = api.get_team_roster(team_key)
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=503)

    # Only hitters (non-pitchers, non-IL)
    hitters = [
        p for p in roster
        if p.get('position_type') != 'P'
        and p.get('selected_position') not in ('IL', 'DL', 'NA')
    ]

    today = date_cls.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(7)]

    matchups = get_pitcher_matchups(days=7)

    hitter_rows = []
    for p in hitters:
        norm = _canon(p.get('mlb_team', ''))
        games = [matchups.get((d, norm)) for d in dates]
        hitter_rows.append({
            'name':      p.get('name', ''),
            'image_url': p.get('image_url', ''),
            'mlb_team':  p.get('mlb_team', ''),
            'position':  p.get('selected_position', ''),
            'bats':      p.get('bats', ''),
            'games':     games,
        })

    return JsonResponse({'dates': dates, 'hitters': hitter_rows})


@login_required
def league_analytics_api(request):
    """
    AJAX: return league-wide standings + current week scores for all teams.
    Elite tier only.

    Returns JSON:
      {
        teams: [{rank, team_name, total_points, avg_points_per_week,
                 week_points, wins, losses, ties, is_user_team}],
        weeks_played: int,
        is_h2h: bool,
      }
    """
    try:
        if not request.user.profile.can_access_league_analytics:
            return JsonResponse({'error': 'upgrade_required'}, status=403)
    except Exception:
        return JsonResponse({'error': 'upgrade_required'}, status=403)

    if _is_rate_limited(request, 'league_analytics_api'):
        return JsonResponse({'error': 'rate_limited'}, status=429)

    team_key = request.GET.get('key', '').strip()
    if not team_key or '.t.' not in team_key:
        return JsonResponse({'error': 'invalid_request'}, status=400)

    league_key = team_key.rsplit('.t.', 1)[0]

    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
        api = get_api_for_user(social)
        if not _is_authorized_team_key(request.user, team_key, api=api):
            return JsonResponse({'error': 'forbidden'}, status=403)
    except TokenExpiredError:
        return JsonResponse({'error': 'auth_required'}, status=401)
    except Exception as exc:
        logger.error('league_analytics_api auth failed user=%s: %s', request.user.pk, exc)
        return JsonResponse({'error': 'service_unavailable'}, status=503)

    cache_key = f'league_analytics:{league_key}'
    cached = cache.get(cache_key)
    if cached:
        # Re-stamp is_user_team based on current user's team_key
        for t in cached['teams']:
            t['is_user_team'] = (t['team_key'] == team_key)
        return JsonResponse(cached)

    try:
        standings = api.get_league_standings(league_key)
        week_pts_map = api.get_league_scoreboard(league_key)
    except TokenExpiredError:
        return JsonResponse({'error': 'auth_required'}, status=401)
    except Exception as exc:
        logger.error('league_analytics_api fetch failed league=%s: %s', league_key, exc)
        return JsonResponse({'error': 'service_unavailable'}, status=503)

    league_settings = LeagueSettings.objects.filter(league_key=league_key).first()
    current_week = (league_settings.current_week or 1) if league_settings else 1
    start_week   = (league_settings.start_week   or 1) if league_settings else 1
    is_h2h       = league_settings.is_h2h if league_settings else False
    weeks_played = max(1, current_week - start_week + 1)

    teams_out = []
    for t in standings:
        total_pts = t['points_for']
        avg_pts   = round(total_pts / weeks_played, 1)
        week_p    = week_pts_map.get(t['team_key'], 0.0)
        teams_out.append({
            'team_key':        t['team_key'],
            'team_name':       t['team_name'],
            'rank':            t['rank'],
            'total_points':    round(total_pts, 1),
            'avg_points':      avg_pts,
            'week_points':     round(week_p, 1),
            'wins':            t['wins'],
            'losses':          t['losses'],
            'ties':            t['ties'],
            'is_user_team':    (t['team_key'] == team_key),
        })

    teams_out.sort(key=lambda x: x['rank'])

    payload = {
        'teams':        teams_out,
        'weeks_played': weeks_played,
        'is_h2h':       is_h2h,
    }
    cache.set(cache_key, payload, timeout=600)  # 10 minutes

    # is_user_team is already set correctly above
    return JsonResponse(payload)
