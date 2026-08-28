#!/usr/bin/env python3
"""Trishula engine launcher (the Shiva Agent upgrade core).

Usage:
    ./trishula_launch.py code  "fix the retry bug in client.py" [--path DIR]
    ./trishula_launch.py team  "ship the webhooks feature"     [--path DIR]
    ./trishula_launch.py skills [list|search "query"]
    ./trishula_launch.py runs
    ./trishula_launch.py selftest
"""

import sys

if __name__ == "__main__":
    from trishula.cli import main

    sys.exit(main())
