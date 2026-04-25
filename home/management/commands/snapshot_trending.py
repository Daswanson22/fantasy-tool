"""
Django management command: snapshot_trending

Saves a daily trending snapshot for every rostered player across all users'
selected leagues. The trending value mirrors exactly what the roster UI shows:

    trending_delta = (lastweek_pts / 6) - (lastmonth_pts / 24)

where 6 ≈ MLB games per week and 24 ≈ games per month.

Usage
-----
    python manage.py snapshot_trending

    # Dry run — prints what would be saved, writes nothing:
    python manage.py snapshot_trending --dry-run

    # Single user only:
    python manage.py snapshot_trending --user dylan

Schedule
--------
Run once per day via cron or a task scheduler:
    0 6 * * * /path/to/venv/bin/python /path/to/manage.py snapshot_trending
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand

from accounts.models import PlayerTrendingSnapshot, SelectedLeague
from home.yahoo_api import get_api_for_user, TokenExpiredError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Snapshot trending delta for every rostered player today.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            dest='dry_run',
            help='Print what would be saved without writing to the database.',
        )
        parser.add_argument(
            '--user',
            type=str,
            default=None,
            dest='username',
            help='Only snapshot leagues belonging to this username.',
        )

    def handle(self, *args, **options):
        dry_run  = options['dry_run']
        username = options.get('username')
        today    = date.today()

        mode = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(f'\n{mode}Trending snapshot — {today}\n')

        selections = (
            SelectedLeague.objects
            .select_related('user')
            .order_by('user__username', 'league_key')
        )
        if username:
            selections = selections.filter(user__username=username)

        if not selections.exists():
            self.stdout.write(self.style.WARNING('No selected leagues found.\n'))
            return

        totals = {'saved': 0, 'updated': 0, 'no_data': 0, 'errors': 0}

        current_user = None
        api = None

        for sel in selections:
            user = sel.user

            if user != current_user:
                current_user = user
                api = self._build_api(user)
                if api is None:
                    self.stdout.write(self.style.ERROR(
                        f'  [{user.username}] No Yahoo OAuth token — skipping all leagues.\n'
                    ))

            if api is None:
                totals['errors'] += 1
                continue

            team_key = sel.team_key
            self.stdout.write(f'  [{user.username}] {sel.team_name} ({team_key})\n')

            # Fetch roster + lastweek/lastmonth stats (same calls the UI makes)
            try:
                roster = api.get_team_roster(team_key)
            except TokenExpiredError:
                self.stdout.write(self.style.ERROR('    Token expired — skipping.\n'))
                totals['errors'] += 1
                api = None
                continue
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'    Roster fetch error: {e}\n'))
                totals['errors'] += 1
                continue

            if not roster:
                self.stdout.write(self.style.WARNING('    Empty roster — skipping.\n'))
                continue

            try:
                lastweek_map  = api.get_team_player_stats(team_key, stat_type='lastweek')
                lastmonth_map = api.get_team_player_stats(team_key, stat_type='lastmonth')
            except TokenExpiredError:
                self.stdout.write(self.style.ERROR('    Token expired fetching stats — skipping.\n'))
                totals['errors'] += 1
                api = None
                continue
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'    Stats fetch error: {e}\n'))
                totals['errors'] += 1
                continue

            for player in roster:
                pk          = player.get('player_key', '')
                player_name = player.get('name', pk)

                lw = lastweek_map.get(pk)
                lm = lastmonth_map.get(pk)

                delta = None
                if lw is not None and lm is not None and lm > 0:
                    delta = round((lw / 6) - (lm / 24), 1)

                delta_str = f'{delta:+.1f}' if delta is not None else 'N/A'
                lw_str    = f'{lw:.1f}'     if lw    is not None else 'N/A'
                lm_str    = f'{lm:.1f}'     if lm    is not None else 'N/A'
                self.stdout.write(
                    f'    {player_name:<28} lw={lw_str:>7}  lm={lm_str:>7}  d={delta_str:>7}\n'
                )

                if dry_run:
                    totals['no_data' if delta is None else 'saved'] += 1
                    continue

                _, created = PlayerTrendingSnapshot.objects.update_or_create(
                    user=user,
                    team_key=team_key,
                    player_key=pk,
                    date=today,
                    defaults={
                        'player_name':   player_name,
                        'lastweek_pts':  lw,
                        'lastmonth_pts': lm,
                        'trending_delta': delta,
                    },
                )

                if created:
                    totals['saved'] += 1
                else:
                    totals['updated'] += 1

        self.stdout.write('\nSummary:\n')
        self.stdout.write(f'  saved  : {totals["saved"]}\n')
        self.stdout.write(f'  updated: {totals["updated"]}\n')
        self.stdout.write(f'  no_data: {totals["no_data"]}\n')
        self.stdout.write(f'  errors : {totals["errors"]}\n')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN — nothing was written.\n'))
        else:
            self.stdout.write(self.style.SUCCESS('\nDone.\n'))

    def _build_api(self, user):
        try:
            social = user.social_auth.get(provider='yahoo-oauth2')
            return get_api_for_user(social)
        except Exception as e:
            logger.warning('snapshot_trending: cannot build API for %s – %s', user.username, e)
            return None
