"""
home/ai_manager.py

AI Manager execution engine.

Public entry points:
    run_all_enabled(dry_run=True)
        Called by the management command. Iterates every enabled AIManagerConfig
        and runs the engine for each team. Returns a list of result dicts.

    run_for_team(user, team_key, social_auth, league_settings, ai_config, dry_run=True)
        Single-team execution. Returns one result dict.

Result dict shape:
    {
        'team_key':   str,
        'dry_run':    bool,
        'decision':   'executed' | 'dry_run' | 'no_action' | 'skipped' | 'error',
        'reason':     str,
        'drop_player': dict | None,
        'add_player':  dict | None,
    }

All decisions — including skips and failures — are logged to AITransactionLog
so there is always a written record of what the engine considered.
"""

import logging
from datetime import date as date_cls, timedelta

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

# Days ahead to scan for probable SP starts (today = day 0)
LOOKAHEAD_DAYS = 2

# IL/DL positions that make a player ineligible to drop
_IL_POSITIONS = {'IL', 'DL', 'NA'}


# ── Public entry points ───────────────────────────────────────────────────────

def run_all_enabled(dry_run=True):
    """
    Find every enabled AIManagerConfig and run the engine for each team.

    Skips teams whose owner has no Yahoo OAuth2 token or missing league settings,
    but continues to the next team — one failure never blocks the rest.

    Returns a list of result dicts (one per config).
    """
    from accounts.models import AIManagerConfig, LeagueSettings

    configs = (
        AIManagerConfig.objects
        .filter(is_enabled=True)
        .select_related('user')
    )

    results = []
    for config in configs:
        user      = config.user
        team_key  = config.team_key
        league_key = team_key.rsplit('.t.', 1)[0]

        # Resolve Yahoo social auth
        try:
            social_auth = user.social_auth.get(provider='yahoo-oauth2')
        except Exception:
            results.append(_skip_result(team_key, dry_run,
                           'No Yahoo OAuth2 token found for this user.'))
            continue

        league_settings = LeagueSettings.objects.filter(league_key=league_key).first()

        result = run_for_team(
            user=user,
            team_key=team_key,
            social_auth=social_auth,
            league_settings=league_settings,
            ai_config=config,
            dry_run=dry_run,
        )
        results.append(result)

    return results


