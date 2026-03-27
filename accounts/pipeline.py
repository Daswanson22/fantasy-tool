from django.contrib.auth import get_user_model
from social_core.pipeline.partial import partial


@partial
def require_registration(strategy, details, backend, user=None, *args, **kwargs):
    """
    For new Yahoo OAuth users: redirect to a registration form to collect
    username, email, and password before creating their Django account.
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

    # Second pass — form was submitted, validate and inject into details
    User = get_user_model()
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password1 = data.get('password1', '')
    password2 = data.get('password2', '')

    errors = []
    if not username:
        errors.append('Username is required.')
    elif User.objects.filter(username=username).exists():
        errors.append('That username is already taken.')

    if not email:
        errors.append('Email is required.')

    if not password1:
        errors.append('Password is required.')
    elif len(password1) < 8:
        errors.append('Password must be at least 8 characters.')
    elif password1 != password2:
        errors.append('Passwords do not match.')

    if errors:
        strategy.request.session['registration_errors'] = errors
        strategy.request.session['registration_prefill'] = {
            'username': username,
            'email': email,
        }
        current_partial = kwargs.get('current_partial')
        return strategy.redirect(
            '/accounts/complete-registration/?partial_token={}'.format(
                current_partial.token
            )
        )

    # Inject validated values so get_username / create_user pick them up
    details['username'] = username
    details['email'] = email

    # Stash password in session — applied by set_yahoo_user_password after create_user
    strategy.request.session['yahoo_reg_password'] = password1


def set_yahoo_user_password(strategy, user=None, *args, **kwargs):
    """
    Set the password for users created through the Yahoo OAuth registration
    flow. The password was stashed in the session by require_registration.
    """
    if not user:
        return
    if user.has_usable_password():
        return  # Already has a password (email signup users, or re-authentication)

    password = strategy.request.session.pop('yahoo_reg_password', None)
    if password:
        user.set_password(password)
        user.save(update_fields=['password'])


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
