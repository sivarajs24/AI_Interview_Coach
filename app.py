"""Compatibility entry point for starting the Django development server."""

import os
import sys


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "interviewiq.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line([sys.argv[0], "runserver", *sys.argv[1:]])
