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
    from social_django.utils import load_strategy, load_backend
    strategy = load_strategy(request)
    backend = load_backend(strategy=strategy, name='yahoo-oauth2', redirect_uri=None)
    auth_url = backend.auth_url()
    redirect_uri = backend.get_redirect_uri()
    return HttpResponse(
        f'<h2>Yahoo OAuth Debug</h2>'
        f'<p><strong>Redirect URI being sent to Yahoo:</strong><br>'
        f'<code>{redirect_uri}</code></p>'
        f'<p><strong>Full auth URL:</strong><br>'
        f'<code style="word-break:break-all">{auth_url}</code></p>'
        f'<p><a href="{auth_url}">Click here to test Yahoo OAuth directly</a></p>'
    )
