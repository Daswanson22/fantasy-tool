"""
home/roster_optimizer.py

Modular roster slot optimization.

Public API
----------
promote_starters(api, team_key, league_key, date, dry_run)
    Full daily pass: promote every confirmed starter sitting on BN to an
    active slot; demote non-starters from active slots to make room.
    Used by the "Auto-promote today's starters" toggle and callable
    independently from any future feature.

place_player_in_active_slot(api, team_key, added_player, current_roster,
                             league_key, date, dry_run)
    Single-player placement: move one newly added player from BN into the
    best available active slot.  Called by the AI Manager after a successful
    add/drop so the pickup is immediately in the lineup.

Both functions return a list of RosterMove dicts and never raise — errors
are logged and the move is marked with an 'error' key.

RosterMove dict shape
---------------------
{
    'player_key':    str,
    'player_name':   str,
    'from_position': str,
    'to_position':   str,
    'reason':        str,
    'executed':      bool,   # False in dry_run or on API error
    'error':         str,    # only present when execution failed
}
"""

import logging
from datetime import date as date_cls

logger = logging.getLogger(__name__)

# Active pitcher slot types in promotion-preference order
_PITCHER_SLOT_PRIORITY = ['SP', 'P', 'RP']
_ACTIVE_PITCHER_SLOTS  = set(_PITCHER_SLOT_PRIORITY)
_IL_SLOTS              = {'IL', 'DL', 'NA'}
_BENCH_SLOT            = 'BN'


# ── Public API ────────────────────────────────────────────────────────────────

def promote_starters(api, team_key, league_key, date=None, dry_run=True):
    """
    Full daily roster optimization pass.

    Algorithm:
      1. Fetch roster and today's starting statuses for all pitchers.
      2. Identify confirmed starters sitting on BN  → need promotion.
      3. Identify non-starters occupying active slots → available for demotion.
      4. For each bench starter, find an active slot by displacing a non-starter.
         Demote the non-starter first, then promote the starter.

    This is the engine behind the "Auto-promote today's starters" toggle.
    It is also safe to call even when no add/drop happened — it is purely
    a lineup optimization and never touches the transaction API.

    Returns a (possibly empty) list of RosterMove dicts.
    """
    date_str = date or date_cls.today().isoformat()
    moves = []

    try:
        roster = api.get_team_roster(team_key, date=date_str)
    except Exception as exc:
        logger.error('promote_starters: could not fetch roster for %s: %s', team_key, exc)
        return moves

    # Fetch today's starting status for all pitchers on the roster
    pitcher_keys = [
        p['player_key'] for p in roster
        if p.get('position_type') == 'P' and p.get('player_key')
    ]
    starting_map = {}
    if pitcher_keys:
        try:
            starting_map = api.get_player_starting_statuses(
                league_key, pitcher_keys, date=date_str
            )
        except Exception as exc:
            logger.warning('promote_starters: could not fetch starting statuses: %s', exc)

    for p in roster:
        if p['player_key'] in starting_map:
            p['is_starting'] = starting_map[p['player_key']]

    # Players confirmed starting today who are stuck on the bench
    starters_on_bench = [
        p for p in roster
        if p.get('is_starting') is True
        and p.get('selected_position') == _BENCH_SLOT
    ]

    if not starters_on_bench:
        logger.info('promote_starters: no bench starters to promote for %s', team_key)
        return moves

    # Active-slot pitchers who are NOT starting today — eligible for demotion
    non_starters_in_active = [
        p for p in roster
        if p.get('is_starting') is not True
        and p.get('selected_position') in _ACTIVE_PITCHER_SLOTS
    ]

    # Work on a mutable copy so we track which displaceables are still available
    # as we process multiple promotions in one pass
    available_to_displace = list(non_starters_in_active)

    for starter in starters_on_bench:
        eligible = _eligible_pitcher_slots(starter)
        slot, to_displace = _find_slot(eligible, available_to_displace)

        if slot is None:
            logger.info(
                'promote_starters: no available slot for %s (eligible=%s, no displaceable non-starters)',
                starter['name'], eligible,
            )
            continue

        # Demote the current occupant first so the slot is free
        if to_displace:
            move = _execute_move(
                api, team_key, to_displace, _BENCH_SLOT,
                reason=f'Demoted to BN — not starting today; making room for {starter["name"]}',
                date=date_str, dry_run=dry_run,
            )
            moves.append(move)
            available_to_displace.remove(to_displace)

        # Promote the confirmed starter
        move = _execute_move(
            api, team_key, starter, slot,
            reason=f'Promoted to {slot} — confirmed starter today',
            date=date_str, dry_run=dry_run,
        )
        moves.append(move)

    return moves


