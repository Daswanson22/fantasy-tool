from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth import login
from .forms import SignUpForm


def signup(request):
    if request.user.is_authenticated:
        return redirect('home:index')

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome to Fantasy Tool, {user.username}!')
            return redirect('home:index')
    else:
        form = SignUpForm()

    return render(request, 'accounts/signup.html', {'form': form})


def yahoo_debug(request):
    """Temporary: shows the exact OAuth URL social-auth will send to Yahoo."""
    from django.urls import reverse
    from social_django.utils import load_strategy, load_backend

    strategy = load_strategy(request)
    # Mirror exactly what the @psa('social:complete') decorator does
    redirect_path = reverse('social:complete', args=['yahoo-oauth2'])
    backend = load_backend(strategy=strategy, name='yahoo-oauth2', redirect_uri=redirect_path)

    abs_redirect_uri = strategy.build_absolute_uri(redirect_path)
    auth_url = backend.auth_url()

    expected_redirect = 'https://localhost:8000/auth/complete/yahoo-oauth2/'
    redirect_match = abs_redirect_uri == expected_redirect

    # Check which authorization URL is being used
    auth_base = auth_url.split('?')[0] if '?' in auth_url else auth_url
    correct_auth_url = 'https://api.login.yahoo.com/oauth2/request_auth'
    auth_url_match = auth_base == correct_auth_url

    return HttpResponse(
        f'<h2>Yahoo OAuth Debug</h2>'
        f'<p><strong>Scheme detected:</strong> <code>{request.scheme}</code></p>'
        f'<p><strong>Host:</strong> <code>{request.get_host()}</code></p>'
        f'<hr>'
        f'<h3>Authorization URL</h3>'
        f'<p><strong>Base URL used:</strong> <code>{auth_base}</code></p>'
        f'<p><strong>Expected:</strong> <code>{correct_auth_url}</code></p>'
        f'<p><strong>Match:</strong> <b>{"✅ YES" if auth_url_match else "❌ NO — wrong authorization URL!"}</b></p>'
        f'<hr>'
        f'<h3>Redirect URI</h3>'
        f'<p><strong>Absolute redirect URI sent to Yahoo:</strong><br>'
        f'<code>{abs_redirect_uri}</code></p>'
        f'<p><strong>Registered in Yahoo Developer portal:</strong><br>'
        f'<code>{expected_redirect}</code></p>'
        f'<p><strong>Match:</strong> '
        f'<b>{"✅ YES" if redirect_match else "❌ NO — mismatch!"}</b></p>'
        f'<hr>'
        f'<p><strong>Full auth URL:</strong><br>'
        f'<code style="word-break:break-all">{auth_url}</code></p>'
        f'<p><a href="{auth_url}">Test Yahoo OAuth directly</a></p>'
    )
