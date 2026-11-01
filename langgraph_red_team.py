"""Compatibility entrypoint for the bounded, validated campaign controller.

The former prompt-only LangGraph demonstration is archived under legacy/.
Task evolution now belongs to the controller, which validates every child and
records every attempt. Use `redforge campaign run --help` for the supported API.
"""

import sys

from harborforge.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["campaign", "run", *sys.argv[1:]]))
