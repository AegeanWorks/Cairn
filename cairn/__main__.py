"""`python -m cairn` entry point."""

import sys

from cairn.cli import command_line

if __name__ == "__main__":
    sys.exit(command_line(sys.argv[1:]))
