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

    return HttpResponse(
        f'<h2>Yahoo OAuth Debug</h2>'
        f'<p><strong>Scheme detected:</strong> <code>{request.scheme}</code></p>'
        f'<p><strong>Host:</strong> <code>{request.get_host()}</code></p>'
        f'<p><strong>Redirect path (relative):</strong> <code>{redirect_path}</code></p>'
        f'<p><strong>Absolute redirect URI sent to Yahoo:</strong><br>'
        f'<code>{abs_redirect_uri}</code></p>'
        f'<p><strong>Registered in Yahoo Developer portal:</strong><br>'
        f'<code>https://localhost:8000/auth/complete/yahoo-oauth2/</code></p>'
        f'<p><strong>Match:</strong> '
        f'<b>{"✅ YES" if abs_redirect_uri == "https://localhost:8000/auth/complete/yahoo-oauth2/" else "❌ NO — mismatch!"}</b></p>'
        f'<hr>'
        f'<p><strong>Full auth URL:</strong><br>'
        f'<code style="word-break:break-all">{auth_url}</code></p>'
        f'<p><a href="{auth_url}">Test Yahoo OAuth directly</a></p>'
    )
