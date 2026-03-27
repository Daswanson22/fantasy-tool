from django.contrib.auth import get_user_model
from social_core.pipeline.partial import partial


@partial
def require_registration(strategy, details, backend, user=None, *args, **kwargs):
    """
    For new Yahoo OAuth users: redirect to a form to collect only a username.
    Email is taken directly from Yahoo's OAuth details (no password needed).
    Existing users pass through immediately.
    """
    if user:
        return  # Existing user — nothing to do

    data = strategy.request_data()

    if 'username' not in data:
        # First pass — save pipeline state and send user to the form
        current_partial = kwargs.get('current_partial')
        return strategy.redirect(
            '/accounts/complete-registration/?partial_token={}'.format(
                current_partial.token
            )
        )

    # Second pass — form was submitted, validate username only
    User = get_user_model()
    username = data.get('username', '').strip()

    errors = []
    if not username:
        errors.append('Username is required.')
    elif len(username) < 3:
        errors.append('Username must be at least 3 characters.')
    elif not username.replace('_', '').replace('-', '').isalnum():
        errors.append('Username may only contain letters, numbers, hyphens and underscores.')
    elif User.objects.filter(username=username).exists():
        errors.append('That username is already taken.')

    if errors:
        strategy.request.session['registration_errors'] = errors
        strategy.request.session['registration_prefill'] = {'username': username}
        current_partial = kwargs.get('current_partial')
        return strategy.redirect(
            '/accounts/complete-registration/?partial_token={}'.format(
                current_partial.token
            )
        )

    # Inject username; email comes from Yahoo details (set by get_user_details)
    details['username'] = username


def fix_username_from_email(backend, user, response, details, *args, **kwargs):
    """
    If the user's current username is still the raw Yahoo sub hash (a long
    hex string), replace it with the email prefix now that we have the email
    from the JWT claims.
    """
    if not user:
        return

    email = details.get('email') or response.get('email', '')
    if not email:
        return

    email_prefix = email.split('@')[0]
    current = user.username

    # A Yahoo sub hash is 32 lowercase hex characters
    is_hash = len(current) >= 30 and all(c in '0123456789abcdef' for c in current)
    if is_hash and current != email_prefix:
        user.username = email_prefix
        user.save(update_fields=['username'])
