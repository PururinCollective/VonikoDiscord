"""Turn-taking for several characters running together (`run.bat --char all`).

On their own, every character answers the moment she's called: they all write at the same time, from a view
of the chat that doesn't include each other's replies yet, and the replies land one by one whenever the model
finishes. The director is a neutral referee per channel: it waits for the chat to settle, decides who speaks
and in what order, lets one character write at a time with the latest chat in front of her, and makes the
others wait (or just read).
"""
import asyncio
import logging
import time
from collections import deque

import discord

log = logging.getLogger("director")

BOT_QUIET_SECONDS = 1.0  # wait after a character's message before the next one reacts to it


class Director:
    def __init__(self, bots: list, debounce: float, max_bot_chain: int):
        self.bots = bots
        self.debounce = debounce
        self.max_bot_chain = max_bot_chain
        self.seen: deque[int] = deque(maxlen=5000)       # every bot reports each message; handle it once
        # channel id -> newest message not reacted to yet. People come first: a character's reply that lands
        # while someone is typing must not push their new message out of the queue.
        self.pending_person: dict[int, discord.Message] = {}
        self.pending_bot: dict[int, discord.Message] = {}
        self.last_seen: dict[int, float] = {}
        self.chain: dict[int, int] = {}                  # bot messages in a row since a person spoke
        self.last_person: dict[int, int] = {}            # id of the newest message from a person
        self.workers: dict[int, asyncio.Task] = {}
        for bot in bots:
            bot.director = self

    def message_seen(self, message: discord.Message) -> None:
        """Called for every new message in a watched channel, by any bot that sees it."""
        if message.id in self.seen:
            return
        self.seen.append(message.id)
        channel_id = message.channel.id
        if message.author.bot:
            self.chain[channel_id] = self.chain.get(channel_id, 0) + 1
            self.pending_bot[channel_id] = message
        else:
            self.chain[channel_id] = 0
            self.last_person[channel_id] = message.id
            self.pending_person[channel_id] = message
        self.last_seen[channel_id] = time.monotonic()
        worker = self.workers.get(channel_id)
        if worker is None or worker.done():
            self.workers[channel_id] = asyncio.create_task(self._worker(channel_id))

    def share(self, author_bot, sent: discord.Message) -> None:
        """A character just posted: put it in the other characters' memory right away, so whoever speaks next
        already sees it (the copy that arrives through Discord a moment later is then skipped)."""
        for bot in self.bots:
            if bot is not author_bot and bot.user and bot.watching(sent.channel) and bot.get_channel(sent.channel.id):
                bot.remember(sent)
        self.message_seen(sent)

    def speakers(self, message: discord.Message) -> list:
        """Who answers this message, in speaking order. Everyone else just reads it."""
        if message.author.bot and self.chain.get(message.channel.id, 0) >= self.max_bot_chain:
            return []  # the characters have talked among themselves long enough; wait for a person
        chosen = [bot for bot in self.bots
                  if bot.user and bot.user.id != message.author.id and bot.get_channel(message.channel.id)
                  and bot.watching(message.channel) and bot.wants_to_answer(message)]
        # whoever is replied to or @mentioned goes first, then in the order their names come up
        return sorted(chosen, key=lambda bot: bot.call_position(message))

    async def _worker(self, channel_id: int) -> None:
        while channel_id in self.pending_person or channel_id in self.pending_bot:
            # let the chat settle first: people often send a few messages in a row
            while True:
                quiet = self.debounce if channel_id in self.pending_person else BOT_QUIET_SECONDS
                wait = self.last_seen[channel_id] + quiet - time.monotonic()
                if wait <= 0:
                    break
                await asyncio.sleep(wait)
            if channel_id in self.pending_person:
                message = self.pending_person.pop(channel_id)
                self.pending_bot.pop(channel_id, None)  # older chatter between characters is superseded
            else:
                message = self.pending_bot.pop(channel_id)
            person = self.last_person.get(channel_id)
            speakers = self.speakers(message)
            if speakers:
                log.info("%s answer(s) %s, in that order", ", ".join(b.character.name for b in speakers),
                         message.author.display_name)
            for bot in speakers:
                if self.last_person.get(channel_id) != person:
                    log.info("Someone said something new; the rest of this round is skipped")
                    break
                if message.author.bot and self.chain.get(channel_id, 0) >= self.max_bot_chain:
                    break  # the characters before her in this round used up the chain
                try:
                    await bot.answer(message)
                except Exception:
                    bot.log.exception("Failed to reply in channel %s", channel_id)
