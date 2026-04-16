"""
Stripe billing helpers and views.

Flow:
  Upgrade/new sub  → create_checkout_session → Stripe Checkout → success URL
  Manage existing  → create_portal_session   → Stripe Customer Portal
  Stripe events    → stripe_webhook          → update UserProfile tier

Tier mapping:
  free  → no Stripe subscription
  pro   → STRIPE_PRICE_PRO
  elite → STRIPE_PRICE_ELITE
"""

import logging

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

# Price ID → internal tier name


def _price_to_tier_map():
    """Build lazily so settings are fully loaded."""
    return {
        settings.STRIPE_PRICE_PRO:   'pro',
        settings.STRIPE_PRICE_ELITE: 'elite',
    }


def _obj_get(obj, key, default=None):
    """Read dict-style or attribute-style Stripe objects safely."""
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, default)


def _session_belongs_to_user(session, user):
    """
    Ensure a checkout session can only affect the logged-in owner's account.
    Accepts any of these strong bindings:
      - profile.stripe_customer_id matches session.customer
      - session.client_reference_id == user.pk
      - session metadata user_id == user.pk
      - customer metadata user_id == user.pk
    """
    profile = user.profile
    has_binding = False

    customer = _obj_get(session, 'customer')
    if isinstance(customer, str):
        customer_id = customer
        customer_metadata = {}
    else:
        customer_id = _obj_get(customer, 'id', '')
        customer_metadata = _obj_get(customer, 'metadata', {}) or {}

    if profile.stripe_customer_id:
        has_binding = True
        if customer_id != profile.stripe_customer_id:
            return False

    client_reference_id = _obj_get(session, 'client_reference_id')
    if client_reference_id:
        has_binding = True
        if str(client_reference_id) != str(user.pk):
            return False

    session_metadata = _obj_get(session, 'metadata', {}) or {}
    session_user_id = session_metadata.get('user_id') if isinstance(session_metadata, dict) else None
    if session_user_id:
        has_binding = True
        if str(session_user_id) != str(user.pk):
            return False

    customer_user_id = (
        customer_metadata.get('user_id')
        if isinstance(customer_metadata, dict) else None
    )
    if customer_user_id:
        has_binding = True
        if str(customer_user_id) != str(user.pk):
            return False

    return has_binding


def _get_or_create_customer(user):
    """Return the Stripe customer for this user, creating one if needed.

    If the stored ID belongs to a different Stripe mode (e.g. a live-mode ID
    used with a test-mode key), clears the stale ID and creates a fresh one.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY
    profile = user.profile

    if profile.stripe_customer_id:
        try:
            stripe.Customer.retrieve(profile.stripe_customer_id)
            return profile.stripe_customer_id
        except stripe.error.InvalidRequestError:
            # Stale ID (wrong mode or deleted) — fall through to create a new one
            logger.warning(
                'Stale Stripe customer ID %s for user %s — creating a new one.',
                profile.stripe_customer_id, user.username,
            )
            profile.stripe_customer_id = ''
            profile.stripe_subscription_id = ''
            profile.save(update_fields=['stripe_customer_id', 'stripe_subscription_id'])

    customer = stripe.Customer.create(
        email=user.email,
        name=user.username,
        metadata={'user_id': str(user.pk)},
    )
    profile.stripe_customer_id = customer.id
    profile.save(update_fields=['stripe_customer_id'])
    return customer.id


@login_required
def create_checkout_session(request, price_id):
    """
    Start a Stripe Checkout session for the given price_id.
    Redirects the user to Stripe's hosted checkout page.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY
    valid_prices = {settings.STRIPE_PRICE_PRO, settings.STRIPE_PRICE_ELITE}

    if price_id not in valid_prices:
        messages.error(request, 'Invalid plan selected.')
        return redirect('accounts:manage_account')

    customer_id = _get_or_create_customer(request.user)

    success_url = request.build_absolute_uri(reverse('accounts:billing_success'))
    cancel_url  = request.build_absolute_uri(reverse('accounts:manage_account'))

    session = stripe.checkout.Session.create(
        customer=customer_id,
        client_reference_id=str(request.user.pk),
        metadata={'user_id': str(request.user.pk)},
        payment_method_types=['card'],
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        subscription_data={'metadata': {'user_id': str(request.user.pk)}},
        success_url=success_url + '?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=cancel_url,
        allow_promotion_codes=True,
    )
    return redirect(session.url, permanent=False)


