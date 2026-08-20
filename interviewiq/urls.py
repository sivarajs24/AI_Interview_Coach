"""URL configuration for InterviewIQ."""

from django.urls import include, path


urlpatterns = [path("", include("coach.urls"))]