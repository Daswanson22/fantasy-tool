from django.shortcuts import render, redirect
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
