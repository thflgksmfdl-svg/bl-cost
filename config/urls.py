from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from ledger import views

urlpatterns = [path("admin/", admin.site.urls), path("health/", views.health),
               path("login/", views.sign_in), path("logout/", views.sign_out),
               path("account/password/", auth_views.PasswordChangeView.as_view(template_name="password.html", success_url="/")),
               path("bl-cost/", include("ledger.urls")), path("", include("ledger.urls"))]
