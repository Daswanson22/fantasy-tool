from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings
from . import views
from . import billing

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup, name='signup'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('complete-registration/', views.complete_yahoo_registration, name='complete_registration'),
    path('manage/', views.manage_account, name='manage_account'),
    path('verify-email/<str:token>/', views.verify_email_change, name='verify_email_change'),
    # Billing
    path('billing/checkout/<str:price_id>/', billing.create_checkout_session, name='billing_checkout'),
    path('billing/portal/', billing.create_portal_session, name='billing_portal'),
    path('billing/success/', billing.billing_success, name='billing_success'),
    path('billing/webhook/', billing.stripe_webhook, name='stripe_webhook'),
]

if settings.DEBUG:
    urlpatterns.append(path('yahoo-debug/', views.yahoo_debug, name='yahoo_debug'))
