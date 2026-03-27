from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .yahoo_api import get_api_for_user, TokenExpiredError


def index(request):
    return render(request, 'home/index.html')


def _is_hash_username(username):
    """Return True if the username looks like a Yahoo sub hash (32+ hex chars)."""
    return len(username) >= 30 and all(c in '0123456789abcdef' for c in username)


@login_required
def dashboard(request):
    user = request.user
    yahoo_connected = False
    yahoo_guid = None
    mlb_leagues = []

    try:
        social = user.social_auth.get(provider='yahoo-oauth2')
        yahoo_connected = True
        yahoo_guid = social.extra_data.get('yahoo_guid', '')

        # Fix hash username for accounts created before the pipeline fix was added
        if _is_hash_username(user.username):
            email = user.email or social.extra_data.get('email', '')
            if email:
                user.username = email.split('@')[0]
                user.save(update_fields=['username'])

        # Fetch MLB leagues to display inline on dashboard
        api = get_api_for_user(social)
        mlb_leagues = api.get_mlb_leagues()

    except TokenExpiredError:
        messages.warning(request, 'Yahoo session expired — please reconnect.')
    except Exception:
        pass  # Leagues simply won't show if the API call fails

    return render(request, 'home/dashboard.html', {
        'yahoo_connected': yahoo_connected,
        'yahoo_guid': yahoo_guid,
        'mlb_leagues': mlb_leagues,
    })


@login_required
def teams(request):
    """
    Free tier: fetch the user's first Yahoo Fantasy league and show its roster.
    """
    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
    except Exception:
        messages.error(request, 'Please connect your Yahoo Fantasy account first.')
        return redirect('home:dashboard')

    try:
        api = get_api_for_user(social)
        all_teams = api.get_user_teams()
    except TokenExpiredError:
        messages.error(request, 'Your Yahoo session expired. Please reconnect.')
        return redirect('home:dashboard')
    except Exception as e:
        messages.error(request, f'Could not load teams from Yahoo: {e}')
        return redirect('home:dashboard')

    if not all_teams:
        return render(request, 'home/teams.html', {
            'team': None,
            'roster': [],
            'error': 'No fantasy teams found on your Yahoo account.',
        })

    # Free tier: only the first team
    team = all_teams[0]

    try:
        roster = api.get_team_roster(team['team_key'])
    except Exception as e:
        roster = []
        messages.warning(request, f'Could not load roster: {e}')

    # Group roster by selected position for cleaner display
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
        'total_teams': len(all_teams),
        'is_free_tier': True,
    })