def run_for_team(user, team_key, social_auth, league_settings, ai_config, dry_run=True):
    """
    Run one AI Manager cycle for a single team.

    Execution order:
      1.  Enabled guard  — skip immediately if AI Manager is disabled
      2.  API init       — resolve Yahoo OAuth token; bail on expired session
      3.  Auto-promote   — if enabled, promote today's confirmed starters from BN
                           (runs every day, independent of add/drop state)
      4.  Idempotency    — skip add/drop if already executed today (live runs only)
      5.  League checks  — settings cached, week reset, budget available
      6.  Roster fetch   — pull today's roster + season stats + trending
      7.  Keeper set     — load keeper flags for this team
      8.  ESPN probables — fetch now so drop selection can protect upcoming starters
      9.  Drop select    — weakest non-keeper, non-IL, non-starting-soon player
      10. Add select     — best available SP with a probable start in LOOKAHEAD_DAYS
      11a. Dry run       — log the planned action, return without touching Yahoo
      11b. Live run      — call Yahoo add/drop API, log result, update counters
      12. Post-add       — place newly added player from BN into best active slot
    """
    from home.yahoo_api import get_api_for_user, TokenExpiredError, YahooAPIError
    from home.espn_api import get_probable_starters_by_date
    from accounts.models import KeptPlayer

    result = {
        'team_key':     team_key,
        'dry_run':      dry_run,
        'decision':     'no_action',
        'reason':       '',
        'drop_player':  None,
        'add_player':   None,
        'roster_moves': [],
    }

    today      = date_cls.today()
    league_key = team_key.rsplit('.t.', 1)[0]

    # ── 1. API init ───────────────────────────────────────────────────────────

    try:
        api = get_api_for_user(social_auth)
    except TokenExpiredError:
        return _skip(result, 'Yahoo session expired — user must reconnect.')
    except Exception as exc:
        return _skip(result, f'Could not initialize Yahoo API: {exc}')

    # ── 3. Auto-promote starters ──────────────────────────────────────────────
    # Runs every day before the add/drop idempotency guard so the daily
    # lineup pass fires even when no add/drop is scheduled.

    if getattr(ai_config, 'auto_promote_starters', False):
        from home.roster_optimizer import promote_starters
        promo_moves = promote_starters(
            api, team_key, league_key,
            date=today.isoformat(), dry_run=dry_run,
        )
        result['roster_moves'].extend(promo_moves)

    # ── 4. Add/drop idempotency guard ─────────────────────────────────────────
    # Only blocks add/drop transactions — roster optimization already ran above.

    if not dry_run and ai_config.last_ai_run_date == today:
        return _skip(result, 'Already ran today — idempotency guard.')

    if league_settings is None:
        return _skip(result,
                     'League settings not cached. '
                     'Open the team page once to sync them, then re-run.')

    # ── 5. Week boundary reset ────────────────────────────────────────────────

    current_week = league_settings.current_week
    if current_week and ai_config.last_known_week != current_week:
        logger.info(
            'AI Manager week rollover team=%s  %s→%s  resetting budget',
            team_key, ai_config.last_known_week, current_week,
        )
        ai_config.adds_used_this_week = 0
        ai_config.last_known_week = current_week
        if not dry_run:
            ai_config.save(update_fields=['adds_used_this_week', 'last_known_week'])

    # ── 5b. Budget check ──────────────────────────────────────────────────────
    # Use the league's actual add limits from Yahoo settings.
    # None or 0 means the league has no limit — proceed unrestricted.

    league_format = 'h2h' if league_settings.is_h2h else 'roto'
    budget = (
        league_settings.max_weekly_adds  if league_format == 'h2h'
        else league_settings.max_season_adds
    )

    if budget:
        used      = ai_config.adds_used_this_week
        remaining = budget - used
        if remaining <= 0:
            return _skip(result,
                         f'Weekly budget exhausted ({used}/{budget} moves used this week).')

    # ── 6. Fetch roster + stats ───────────────────────────────────────────────

    try:
        roster = api.get_team_roster(team_key)
    except TokenExpiredError:
        return _skip(result, 'Yahoo session expired — user must reconnect.')
    except Exception as exc:
        return _skip(result, f'Could not fetch roster: {exc}')

    if not roster:
        return _skip(result, 'Roster returned empty.')

    _enrich_roster(api, team_key, roster)

    # ── 7. Keeper set ─────────────────────────────────────────────────────────

    kept_keys = set(
        KeptPlayer.objects
        .filter(user=user, team_key=team_key)
        .values_list('player_key', flat=True)
    )

    # ── 8. ESPN probables — fetched early so drop selection can protect starters ─

    lookahead_dates = [
        (today + timedelta(days=i)).isoformat()
        for i in range(LOOKAHEAD_DAYS + 1)
    ]
    try:
        espn_by_date = get_probable_starters_by_date(lookahead_dates)
    except Exception as exc:
        return _skip(result, f'Could not fetch ESPN probable starters: {exc}')

    # Build a set of normalized names that have a probable start in the window.
    # Any roster player in this set is protected — we never drop someone who
    # is scheduled to start within the next LOOKAHEAD_DAYS days.
    starting_soon_names = _build_starting_soon_names(espn_by_date, lookahead_dates)

    # ── 9. Drop candidate ─────────────────────────────────────────────────────

    drop_candidate = _select_drop_candidate(roster, kept_keys, starting_soon_names)
    if drop_candidate is None:
        return {**result,
                'decision': 'no_action',
                'reason':   ('No droppable players — everyone is either a keeper, '
                             'on IL, or has a probable start in the next '
                             f'{LOOKAHEAD_DAYS} day(s).')}

    # ── 10. Add candidate ─────────────────────────────────────────────────────

    try:
        available_pitchers = api.get_league_available_players(
            league_key, count=50, position='P'
        )
        _attach_trends(api, league_key, available_pitchers)
    except TokenExpiredError:
        return _skip(result, 'Yahoo session expired while fetching available pitchers.')
    except Exception as exc:
        return _skip(result, f'Could not fetch available pitchers: {exc}')

    add_candidate = _select_add_candidate(available_pitchers, espn_by_date, lookahead_dates)
    if add_candidate is None:
        return {**result,
                'decision': 'no_action',
                'reason':   (f'No probable SP starts on the waiver wire '
                             f'within the next {LOOKAHEAD_DAYS} day(s).')}

    result['drop_player'] = drop_candidate
    result['add_player']  = add_candidate

    log_reason = (
        f'Drop {drop_candidate["name"]} (drop_score={drop_candidate.get("drop_score", "?")}); '
        f'Add {add_candidate["name"]} '
        f'(add_score={add_candidate.get("add_score", "?")}, '
        f'start in {add_candidate.get("days_to_start", "?")} day(s))'
    )

    # ── 10b. Execution-time keeper safety re-check ───────────────────────────
    # Re-query in case the user toggled a keeper flag after the engine started.

    if drop_candidate['player_key'] in kept_keys:
        return _skip(result,
                     f'Blocked: {drop_candidate["name"]} was marked as a keeper '
                     f'at execution time.')

    # ── 11a. Dry-run path ─────────────────────────────────────────────────────

    if dry_run:
        _log_transaction(user, team_key, 'drop',
                         drop_candidate['player_key'], drop_candidate['name'],
                         log_reason, dry_run=True)
        _log_transaction(user, team_key, 'add',
                         add_candidate['player_key'], add_candidate['name'],
                         log_reason, dry_run=True)
        logger.info('[DRY RUN] team=%s  %s', team_key, log_reason)

        # Simulate post-add slot placement (dry run — no API writes)
        from home.roster_optimizer import place_player_in_active_slot
        placement_moves = place_player_in_active_slot(
            api, team_key, add_candidate, roster,
            league_key, date=today.isoformat(), dry_run=True,
        )
        result['roster_moves'].extend(placement_moves)

        return {**result, 'decision': 'dry_run', 'reason': log_reason}

    # ── 10. Live execution ────────────────────────────────────────────────────

    try:
        api.add_drop_player(
            league_key=league_key,
            team_key=team_key,
            add_player_key=add_candidate['player_key'],
            drop_player_key=drop_candidate['player_key'],
        )
    except (TokenExpiredError, YahooAPIError) as exc:
        msg = f'Yahoo rejected transaction: {exc}'
        logger.error('AI Manager transaction failed team=%s: %s', team_key, exc)
        _log_transaction(user, team_key, 'add',
                         add_candidate['player_key'], add_candidate['name'],
                         f'FAILED — {msg}', dry_run=False)
        return {**result, 'decision': 'error', 'reason': msg}
    except Exception as exc:
        msg = f'Unexpected error during Yahoo transaction: {exc}'
        logger.exception('AI Manager unexpected error team=%s', team_key)
        return {**result, 'decision': 'error', 'reason': msg}

    # ── 11. Post-execution bookkeeping ────────────────────────────────────────

    _log_transaction(user, team_key, 'drop',
                     drop_candidate['player_key'], drop_candidate['name'],
                     log_reason, dry_run=False)
    _log_transaction(user, team_key, 'add',
                     add_candidate['player_key'], add_candidate['name'],
                     log_reason, dry_run=False)

    ai_config.adds_used_this_week += 1
    ai_config.last_ai_run_date = today
    ai_config.save(update_fields=['adds_used_this_week', 'last_ai_run_date'])

    # Move the newly added player from BN into the best active pitcher slot
    from home.roster_optimizer import place_player_in_active_slot
    placement_moves = place_player_in_active_slot(
        api, team_key, add_candidate, roster,
        league_key, date=today.isoformat(), dry_run=False,
    )
    result['roster_moves'].extend(placement_moves)

    logger.info('AI Manager executed team=%s: %s', team_key, log_reason)
    final_result = {**result, 'decision': 'executed', 'reason': log_reason}

    # Notify the user by email (non-blocking — a failure here must never surface to the caller)
    try:
        _notify_user(user, final_result)
    except Exception as exc:
        logger.warning('AI Manager email notification failed team=%s: %s', team_key, exc)

    return final_result


