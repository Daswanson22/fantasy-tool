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
