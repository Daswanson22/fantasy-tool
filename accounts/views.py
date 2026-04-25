import random
import secrets
import time

from django.shortcuts import render, redirect
from django.http import HttpResponse, Http404
from django.contrib import messages
from django.contrib.auth import login, get_user_model
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.conf import settings as django_settings
from django.urls import reverse
from .emails import send_otp_email, send_email_change_verification
from .forms import SignUpForm, LoginEmailForm, LoginCodeForm, UsernameForm, EmailChangeForm
from .models import PendingEmailChange

User = get_user_model()

_OTP_TTL = 600  # 10 minutes
_OTP_SEND_WINDOW = 900
_OTP_VERIFY_WINDOW = 900
_OTP_SEND_LIMIT = 5
_OTP_VERIFY_LIMIT = 10


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _rate_key(prefix, request, email=''):
    return f'{prefix}:{_client_ip(request)}:{(email or "").strip().lower()}'


def _is_rate_limited(key, limit):
    return int(cache.get(key, 0) or 0) >= limit


def _bump_rate_limit(key, timeout):
    if cache.add(key, 1, timeout=timeout):
        return
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=timeout)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home:dashboard')

    # --- step 2: verify code ---
    if request.method == 'POST' and 'code' in request.POST:
        form = LoginCodeForm(request.POST)
        email = request.session.get('login_email', '')
        verify_key = _rate_key('otp:verify', request, email)
        if _is_rate_limited(verify_key, _OTP_VERIFY_LIMIT):
            messages.error(request, 'Too many code attempts. Please wait and try again.')
            return render(request, 'accounts/login.html', {
                'step': 'verify',
                'code_form': form,
                'email': email,
            })

        if form.is_valid():
            entered = form.cleaned_data['code'].strip()
            stored = request.session.get('login_otp')
            expires = request.session.get('login_otp_expires', 0)
            if stored and secrets.compare_digest(str(entered), str(stored)) and time.time() < expires:
                try:
                    user = User.objects.get(email__iexact=email)
                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    cache.delete(verify_key)
                    # clean up session keys
                    for k in ('login_otp', 'login_otp_expires', 'login_email'):
                        request.session.pop(k, None)
                    return redirect('home:dashboard')
                except User.DoesNotExist:
                    _bump_rate_limit(verify_key, _OTP_VERIFY_WINDOW)
                    messages.error(request, 'No account found for that email.')
            else:
                _bump_rate_limit(verify_key, _OTP_VERIFY_WINDOW)
                messages.error(request, 'Invalid or expired code. Please try again.')
        return render(request, 'accounts/login.html', {
            'step': 'verify',
            'code_form': form,
            'email': email,
        })

    # --- step 1: submit email ---
    if request.method == 'POST':
        form = LoginEmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            send_key = _rate_key('otp:send', request, email)
            if _is_rate_limited(send_key, _OTP_SEND_LIMIT):
                messages.error(request, 'Too many code requests. Please wait and try again.')
                return render(request, 'accounts/login.html', {'step': 'email', 'email_form': form})

            if not User.objects.filter(email__iexact=email).exists():
                _bump_rate_limit(send_key, _OTP_SEND_WINDOW)
                messages.error(request, 'No account found with that email address.')
                return render(request, 'accounts/login.html', {'step': 'email', 'email_form': form})

            code = f'{random.SystemRandom().randint(0, 999999):06d}'
            request.session['login_otp'] = code
            request.session['login_otp_expires'] = time.time() + _OTP_TTL
            request.session['login_email'] = email
            cache.delete(_rate_key('otp:verify', request, email))

            send_otp_email(email, code)
            _bump_rate_limit(send_key, _OTP_SEND_WINDOW)
            return render(request, 'accounts/login.html', {
                'step': 'verify',
                'code_form': LoginCodeForm(),
                'email': email,
            })
        return render(request, 'accounts/login.html', {'step': 'email', 'email_form': form})

    return render(request, 'accounts/login.html', {
        'step': 'email',
        'email_form': LoginEmailForm(),
    })


def complete_yahoo_registration(request):
    """
    Shown after Yahoo OAuth succeeds for a brand-new user.
    Collects username, email, and password, then resumes the social auth
    pipeline by POSTing to /auth/complete/yahoo-oauth2/.
    """
    if request.user.is_authenticated:
        return redirect('home:dashboard')

    partial_token = request.GET.get('partial_token', '')
    if not partial_token:
        return redirect('accounts:signup')

    errors = request.session.pop('registration_errors', [])
    prefill = request.session.pop('registration_prefill', {})

    return render(request, 'accounts/complete_registration.html', {
        'partial_token': partial_token,
        'errors': errors,
        'prefill': prefill,
    })


def signup(request):
    if request.user.is_authenticated:
        return redirect('home:index')

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, f'Welcome to Fantasy Tool, {user.username}!')
            return redirect('home:index')
    else:
        form = SignUpForm()

    return render(request, 'accounts/signup.html', {'form': form})