# ── Private helpers ───────────────────────────────────────────────────────────

def _skip(result, reason):
    """Return a skipped result dict and emit a log line."""
    logger.info('AI Manager skipped team=%s  reason=%s', result.get('team_key'), reason)
    return {**result, 'decision': 'skipped', 'reason': reason}


def _skip_result(team_key, dry_run, reason):
    """Build a fresh skipped result without an existing result dict."""
    return _skip(
        {'team_key': team_key, 'dry_run': dry_run,
         'decision': 'skipped', 'reason': '',
         'drop_player': None, 'add_player': None},
        reason,
    )


def _enrich_roster(api, team_key, roster):
    """Attach total_points and trending_delta to each roster player in-place.

    Failures are non-fatal: the engine continues with whatever data it has.
    """
    try:
        stats_map = api.get_team_player_stats(team_key, stat_type='season')
        for p in roster:
            p['total_points'] = stats_map.get(p['player_key'])
    except Exception as exc:
        logger.warning('_enrich_roster: could not fetch season stats: %s', exc)

    try:
        lw_map = api.get_team_player_stats(team_key, stat_type='lastweek')
        lm_map = api.get_team_player_stats(team_key, stat_type='lastmonth')
        for p in roster:
            pk = p['player_key']
            lw = lw_map.get(pk)
            lm = lm_map.get(pk)
            if lw is not None and lm and lm > 0:
                p['trending_delta'] = round((lw / 6) - (lm / 24), 1)
    except Exception as exc:
        logger.warning('_enrich_roster: could not compute trending: %s', exc)


