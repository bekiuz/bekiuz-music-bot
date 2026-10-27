"""Compatibility entry point for hosts that default to main.py."""

import asyncio

from bot import main


if __name__ == "__main__":
    asyncio.run(main())