@login_required
def manage_account(request):
    username_form = UsernameForm(instance=request.user, current_user=request.user)
    email_form = EmailChangeForm(current_user=request.user, initial={'email': request.user.email})

    if request.method == 'POST':
        if 'save_username' in request.POST:
            username_form = UsernameForm(request.POST, instance=request.user, current_user=request.user)
            if username_form.is_valid():
                username_form.save()
                messages.success(request, 'Username updated successfully.')
                return redirect('accounts:manage_account')

        elif 'save_notifications' in request.POST:
            profile = request.user.profile
            profile.email_notifications = 'email_notifications' in request.POST
            profile.save(update_fields=['email_notifications'])
            messages.success(request, 'Notification preferences saved.')
            return redirect('accounts:manage_account')

        elif 'request_email_change' in request.POST:
            email_form = EmailChangeForm(request.POST, current_user=request.user)
            if email_form.is_valid():
                new_email = email_form.cleaned_data['email']
                pending = PendingEmailChange.create_for_user(request.user, new_email)
                verify_url = request.build_absolute_uri(
                    reverse('accounts:verify_email_change', args=[pending.token])
                )
                send_email_change_verification(
                    request.user, new_email, verify_url,
                    ttl_hours=PendingEmailChange.TOKEN_TTL_HOURS,
                )
                messages.success(request, f'Verification email sent to {new_email}. Click the link to confirm your new address.')
                return redirect('accounts:manage_account')

    return render(request, 'accounts/manage_account.html', {
        'username_form': username_form,
        'email_form': email_form,
        'stripe_price_pro':   django_settings.STRIPE_PRICE_PRO,
        'stripe_price_elite': django_settings.STRIPE_PRICE_ELITE,
    })


def verify_email_change(request, token):
    try:
        pending = PendingEmailChange.objects.select_related('user').get(token=token)
    except PendingEmailChange.DoesNotExist:
        messages.error(request, 'This verification link is invalid.')
        return redirect('accounts:manage_account')

    if pending.is_expired():
        pending.delete()
        messages.error(request, 'This verification link has expired. Please request a new one.')
        return redirect('accounts:manage_account')

    user = pending.user
    user.email = pending.new_email
    user.save(update_fields=['email'])
    pending.delete()

    messages.success(request, f'Your email address has been updated to {user.email}.')
    return redirect('accounts:manage_account')


@staff_member_required
def yahoo_debug(request):
    """Temporary: shows the exact OAuth URL social-auth will send to Yahoo."""
    if not django_settings.DEBUG:
        raise Http404('Not found')

    from urllib.parse import urlparse, parse_qs
    from django.urls import reverse
    from social_django.utils import load_strategy, load_backend

    strategy = load_strategy(request)
    redirect_path = reverse('social:complete', args=['yahoo-oauth2'])
    backend = load_backend(strategy=strategy, name='yahoo-oauth2', redirect_uri=redirect_path)
    auth_url = backend.auth_url()

    # Parse the auth_url to extract the actual redirect_uri Yahoo will receive
    parsed = urlparse(auth_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    actual_redirect_in_url = params.get('redirect_uri', ['(not present)'])[0]
    actual_client_id = params.get('client_id', ['(not present)'])[0]
    actual_response_type = params.get('response_type', ['(not present)'])[0]
    actual_scope = params.get('scope', ['(not present)'])[0]
    actual_state = params.get('state', ['(not present)'])[0]

    expected_redirect = 'https://localhost:8000/auth/complete/yahoo-oauth2/'
    redirect_match = actual_redirect_in_url == expected_redirect
    auth_base = f'{parsed.scheme}://{parsed.netloc}{parsed.path}'

    rows = ''.join(
        f'<tr><td style="padding:4px 12px 4px 4px"><strong>{k}</strong></td>'
        f'<td><code style="word-break:break-all">{v}</code></td></tr>'
        for k, v in [
            ('client_id', actual_client_id),
            ('redirect_uri', actual_redirect_in_url),
            ('response_type', actual_response_type),
            ('scope', actual_scope),
            ('state', actual_state),
        ]
    )

    return HttpResponse(
        f'<h2>Yahoo OAuth Debug</h2>'
        f'<p><strong>Scheme detected:</strong> <code>{request.scheme}</code></p>'
        f'<p><strong>Host:</strong> <code>{request.get_host()}</code></p>'
        f'<hr>'
        f'<h3>Authorization endpoint</h3>'
        f'<p><code>{auth_base}</code></p>'
        f'<hr>'
        f'<h3>Parameters sent to Yahoo (parsed from auth_url)</h3>'
        f'<table>{rows}</table>'
        f'<hr>'
        f'<h3>Redirect URI check</h3>'
        f'<p><strong>Embedded in auth_url:</strong> <code>{actual_redirect_in_url}</code></p>'
        f'<p><strong>Registered in Yahoo Developer portal:</strong> '
        f'<code>{expected_redirect}</code></p>'
        f'<p><strong>Match:</strong> '
        f'<b>{"✅ YES" if redirect_match else "❌ NO — mismatch! Update Yahoo portal to match the value above."}</b></p>'
        f'<hr>'
        f'<p><strong>Full auth URL:</strong><br>'
        f'<code style="word-break:break-all">{auth_url}</code></p>'
        f'<p><a href="{auth_url}">Test Yahoo OAuth directly</a></p>'
    )
