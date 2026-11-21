#!/usr/bin/env python3
"""Run the Parsimon CLI from the plugin folder without installing it: python3 cli.py report"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from parsimon.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
