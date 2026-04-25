import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.backends import YahooFantasyOAuth2


class _StripeObj(dict):
    """Minimal Stripe-like object supporting attr and dict access."""

    def __getattr__(self, item):
        return self.get(item)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    STRIPE_PRICE_PRO='price_pro_test',
    STRIPE_PRICE_ELITE='price_elite_test',
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
)
class StagingVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user_model = get_user_model()
        self.user = self.user_model.objects.create_user(
            username='staginguser',
            email='staging@example.com',
            password='Password123!',
        )
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def test_yahoo_backend_uses_verified_id_token_claims(self):
        backend = YahooFantasyOAuth2.__new__(YahooFantasyOAuth2)
        claims = {'sub': 'abc123', 'email': 'staging@example.com'}
        response = {'id_token': 'fake.jwt.token', 'xoauth_yahoo_guid': 'guid-1'}

        with patch('accounts.backends._validate_id_token', return_value=claims):
            data = YahooFantasyOAuth2.user_data(backend, 'token', response=response)

        self.assertEqual(data['sub'], 'abc123')
        self.assertEqual(data['email'], 'staging@example.com')
        self.assertEqual(data['xoauth_yahoo_guid'], 'guid-1')

    def test_yahoo_debug_route_is_not_exposed_when_debug_false(self):
        with override_settings(DEBUG=False):
            resp = self.client.get('/accounts/yahoo-debug/')
        self.assertEqual(resp.status_code, 404)

    def test_stripe_checkout_success_updates_tier_for_owner_only(self):
        profile = self.user.profile
        profile.tier = 'free'
        profile.stripe_customer_id = 'cus_owner_123'
        profile.save(update_fields=['tier', 'stripe_customer_id'])

        session = _StripeObj(
            status='complete',
            customer='cus_owner_123',
            client_reference_id=str(self.user.pk),
            metadata={'user_id': str(self.user.pk)},
            subscription=_StripeObj(
                status='active',
                id='sub_123',
                items={'data': [{'price': {'id': 'price_pro_test'}}]},
            ),
        )

        with patch('accounts.billing.stripe.checkout.Session.retrieve', return_value=session):
            resp = self.client.get(
                reverse('accounts:billing_success'),
                {'session_id': 'cs_test_ok'},
            )

        self.assertEqual(resp.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.tier, 'pro')
        self.assertEqual(profile.stripe_subscription_id, 'sub_123')

    def test_tampered_session_id_does_not_change_tier(self):
        profile = self.user.profile
        profile.tier = 'free'
        profile.stripe_customer_id = 'cus_owner_123'
        profile.save(update_fields=['tier', 'stripe_customer_id'])

        attacker = self.user_model.objects.create_user(
            username='attacker',
            email='attacker@example.com',
            password='Password123!',
        )
        session = _StripeObj(
            status='complete',
            customer='cus_other_999',
            client_reference_id=str(attacker.pk),
            metadata={'user_id': str(attacker.pk)},
            subscription=_StripeObj(
                status='active',
                id='sub_attacker',
                items={'data': [{'price': {'id': 'price_pro_test'}}]},
            ),
        )

        with patch('accounts.billing.stripe.checkout.Session.retrieve', return_value=session):
            resp = self.client.get(
                reverse('accounts:billing_success'),
                {'session_id': 'cs_test_tampered'},
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Could not verify checkout ownership.')
        profile.refresh_from_db()
        self.assertEqual(profile.tier, 'free')
        self.assertEqual(profile.stripe_subscription_id, '')

    def test_otp_send_is_rate_limited(self):
        self.client.logout()
        payload = {'email': self.user.email}
        login_url = reverse('accounts:login')

        for _ in range(5):
            resp = self.client.post(login_url, payload)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, '6-digit code')

        blocked = self.client.post(login_url, payload)
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, 'Too many code requests. Please wait and try again.')

    def test_otp_verify_is_rate_limited(self):
        self.client.logout()
        login_url = reverse('accounts:login')
        session = self.client.session
        session['login_email'] = self.user.email
        session['login_otp'] = '123456'
        session['login_otp_expires'] = time.time() + 600
        session.save()

        for _ in range(10):
            resp = self.client.post(login_url, {'code': '000000'})
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'Invalid or expired code')

        blocked = self.client.post(login_url, {'code': '000000'})
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, 'Too many code attempts. Please wait and try again.')