def place_player_in_active_slot(api, team_key, added_player, current_roster,
                                 league_key, date=None, dry_run=True):
    """
    Move a newly added player from BN into the best available active slot.

    Called by the AI Manager immediately after a successful add/drop.
    Uses the roster snapshot taken before the add to find a displaceable
    slot occupant, avoiding an extra API round-trip.

    Displacement rule: an active-slot pitcher is only displaced if they are
    NOT confirmed starting today.  A confirmed starter is never bumped.

    Args:
        added_player:    dict with at least player_key, name, eligible_positions,
                         and optionally days_to_start (for the log message).
        current_roster:  roster list fetched before the add (used for slot analysis).

    Returns a list of 0–2 RosterMove dicts (demotion + promotion, or just
    promotion if the target slot was empty).
    """
    date_str = date or date_cls.today().isoformat()
    moves = []

    eligible = _eligible_pitcher_slots(added_player)
    if not eligible:
        logger.info(
            'place_player_in_active_slot: %s has no eligible active pitcher slots '
            '(eligible_positions=%s) — leaving on BN',
            added_player.get('name'), added_player.get('eligible_positions'),
        )
        return moves

    # Fetch starting status only for pitchers currently in active slots
    # so we know who is safe to displace
    active_pitchers = [
        p for p in current_roster
        if p.get('selected_position') in _ACTIVE_PITCHER_SLOTS
        and p.get('player_key')
    ]
    if active_pitchers:
        try:
            keys = [p['player_key'] for p in active_pitchers]
            starting_map = api.get_player_starting_statuses(league_key, keys, date=date_str)
            for p in active_pitchers:
                if p['player_key'] in starting_map:
                    p['is_starting'] = starting_map[p['player_key']]
        except Exception as exc:
            logger.warning(
                'place_player_in_active_slot: could not fetch starting statuses: %s', exc
            )

    # Only displace players who are NOT confirmed starters today
    displaceable = [p for p in active_pitchers if p.get('is_starting') is not True]

    slot, to_displace = _find_slot(eligible, displaceable)

    if slot is None:
        logger.info(
            'place_player_in_active_slot: no available slot for %s — '
            'all eligible active slots are occupied by confirmed starters',
            added_player.get('name'),
        )
        return moves

    # Demote the occupant first
    if to_displace:
        move = _execute_move(
            api, team_key, to_displace, _BENCH_SLOT,
            reason=(
                f'Demoted to BN — not starting today; '
                f'making room for newly added {added_player["name"]}'
            ),
            date=date_str, dry_run=dry_run,
        )
        moves.append(move)

    days = added_player.get('days_to_start')
    start_note = f'start in {days} day(s)' if days is not None else 'probable start upcoming'
    move = _execute_move(
        api, team_key, added_player, slot,
        reason=f'Promoted to {slot} — added by AI Manager ({start_note})',
        date=date_str, dry_run=dry_run,
    )
    moves.append(move)

    return moves


# ── Private helpers ───────────────────────────────────────────────────────────

def _eligible_pitcher_slots(player):
    """
    Return the active pitcher slots this player is eligible for,
    in promotion-preference order (SP > P > RP).
    """
    raw = player.get('eligible_positions', '')
    eligible = {pos.strip() for pos in raw.split(',')}
    return [slot for slot in _PITCHER_SLOT_PRIORITY if slot in eligible]


def _find_slot(eligible_slots, displaceable_players):
    """
    Find the best active slot from eligible_slots that has a displaceable occupant.

    Returns (slot_str, player_to_displace) or (None, None) if nothing is available.

    Slot preference follows _PITCHER_SLOT_PRIORITY (SP first).
    Among multiple displaceable players in the same slot type, the first is chosen
    (Yahoo only allows one player per slot type in most leagues, but some have two
    SP slots — in that case we displace the first non-starter found).
    """
    slot_to_displaceable: dict[str, list] = {}
    for p in displaceable_players:
        pos = p.get('selected_position', '')
        if pos in _ACTIVE_PITCHER_SLOTS:
            slot_to_displaceable.setdefault(pos, []).append(p)

    for slot in eligible_slots:
        occupants = slot_to_displaceable.get(slot, [])
        if occupants:
            return (slot, occupants[0])

    return (None, None)


def _execute_move(api, team_key, player, to_position, reason, date, dry_run):
    """
    Execute a single `set_roster_position` call and return a RosterMove dict.
    Failures are caught and recorded in the move's 'error' key.
    """
    from_position = player.get('selected_position', '?')
    move = {
        'player_key':    player['player_key'],
        'player_name':   player.get('name', player['player_key']),
        'from_position': from_position,
        'to_position':   to_position,
        'reason':        reason,
        'executed':      False,
    }

    if dry_run:
        logger.info(
            '[DRY RUN] roster move: %s  %s → %s  (%s)',
            move['player_name'], from_position, to_position, reason,
        )
        return move

    try:
        from home.yahoo_api import YahooAPIError
        api.set_roster_position(team_key, player['player_key'], to_position, date=date)
        move['executed'] = True
        logger.info(
            'Roster move: %s  %s → %s', move['player_name'], from_position, to_position,
        )
    except Exception as exc:
        move['error'] = str(exc)
        logger.error(
            'Roster move failed: %s  %s → %s  error=%s',
            move['player_name'], from_position, to_position, exc,
        )

    return move
