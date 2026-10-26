#!/usr/bin/env python
"""Demo Django project hosting the ITR analysis API (development use only)."""
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "itr_demo.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
