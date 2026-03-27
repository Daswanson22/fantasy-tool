from django.shortcuts import render
from django.contrib.auth.decorators import login_required


def index(request):
    return render(request, 'home/index.html')


@login_required
def dashboard(request):
    # Get the Yahoo social auth entry for this user (if connected)
    yahoo_auth = None
    yahoo_connected = False
    yahoo_guid = None

    try:
        social = request.user.social_auth.get(provider='yahoo-oauth2')
        yahoo_connected = True
        yahoo_guid = social.extra_data.get('yahoo_guid', '')
    except Exception:
        pass

    return render(request, 'home/dashboard.html', {
        'yahoo_connected': yahoo_connected,
        'yahoo_guid': yahoo_guid,
    })
