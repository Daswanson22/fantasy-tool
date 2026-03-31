from datetime import date as date_cls, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from accounts.models import SelectedLeague
from .yahoo_api import get_api_for_user, TokenExpiredError

_staff_only = user_passes_test(lambda u: u.is_staff, login_url='/dashboard/')


def index(request):
    return render(request, 'home/index.html')


def _is_hash_username(username):
    return len(username) >= 30 and all(c in '0123456789abcdef' for c in username)


def _get_tier_info(user):
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
    team_key  = request.POST.get('team_key', '').strip()
    team_name = request.POST.get('team_name', '').strip()
    league_key = request.POST.get('league_key', '').strip()

    if not team_key:
        return redirect('home:dashboard')

    tier, league_limit = _get_tier_info(request.user)
    current_count = SelectedLeague.objects.filter(user=request.user).count()

    # Already selected
    if SelectedLeague.objects.filter(user=request.user, team_key=team_key).exists():
        return redirect('home:dashboard')

    # Tier limit reached
    if league_limit is not None and current_count >= league_limit:
        messages.error(request, 'You have already selected the maximum leagues for your plan.')
        return redirect('home:dashboard')

    SelectedLeague.objects.create(
        user=request.user,
        team_key=team_key,
        team_name=team_name,
        league_key=league_key,
    )
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
            cache.set(cache_key, roster, timeout=300)  # 5 minutes
        except Exception as e:
            roster = []
            messages.warning(request, f'Could not load roster: {e}')

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

    return render(request, 'home/teams.html', {
        'team': team,
        'batting_lineup': batting_lineup,
        'pitching_lineup': pitching_lineup,
        'bench': bench,
        'il_list': il_list,
        'roster_date': roster_date_str,
        'prev_date': prev_date,
        'next_date': next_date,
        'tier': tier,
        'locked': False,
    })
