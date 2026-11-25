from django.urls import path
from . import views

urlpatterns = [path("", views.index), path("api/status/", views.status), path("api/reports/", views.reports), path("api/batches/", views.batches),
               path("work-check/", views.report_page, {"mode": "work-check"}),
               path("dashboard/", views.report_page, {"mode": "dashboard"}),
               path("api/batches/<uuid:pk>/", views.batch_detail),
               path("api/batches/<uuid:pk>/<str:action>/", views.batch_action)]
