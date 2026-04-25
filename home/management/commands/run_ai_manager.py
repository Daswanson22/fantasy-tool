"""
Django management command: run_ai_manager

Usage
-----
Dry run (safe — reads data and logs what WOULD happen, no Yahoo API writes):
    python manage.py run_ai_manager --dry-run

Live run (executes real add/drop transactions via Yahoo API):
    python manage.py run_ai_manager

Single team (dry run):
    python manage.py run_ai_manager --dry-run --team-key 423.l.12345.t.1

Single team (live):
    python manage.py run_ai_manager --team-key 423.l.12345.t.1

Notes
-----
- The command defaults to --dry-run for safety. You must explicitly omit the
  flag (or pass --no-dry-run) to execute real transactions.
- --team-key overrides the normal "run all enabled configs" behaviour and
  runs only the specified team. The team's AIManagerConfig must exist and
  have is_enabled=True (unless --force is also passed).
- All decisions are written to AITransactionLog regardless of dry-run mode,
  giving you a full audit trail you can inspect via the Django admin.
"""

from django.core.management.base import BaseCommand, CommandError
from home.ai_manager import run_all_enabled, run_for_team


class Command(BaseCommand):
    help = 'Run the AI Manager engine for all enabled teams (or a single team).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=True,
            dest='dry_run',
            help='Log planned actions without executing Yahoo API writes (default: True).',
        )
        parser.add_argument(
            '--no-dry-run',
            action='store_false',
            dest='dry_run',
            help='Execute real add/drop transactions via Yahoo API.',
        )
        parser.add_argument(
            '--team-key',
            type=str,
            default=None,
            dest='team_key',
            help='Run for a single team key instead of all enabled configs.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            default=False,
            dest='force',
            help='With --team-key: run even if is_enabled=False on the config.',
        )

    def handle(self, *args, **options):
        dry_run  = options['dry_run']
        team_key = options.get('team_key')
        force    = options.get('force', False)

        mode_label = '[DRY RUN]' if dry_run else '[LIVE]'
        self.stdout.write(f'\n{mode_label} AI Manager starting...\n')

        if team_key:
            results = self._run_single_team(team_key, dry_run, force)
        else:
            results = run_all_enabled(dry_run=dry_run)

        self._print_results(results, dry_run)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _run_single_team(self, team_key, dry_run, force):
        from accounts.models import AIManagerConfig, LeagueSettings

        try:
            config = AIManagerConfig.objects.select_related('user').get(team_key=team_key)
        except AIManagerConfig.DoesNotExist:
            raise CommandError(
                f'No AIManagerConfig found for team_key={team_key!r}. '
                f'The user must visit the teams page at least once to create it.'
            )

        if not config.is_enabled and not force:
            raise CommandError(
                f'AI Manager is disabled for {team_key!r}. '
                f'Pass --force to run anyway, or enable it in the UI.'
            )

        user = config.user
        league_key = team_key.rsplit('.t.', 1)[0]

        try:
            social_auth = user.social_auth.get(provider='yahoo-oauth2')
        except Exception:
            raise CommandError(
                f'No Yahoo OAuth2 token found for user {user.username!r}.'
            )

        league_settings = LeagueSettings.objects.filter(league_key=league_key).first()

        result = run_for_team(
            user=user,
            team_key=team_key,
            social_auth=social_auth,
            league_settings=league_settings,
            ai_config=config,
            dry_run=dry_run,
        )
        return [result]

    def _print_results(self, results, dry_run):
        if not results:
            self.stdout.write(self.style.WARNING('No enabled AI Manager configs found.\n'))
            return

        counts = {'executed': 0, 'dry_run': 0, 'no_action': 0, 'skipped': 0, 'error': 0}

        for r in results:
            decision = r['decision']
            counts[decision] = counts.get(decision, 0) + 1

            # Colour-code by decision type
            if decision == 'executed':
                style = self.style.SUCCESS
            elif decision == 'dry_run':
                style = self.style.SUCCESS
            elif decision in ('skipped', 'no_action'):
                style = self.style.WARNING
            else:  # error
                style = self.style.ERROR

            self.stdout.write(style(
                f'  [{decision.upper():10s}] {r["team_key"]}\n'
                f'             {r["reason"]}\n'
            ))

            # Print player details for dry_run and executed
            if r.get('drop_player'):
                drop = r['drop_player']
                self.stdout.write(
                    f'             DROP: {drop["name"]}'
                    f'  (drop_score={drop.get("drop_score", "?")})\n'
                )
            if r.get('add_player'):
                add = r['add_player']
                self.stdout.write(
                    f'             ADD:  {add["name"]}'
                    f'  (add_score={add.get("add_score", "?")}, '
                    f'start in {add.get("days_to_start", "?")} day(s))\n'
                )

        # Summary line
        self.stdout.write('\nSummary:\n')
        for label, count in counts.items():
            if count:
                self.stdout.write(f'  {label:10s}: {count}\n')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nThis was a DRY RUN — no Yahoo API transactions were made.\n'
                'Re-run without --dry-run to execute for real.\n'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nLive run complete.\n'))