@login_required
def create_portal_session(request):
    """
    Send an existing subscriber to the Stripe Customer Portal
    where they can update payment info, cancel, or change plan.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY
    profile = request.user.profile

    if not profile.stripe_customer_id:
        messages.error(request, 'No billing account found.')
        return redirect('accounts:manage_account')

    return_url = request.build_absolute_uri(reverse('accounts:manage_account'))
    session = stripe.billing_portal.Session.create(
        customer=profile.stripe_customer_id,
        return_url=return_url,
    )
    return redirect(session.url, permanent=False)


@login_required
def billing_success(request):
    """
    Landing page after a successful checkout.
    Directly updates the logged-in user's tier from the session so the change
    is visible immediately, without waiting for the webhook.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session_id = request.GET.get('session_id')
    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(
                session_id, expand=['subscription', 'customer']
            )
            if _obj_get(session, 'status') != 'complete':
                logger.warning('billing_success: incomplete session %s', session_id)
                messages.error(request, 'Checkout session is not complete.')
                return redirect('accounts:manage_account')

            if not _session_belongs_to_user(session, request.user):
                logger.warning(
                    'billing_success: session ownership mismatch session=%s user=%s',
                    session_id,
                    request.user.pk,
                )
                messages.error(request, 'Could not verify checkout ownership.')
                return redirect('accounts:manage_account')

            sub = session.subscription
            if sub and sub.status in ('active', 'trialing'):
                items = sub.get('items', {}).get('data', [])
                price_id = items[0].get('price', {}).get('id') if items else None
                tier = _price_to_tier_map().get(price_id)
                if tier:
                    profile = request.user.profile
                    profile.tier = tier
                    profile.stripe_subscription_id = sub.get('id', '')
                    # Ensure customer ID is stored too
                    if not profile.stripe_customer_id and session.get('customer'):
                        profile.stripe_customer_id = session['customer']
                    profile.save(update_fields=[
                        'tier', 'stripe_subscription_id', 'stripe_customer_id'
                    ])
                    logger.info(
                        'billing_success: updated %s → tier=%s',
                        request.user.username, tier,
                    )
        except Exception as exc:
            logger.warning('billing_success: could not sync subscription: %s', exc)

    messages.success(request, 'Payment successful! Your plan has been updated.')
    return redirect('accounts:manage_account')


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Receives and verifies Stripe webhook events.
    Updates UserProfile.tier based on subscription status.
    """
    stripe.api_key = settings.STRIPE_SECRET_KEY
    payload    = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        logger.warning('Stripe webhook rejected: %s', exc)
        return HttpResponse(status=400)

    _handle_event(event)
    return HttpResponse(status=200)


def _handle_event(event):
    event_type = event['type']
    data = event['data']['object']

    if event_type in (
        'customer.subscription.created',
        'customer.subscription.updated',
    ):
        _sync_subscription(data)

    elif event_type == 'customer.subscription.deleted':
        _cancel_subscription(data)

    elif event_type == 'invoice.payment_failed':
        # Optionally notify the user; tier stays until subscription is deleted
        customer_id = data.get('customer')
        logger.warning('Payment failed for Stripe customer %s', customer_id)


def _sync_subscription(subscription):
    """Set tier based on the active subscription's price.

    Supports both dict-style (webhook payload) and attribute-style
    (Stripe SDK v5+ objects) access.
    """
    def _get(obj, key, default=None):
        try:
            return obj[key]
        except (KeyError, TypeError):
            return getattr(obj, key, default)

    customer_id = _get(subscription, 'customer')
    status      = _get(subscription, 'status')

    if status not in ('active', 'trialing'):
        return

    price_id = None
    try:
        items = subscription.items.data
        if items:
            price_id = items[0].price.id
    except AttributeError:
        # Fallback for plain dict payload
        items = _get(subscription, 'items') or {}
        data  = items.get('data', []) if isinstance(items, dict) else []
        if data:
            price = data[0].get('price', {})
            price_id = price.get('id') if isinstance(price, dict) else getattr(price, 'id', None)

    tier = _price_to_tier_map().get(price_id)
    if not tier:
        logger.warning('Unknown Stripe price %s — skipping tier update', price_id)
        return

    sub_id = _get(subscription, 'id')
    _update_profile(customer_id, tier=tier, subscription_id=sub_id)


def _cancel_subscription(subscription):
    """Downgrade to free when a subscription is deleted."""
    customer_id = subscription.get('customer')
    _update_profile(customer_id, tier='free', subscription_id='')


def _update_profile(customer_id, *, tier, subscription_id):
    try:
        from accounts.models import UserProfile
        profile = UserProfile.objects.select_related('user').get(
            stripe_customer_id=customer_id
        )
        profile.tier = tier
        profile.stripe_subscription_id = subscription_id
        profile.save(update_fields=['tier', 'stripe_subscription_id'])
        logger.info(
            'Updated %s → tier=%s sub=%s',
            profile.user.username, tier, subscription_id,
        )
    except UserProfile.DoesNotExist:
        logger.warning('No profile found for Stripe customer %s', customer_id)
