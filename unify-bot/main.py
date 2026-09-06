"""Start the bot:  python main.py"""
import asyncio
import logging

from unify.bot import UnifyBot
from unify.config import load_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main() -> None:
    env = load_env()
    bot = UnifyBot(env)
    async with bot:
        await bot.start(env.token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
