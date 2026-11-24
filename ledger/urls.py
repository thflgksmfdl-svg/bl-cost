from django.urls import path
from . import views

urlpatterns = [path("", views.index), path("api/status/", views.status), path("api/batches/", views.batches),
               path("api/batches/<uuid:pk>/", views.batch_detail),
               path("api/batches/<uuid:pk>/<str:action>/", views.batch_action)]
