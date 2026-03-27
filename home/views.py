from django.shortcuts import render
from django.contrib.auth.decorators import login_required


def index(request):
    return render(request, 'home/index.html')


def _is_hash_username(username):
    """Return True if the username looks like a Yahoo sub hash (32+ hex chars)."""
    return len(username) >= 30 and all(c in '0123456789abcdef' for c in username)


@login_required
def dashboard(request):
    user = request.user
    yahoo_connected = False
    yahoo_guid = None

    try:
        social = user.social_auth.get(provider='yahoo-oauth2')
        yahoo_connected = True
        yahoo_guid = social.extra_data.get('yahoo_guid', '')

        # Fix hash username for accounts created before the pipeline fix was added
        if _is_hash_username(user.username):
            email = user.email or social.extra_data.get('email', '')
            if email:
                user.username = email.split('@')[0]
                user.save(update_fields=['username'])
    except Exception:
        pass

    return render(request, 'home/dashboard.html', {
        'yahoo_connected': yahoo_connected,
        'yahoo_guid': yahoo_guid,
    })
