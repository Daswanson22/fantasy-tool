from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.admin.views.decorators import staff_member_required
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


@staff_member_required
def yahoo_debug(request):
    """Temporary: shows the exact OAuth URL social-auth will send to Yahoo."""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
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
