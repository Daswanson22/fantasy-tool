from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'accounts'

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('complete-registration/', views.complete_yahoo_registration, name='complete_registration'),
    path('yahoo-debug/', views.yahoo_debug, name='yahoo_debug'),
]
