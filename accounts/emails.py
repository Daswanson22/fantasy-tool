from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags


def _send(subject, to_email, template, context):
    context.setdefault('site_name', 'The Fantasy Lab')
    html_body = render_to_string(template, context)
    text_body = strip_tags(html_body)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    msg.attach_alternative(html_body, 'text/html')
    msg.send()


def send_otp_email(email, code, ttl_minutes=10):
    _send(
        subject='Your Fantasy Lab login code',
        to_email=email,
        template='emails/otp_login.html',
        context={'code': code, 'ttl_minutes': ttl_minutes},
    )


def send_email_change_verification(user, new_email, verify_url, ttl_hours=24):
    _send(
        subject='Verify your new email address — The Fantasy Lab',
        to_email=new_email,
        template='emails/email_change_verify.html',
        context={
            'username': user.username,
            'verify_url': verify_url,
            'ttl_hours': ttl_hours,
        },
    )


def _yahoo_team_url(team_key):
    try:
        league_part, team_num = team_key.rsplit('.t.', 1)
        league_num = league_part.rsplit('.l.', 1)[1]
        return f'https://baseball.fantasysports.yahoo.com/b1/{league_num}/{team_num}'
    except (ValueError, IndexError):
        return 'https://baseball.fantasysports.yahoo.com'


def send_ai_transaction_email(user, result):
    add_player  = result.get('add_player') or {}
    drop_player = result.get('drop_player') or {}
    team_key    = result.get('team_key', '')
    subject = (
        f'AI Manager: +{add_player.get("name", "?")} / '
        f'-{drop_player.get("name", "?")} — The Fantasy Lab'
    )
    _send(
        subject=subject,
        to_email=user.email,
        template='emails/ai_transaction.html',
        context={
            'username':     user.username,
            'add_player':   add_player,
            'drop_player':  drop_player,
            'reason':       result.get('reason', ''),
            'roster_moves': result.get('roster_moves', []),
            'yahoo_team_url': _yahoo_team_url(team_key),
        },
    )


def send_test_email(email, username='there'):
    _send(
        subject='Test Email — The Fantasy Lab',
        to_email=email,
        template='emails/test_email.html',
        context={
            'username': username,
            'smtp_host': getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
            'from_email': settings.DEFAULT_FROM_EMAIL,
        },
    )
