"""URL configuration for AI Interview Coach."""

from django.urls import include, path


urlpatterns = [path("", include("coach.urls"))]