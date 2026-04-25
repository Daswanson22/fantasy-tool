import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from social_django.models import UserSocialAuth

from accounts.models import AIManagerConfig, KeptPlayer, SelectedLeague
from home import views as home_views


class _FakeYahooAPI:
    def __init__(self, teams=None):
        self._teams = teams or []

    def get_mlb_teams(self):
        return self._teams

    def get_league_available_players(self, league_key, count=100, start=0, position=None):
        return []

    def get_league_player_stats(self, league_key, player_keys, stat_type):
        return {}


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class Week2SecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            username='week2user',
            email='week2@example.com',
            password='Password123!',
        )
        self.user.profile.tier = 'pro'
        self.user.profile.save(update_fields=['tier'])
        UserSocialAuth.objects.create(
            user=self.user,
            provider='yahoo-oauth2',
            uid='uid-1',
            extra_data={
                'access_token': 'token',
                'refresh_token': 'refresh',
                'expires_in': 3600,
                'auth_time': int(time.time()),
            },
        )
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_waiver_players_blocks_unauthorized_team_key(self):
        SelectedLeague.objects.create(
            user=self.user,
            team_key='mlb.l.1.t.1',
            team_name='Alpha',
            league_key='mlb.l.1',
        )
        resp = self.client.get(reverse('home:waiver_players_api'), {'key': 'mlb.l.1.t.999'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error'], 'forbidden')

    def test_waiver_players_returns_generic_error_on_backend_failure(self):
        SelectedLeague.objects.create(
            user=self.user,
            team_key='mlb.l.1.t.1',
            team_name='Alpha',
            league_key='mlb.l.1',
        )
        with patch('home.views.get_api_for_user', side_effect=Exception('SECRET_TOKEN_LEAK')):
            resp = self.client.get(reverse('home:waiver_players_api'), {'key': 'mlb.l.1.t.1'})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()['error'], 'service_unavailable')
        self.assertNotIn('SECRET_TOKEN_LEAK', resp.content.decode('utf-8'))

    def test_available_sp_is_rate_limited(self):
        SelectedLeague.objects.create(
            user=self.user,
            team_key='mlb.l.1.t.1',
            team_name='Alpha',
            league_key='mlb.l.1',
        )
        fake_api = _FakeYahooAPI()
        with patch('home.views._RATE_LIMITS', {**home_views._RATE_LIMITS, 'available_sp_api': (1, 60)}):
            with patch('home.views.get_api_for_user', return_value=fake_api):
                with patch('home.espn_api.get_probable_starters_by_date', return_value={}):
                    first = self.client.get(reverse('home:available_sp_api'), {'key': 'mlb.l.1.t.1'})
                    second = self.client.get(reverse('home:available_sp_api'), {'key': 'mlb.l.1.t.1'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()['error'], 'rate_limited')

    def test_toggle_keeper_requires_authorized_team(self):
        SelectedLeague.objects.create(
            user=self.user,
            team_key='mlb.l.1.t.1',
            team_name='Alpha',
            league_key='mlb.l.1',
        )
        resp = self.client.post(
            reverse('home:toggle_keeper'),
            {'team_key': 'mlb.l.1.t.999', 'player_key': 'mlb.p.1'},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error'], 'forbidden')
        self.assertEqual(KeptPlayer.objects.count(), 0)

    def test_save_ai_config_rate_limit(self):
        SelectedLeague.objects.create(
            user=self.user,
            team_key='mlb.l.1.t.1',
            team_name='Alpha',
            league_key='mlb.l.1',
        )
        payload = {
            'team_key': 'mlb.l.1.t.1',
            'league_format': 'roto',
            'max_hitter_moves': '1',
            'max_pitcher_moves': '1',
        }
        with patch('home.views._RATE_LIMITS', {**home_views._RATE_LIMITS, 'save_ai_config': (1, 60)}):
            first = self.client.post(reverse('home:save_ai_config'), payload)
            second = self.client.post(reverse('home:save_ai_config'), payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()['error'], 'rate_limited')
        self.assertEqual(AIManagerConfig.objects.filter(user=self.user, team_key='mlb.l.1.t.1').count(), 1)

    def test_select_league_uses_verified_team_data(self):
        fake_api = _FakeYahooAPI(
            teams=[{
                'team_key': 'mlb.l.77.t.3',
                'team_name': 'Verified Team Name',
                'league_key': 'mlb.l.77',
            }]
        )
        with patch('home.views.get_api_for_user', return_value=fake_api):
            resp = self.client.post(
                reverse('home:select_league'),
                {
                    'team_key': 'mlb.l.77.t.3',
                    'team_name': 'Tampered Name',
                    'league_key': 'mlb.l.999',
                },
            )
        self.assertEqual(resp.status_code, 302)
        obj = SelectedLeague.objects.get(user=self.user, team_key='mlb.l.77.t.3')
        self.assertEqual(obj.team_name, 'Verified Team Name')
        self.assertEqual(obj.league_key, 'mlb.l.77')
