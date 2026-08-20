"""WSGI entry point for InterviewIQ."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interviewiq.settings")
application = get_wsgi_application()