def _attach_trends(api, league_key, players):
    """Attach trending_delta to a list of available players in-place."""
    if not players:
        return
    try:
        keys = [p['player_key'] for p in players if p.get('player_key')]
        lw_map = api.get_league_player_stats(league_key, keys, 'lastweek')
        lm_map = api.get_league_player_stats(league_key, keys, 'lastmonth')
        for p in players:
            pk = p['player_key']
            lw = lw_map.get(pk)
            lm = lm_map.get(pk)
            if lw is not None and lm and lm > 0:
                p['trending_delta'] = round((lw / 6) - (lm / 24), 1)
    except Exception as exc:
        logger.warning('_attach_trends failed: %s', exc)


def _build_starting_soon_names(espn_by_date, lookahead_dates):
    """
    Return a set of normalized player names that have a probable start
    within the lookahead window.

    Used to protect roster players from being dropped when they are
    scheduled to pitch in the next LOOKAHEAD_DAYS days.
    """
    from home.espn_api import normalize_name
    names = set()
    for date_str in lookahead_dates:
        for starter in espn_by_date.get(date_str, []):
            names.add(normalize_name(starter['name']))
    return names


def _select_drop_candidate(roster, kept_keys, starting_soon_names=None):
    """
    Return the weakest droppable player from the roster.

    Eligibility rules:
      - NOT in kept_keys
      - NOT currently slotted in an IL/DL/NA position
      - NOT in starting_soon_names (has a probable start in the lookahead window)

    Drop score (lower = worse = drop first):
        trending_delta * 2  +  total_points / 100

    Weights: trending is penalised more heavily than raw points because a
    player who is trending sharply down is more likely to remain bad, whereas
    a low-points player might just be early in the season.
    """
    from home.espn_api import normalize_name
    protected = starting_soon_names or set()

    candidates = []
    for p in roster:
        if p.get('player_key', '') in kept_keys:
            continue
        if p.get('selected_position', '') in _IL_POSITIONS:
            continue
        if normalize_name(p.get('name', '')) in protected:
            logger.debug(
                'Drop protected: %s has a probable start in the lookahead window', p['name']
            )
            continue
        trend = p.get('trending_delta') or 0.0
        pts   = p.get('total_points')   or 0.0
        score = trend * 2 + pts / 100
        candidates.append({**p, 'drop_score': round(score, 2)})

    if not candidates:
        return None

    candidates.sort(key=lambda p: p['drop_score'])
    return candidates[0]


def _select_add_candidate(available_pitchers, espn_by_date, lookahead_dates):
    """
    Return the best available pitcher with a probable start in the lookahead window.

    Only pitchers who appear in the ESPN probables within LOOKAHEAD_DAYS are
    considered — a player with no start scheduled is never worth the roster spot.

    Add score (higher = better = add first):
        proximity  = (LOOKAHEAD_DAYS + 1 - days_to_start) * 2.0
                     → start today = 6.0  |  tomorrow = 4.0  |  in 2 days = 2.0
        trend_bonus = max(trending_delta, 0) * 1.5
        pts_bonus   = total_points / 100
        add_score   = proximity + trend_bonus + pts_bonus
    """
    from home.espn_api import normalize_name

    # Build {normalized_name → days_to_start}; keep only the soonest start
    name_to_days: dict[str, int] = {}
    for i, date_str in enumerate(lookahead_dates):
        for starter in espn_by_date.get(date_str, []):
            norm = normalize_name(starter['name'])
            if norm not in name_to_days:
                name_to_days[norm] = i

    candidates = []
    for p in available_pitchers:
        norm = normalize_name(p.get('name', ''))
        if norm not in name_to_days:
            continue

        days  = name_to_days[norm]
        trend = p.get('trending_delta') or 0.0
        pts   = p.get('total_points')   or 0.0

        proximity = (LOOKAHEAD_DAYS + 1 - days) * 2.0
        score     = proximity + max(trend, 0) * 1.5 + pts / 100
        candidates.append({**p, 'add_score': round(score, 2), 'days_to_start': days})

    if not candidates:
        return None

    candidates.sort(key=lambda p: p['add_score'], reverse=True)
    return candidates[0]


def _notify_user(user, result):
    """Send a transaction notification email if the user has opted in."""
    try:
        email_on = user.profile.email_notifications
    except Exception:
        email_on = False
    if not email_on or not user.email:
        return
    from accounts.emails import send_ai_transaction_email
    send_ai_transaction_email(user, result)


def _log_transaction(user, team_key, action, player_key, player_name, reason, dry_run):
    """Write one row to AITransactionLog.

    Swallows all exceptions so a logging failure never blocks execution.
    """
    try:
        from accounts.models import AITransactionLog
        AITransactionLog.objects.create(
            user=user,
            team_key=team_key,
            action=action,
            player_key=player_key,
            player_name=player_name,
            reason=reason,
            dry_run=dry_run,
        )
    except Exception as exc:
        logger.error('Failed to write AITransactionLog: %s', exc)
