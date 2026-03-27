from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .yahoo_api import get_api_for_user, TokenExpiredError


def index(request):
    return render(request, 'home/index.html')


def _is_hash_username(username):
    return len(username) >= 30 and all(c in '0123456789abcdef' for c in username)


def _get_tier_info(user):
    """Return (tier, league_limit) from the user's profile, defaulting to free."""
    try:
        profile = user.profile
        return profile.tier, profile.get_league_limit()
    except Exception:
        return 'free', 1


@login_required
def dashboard(request):
    user = request.user
    yahoo_connected = False
    yahoo_guid = None
    mlb_teams = []

    tier, league_limit = _get_tier_info(user)

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

    # Annotate each team: locked=True if beyond this user's league limit
    for i, team in enumerate(mlb_teams):
        team['locked'] = (league_limit is not None) and (i >= league_limit)

    return render(request, 'home/dashboard.html', {
        'yahoo_connected': yahoo_connected,
        'yahoo_guid': yahoo_guid,
        'mlb_teams': mlb_teams,
        'tier': tier,
        'league_limit': league_limit,
    })


@login_required
def teams(request):
    """
    Show the roster for a specific MLB team.
    Accepts ?key=<team_key> to identify which team.
    Enforces tier limits — users cannot bypass them by guessing team keys.
    """
    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
    except Exception:
        messages.error(request, 'Please connect your Yahoo Fantasy account first.')
        return redirect('home:dashboard')

    tier, league_limit = _get_tier_info(request.user)

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

    requested_key = request.GET.get('key', '').strip()

    if requested_key:
        team, team_index = None, None
        for i, t in enumerate(all_mlb_teams):
            if t['team_key'] == requested_key:
                team, team_index = t, i
                break
        if team is None:
            messages.error(request, 'Team not found.')
            return redirect('home:dashboard')
        # Tier enforcement: block access to teams beyond the limit
        if league_limit is not None and team_index >= league_limit:
            return render(request, 'home/teams.html', {
                'team': None, 'starters': [], 'bench': [],
                'locked': True, 'tier': tier,
            })
    else:
        team = all_mlb_teams[0]

    try:
        roster = api.get_team_roster(team['team_key'])
    except Exception as e:
        roster = []
        messages.warning(request, f'Could not load roster: {e}')

    starters, bench = [], []
    for player in roster:
        if player['selected_position'] == 'BN':
            bench.append(player)
        else:
            starters.append(player)

    return render(request, 'home/teams.html', {
        'team': team,
        'starters': starters,
        'bench': bench,
        'tier': tier,
        'locked': False,
    })
