"""Anime character bots for Discord (Oomuro Sakurako, Shima Rin, ...), powered by a local LM Studio model.
Each character has her own config.<char>.json and memory in context/context.<char>.db.

    python bot.py --char sakurako            run Sakurako on Discord
    python bot.py --char all                 run every character together, taking turns (see director.py)
    python bot.py --config config.rin.json   run one character from an explicit config file
    python bot.py --char rin --cli           chat with Rin in the terminal
    python bot.py --char all --no-tui        plain log lines instead of the status screen
"""
import argparse
import asyncio
import base64
import contextlib
import getpass
import io
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from PIL import Image

from brain import Character, forget_user
from director import Director, channel_label
from llm import LMStudioClient
from memory import ContextDB, make_snowflake, now_iso

log = logging.getLogger("bot")
GET_TOKEN = "Get it from discord.com/developers > the character's app > Bot > Reset Token."
CLI_CHANNEL_ID = 1
DB_FOLDER = "context"
MAX_IMAGES = 4                     # per message
MAX_IMAGE_BYTES = 25 * 1024 * 1024
IMAGE_MAX_SIDE = 1024              # px; bigger images are shrunk before they're sent to the model
REPLY_QUOTE_CHARS = 300            # how much of a replied-to message is quoted
LEGACY_DB = ("context.db", "sakurako")  # memory file of the Sakurako-only version, and who it belongs to


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def token_problem(token: str) -> str | None:
    """Catch the usual Developer Portal copy-paste mix-ups before Discord rejects the login."""
    if not token or token.startswith("PUT_"):
        return "is not set."
    if token.isdigit():
        return "is the Application ID, not the bot token."
    if re.fullmatch(r"[0-9a-fA-F]{64}", token):
        return "is the Public Key, not the bot token."
    if token.count(".") != 2:
        return "is not a bot token (maybe the Client Secret?). A bot token has three parts separated by dots."
    return None


SENTENCE_END = re.compile(r"[.!?。！？\n]+")
# "Rin-chan", "Naruto-niichan", "Red-ojisan", "Naruto-nii-chan"
HONORIFIC = re.compile(r"(-(o?nii|o?nee|o?ji|o?ba|aniki|aneki)?(chan|san|sama|senpai|sensei|kun|tan|dono)?)+$")
JP_HONORIFICS = ("ちゃん", "さん", "様", "さま", "先輩", "せんぱい", "くん", "君", "殿", "兄ちゃん", "兄さん", "にいちゃん",
                 "姉ちゃん", "姉さん", "ねえちゃん", "おじさん", "おじちゃん")


class NameTrigger:
    """Decides whether a message is talking TO her by name, not just about her. Her name has to be one of
    the first 3 words of a sentence ("Rin, ...", "hey Rin, ...") or its last word ("... right, Rin?"),
    so "nijika, can you ask sakurako about rin camping?" calls only Nijika."""

    def __init__(self, triggers: list[str]):
        self.latin = [[HONORIFIC.sub("", w) for w in t.lower().split()] for t in triggers if t.isascii()]
        self.cjk = [t for t in triggers if not t.isascii()]

    def called(self, text: str) -> bool:
        for sentence in SENTENCE_END.split(text):
            words = [HONORIFIC.sub("", w.strip("'’-")) for w in re.findall(r"[\w'’-]+", sentence.lower())]
            words = [w for w in words if w]  # "rin's" stays one word, so it isn't "rin"
            for name in self.latin:
                for i in range(len(words) - len(name) + 1):
                    if words[i:i + len(name)] == name and (i < 3 or i + len(name) == len(words)):
                        return True
            sentence = sentence.strip(" \t,，、:：…~～")
            for name in self.cjk:  # Japanese has no spaces: at the start, or at the end (maybe with ちゃん etc.)
                at = sentence.find(name)
                if at != -1 and (at <= 3 or sentence[at + len(name):] in ("", *JP_HONORIFICS)):
                    return True
        return False

    def position(self, text: str) -> int:
        """Where her name first appears in the text (a large number if it doesn't), for speaking order."""
        lowered = text.lower()
        spots = [m.start() for name in self.latin
                 if (m := re.search(r"\b" + r"[\s-]+".join(map(re.escape, name)), lowered))]
        spots += [at for name in self.cjk if (at := text.find(name)) != -1]
        return min(spots, default=10**6)


def has_console() -> bool:
    return sys.platform == "win32" and bool(sys.stdin) and sys.stdin.isatty()


async def wait_for_q() -> None:
    """Return once Q is pressed in this console window (Windows only)."""
    import msvcrt
    while True:
        while msvcrt.kbhit():
            if msvcrt.getwch().lower() == "q":
                return
        await asyncio.sleep(0.2)


def available_characters(folder: Path) -> list[str]:
    return [p.name[len("config."):-len(".json")] for p in sorted(folder.glob("config.*.json"))
            if p.name != "config.example.json"]


def open_all_databases(own_path: Path, own_db: ContextDB) -> list[ContextDB]:
    """This character's database plus every other character's context.<char>.db in the same folder."""
    dbs = [own_db]
    for path in sorted(own_path.parent.glob("context.*.db")):
        if path.resolve() != own_path.resolve():
            dbs.append(ContextDB(str(path), owner=path.name[len("context."):-len(".db")]))
    return dbs


def image_to_data_url(data: bytes) -> str | None:
    """Re-encode any image (WebP, GIF, PNG, huge photos, ...) as a small JPEG, which LM Studio accepts.
    LM Studio rejects WebP, which Discord uses a lot. Returns None if the bytes aren't a readable image."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)  # first frame of an animation
            im = im.convert("RGBA")
            im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
            flat = Image.new("RGB", im.size, "white")  # transparency becomes white, not black
            flat.paste(im, mask=im.getchannel("A"))
            buf = io.BytesIO()
            flat.save(buf, "JPEG", quality=85)
    except Exception:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def shorten(text: str, limit: int = REPLY_QUOTE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def split_message(text: str, limit: int = 2000) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class ForgetMeView(discord.ui.View):
    """Confirm/cancel buttons for /forgetme (the message is ephemeral, so only the requester sees them)."""

    def __init__(self, bot: "CharacterBot", user: discord.abc.User):
        super().__init__(timeout=120)
        self.bot = bot
        self.user = user

    @discord.ui.button(label="Delete my data", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Deleting your data...", view=None)
        user = self.user
        names = {user.name, user.global_name, user.display_name} - {None}
        dbs = open_all_databases(self.bot.db_path, self.bot.db)
        try:
            deleted, scrubbed = await forget_user(dbs, self.bot.character.llm, user.id, names)
        except Exception:
            self.bot.log.exception("/forgetme failed for user %s", user.id)
            await interaction.edit_original_response(
                content="Something went wrong and your data may not be fully deleted. Please contact the server staff.")
            return
        finally:
            for db in dbs[1:]:
                db.close()
        self.bot.log.info("/forgetme: deleted %d message(s) and scrubbed %d memory note(s) for user %s",
                          deleted, scrubbed, user.id)
        await interaction.edit_original_response(
            content=f"Done. Deleted **{deleted}** stored message(s) and removed you from **{scrubbed}** memory "
                    f"note(s), for every character.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was deleted.", view=None)


class CharacterBot(discord.Client):
    def __init__(self, dcfg: dict, character: Character, db: ContextDB, db_path: Path, vision: bool):
        self.cfg = dcfg
        self.vision = vision
        self.director = None  # set by Director when characters run together
        self.problem = ""     # why she couldn't log in, for the status screen
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions(
            everyone=False, roles=False, users=True, replied_user=dcfg.get("mention_author", False)))
        self.character = character
        self.db = db
        self.db_path = db_path
        self.log = logging.getLogger(f"bot.{character.key}")
        self.guild_ids = set(dcfg.get("guild_ids") or [])
        self.channel_ids = set(dcfg.get("channel_ids") or [])
        self.free_talk_ids = set(dcfg.get("free_talk_channel_ids") or [])
        self.triggers = NameTrigger(dcfg.get("name_triggers") or [])
        self.debounce = dcfg.get("debounce_seconds", 2.5)
        self.pending: dict[int, discord.Message] = {}      # channel id -> newest message to answer
        self.last_trigger: dict[int, float] = {}
        self.workers: dict[int, asyncio.Task] = {}
        self.background: set[asyncio.Task] = set()
        self.caught_up = False
        self.tree = app_commands.CommandTree(self)
        self.register_commands()

    def register_commands(self):
        @self.tree.command(name="forgetme", description="Delete everything the character bots have stored about you")
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)
        async def forgetme(interaction: discord.Interaction):
            dbs = open_all_databases(self.db_path, self.db)
            try:
                count = len(set().union(*(db.user_message_ids(interaction.user.id) for db in dbs)))
            finally:
                for db in dbs[1:]:
                    db.close()
            await interaction.response.send_message(
                f"There are **{count}** of your messages stored, plus anything the characters wrote about you "
                "in their memory notes. This covers every character, not just me.\n"
                "Deleting removes:\n"
                "- every message of yours that was stored, in any server\n"
                "- your whole DM history with every character\n"
                "- the characters' replies that mention your name\n"
                "- you from every character's memory notes\n"
                "Messages other people wrote about you are not removed, and anything you send later is "
                "stored again. This can't be undone.",
                view=ForgetMeView(self, interaction.user), ephemeral=True)

    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            self.log.info("Registered slash commands: %s", ", ".join("/" + c.name for c in synced))
        except discord.HTTPException:
            self.log.exception("Couldn't register slash commands (was the bot invited with applications.commands?)")

    # --- which messages she sees and answers ------------------------------------------------

    def watching(self, channel) -> bool:
        if isinstance(channel, discord.DMChannel):
            return self.cfg.get("reply_to_dms", True)
        guild = getattr(channel, "guild", None)
        if guild is None or (self.guild_ids and guild.id not in self.guild_ids):
            return False
        return not self.channel_ids or bool({channel.id, getattr(channel, "parent_id", None)} & self.channel_ids)

    def addressed(self, message: discord.Message) -> bool:
        """True when someone is talking to her directly: DM, @mention, reply to her, or her name."""
        if isinstance(message.channel, discord.DMChannel) or any(u.id == self.user.id for u in message.mentions):
            return True
        guild = self.get_guild(message.guild.id) if message.guild else None  # this bot's view, not another's
        if guild and any(r.is_bot_managed() and r in guild.me.roles for r in message.role_mentions):
            return True  # people often pick the bot's auto-created role instead of the bot when typing @Name
        ref = message.reference.resolved if message.reference else None
        if isinstance(ref, discord.Message) and ref.author.id == self.user.id:
            return True
        return self.triggers.called(message.content)

    def should_reply(self, message: discord.Message) -> bool:
        if self.addressed(message):
            return True
        channel = message.channel
        if {channel.id, getattr(channel, "parent_id", None)} & self.free_talk_ids:
            return True
        return random.random() < self.cfg.get("random_reply_chance", 0.0)

    # --- storing messages in context.db ---------------------------------------------------------

    @staticmethod
    def plain_text(message: discord.Message) -> str:
        text = message.clean_content
        text += "".join(f" [attachment: {a.filename}]" for a in message.attachments)
        text += "".join(f" [sticker: {s.name}]" for s in message.stickers)
        return text.strip()

    def to_text(self, message: discord.Message) -> str:
        """The message as stored and shown to the model. A reply quotes the start of the message it answers,
        so "nijika, can you look at this?" sent as a reply still makes sense later."""
        text = self.plain_text(message)
        if message.type != discord.MessageType.reply or not message.reference:
            return text
        ref = message.reference.resolved
        if isinstance(ref, discord.Message):
            name, quoted = ref.author.display_name, self.plain_text(ref)
        elif stored := self.db.get_message(message.reference.message_id or 0):  # Discord didn't include it
            name, quoted = stored["author_name"], stored["content"]
        else:
            return text  # replied-to message was deleted, and we never saw it
        return f'(replying to {name}: "{shorten(quoted)}") {text}' if quoted else f"(replying to {name}) {text}"

    async def replied_to(self, message: discord.Message) -> discord.Message | None:
        """The message this one replies to, fetched from Discord if it wasn't included."""
        if message.type != discord.MessageType.reply or not message.reference:
            return None
        ref = message.reference.resolved
        if isinstance(ref, discord.Message):
            return ref
        if isinstance(ref, discord.DeletedReferencedMessage) or not message.reference.message_id:
            return None
        try:
            return await message.channel.fetch_message(message.reference.message_id)
        except discord.HTTPException:
            return None

    def remember(self, message: discord.Message) -> bool:
        """Store the message. Returns False if there was nothing to store."""
        text = self.to_text(message)
        if not text:
            return False
        self.db.add_message(
            message.id, message.channel.id, message.guild.id if message.guild else None, message.author.id,
            message.author.display_name, text, self.character.key if message.author.id == self.user.id else None,
            message.created_at.isoformat())
        return True

    @staticmethod
    def describe_place(channel) -> str:
        if isinstance(channel, discord.DMChannel):
            who = channel.recipient.display_name if channel.recipient else "someone"
            return f"a private Discord DM with {who}"
        if isinstance(channel, discord.Thread) and channel.parent:
            return f'the thread "{channel.name}" in #{channel.parent.name} of the "{channel.guild.name}" Discord server'
        return f'#{channel.name} in the "{channel.guild.name}" Discord server'

    def link_mentions(self, text: str, channel) -> str:
        """The model writes "@Anime4000" as plain text, which tags nobody. Turn it into a real Discord mention
        for anyone who has talked in this channel (people or characters, not herself)."""
        if "@" not in text:
            return text
        people = {name: user_id for name, user_id in self.db.people(channel.id).items()
                  if user_id != self.user.id and name.lower() not in ("everyone", "here")}
        if not people:
            return text
        ids = {name.lower(): user_id for name, user_id in people.items()}
        names = "|".join(map(re.escape, sorted(people, key=len, reverse=True)))  # "Rin Fan" before "Rin"
        return re.sub(r"(?<![\w<@])@(%s)(?!\w)" % names, lambda m: f"<@{ids[m.group(1).lower()]}>", text,
                      flags=re.IGNORECASE)

    # --- events -------------------------------------------------------------------------------

    async def on_ready(self):
        self.log.info("%s logged in as %s (id %s) in %d server(s)",
                      self.character.name, self.user, self.user.id, len(self.guilds))
        if self.cfg.get("status_text"):
            await self.change_presence(activity=discord.CustomActivity(name=self.cfg["status_text"]))
        try:
            self.log.info("Using LM Studio model: %s", await self.character.llm.resolve_model())
        except Exception:
            self.log.exception("Can't reach LM Studio at %s", self.character.llm.api)
        if not self.caught_up:  # on_ready fires again after reconnects
            self.caught_up = True
            await self.catch_up()

    async def on_message(self, message: discord.Message):
        if not self.watching(message.channel):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if not self.remember(message) or message.author.id == self.user.id:
            return
        if self.director:  # running together with other characters: the director decides who speaks
            self.director.message_seen(message)
            return
        # stop after max_bot_chain bot messages in a row, so two characters can't keep talking forever
        if message.author.bot and self.bot_chain(message.channel) >= self.cfg.get("max_bot_chain", 3):
            return
        if self.wants_to_answer(message):
            self.schedule(message)

    def wants_to_answer(self, message: discord.Message) -> bool:
        if not message.author.bot:
            return self.should_reply(message)
        if self.cfg.get("reply_to_bots", False):
            return self.should_reply(message)
        # the other characters are bots too: answer one only when she talks to this character
        return self.cfg.get("reply_to_characters", True) and self.addressed(message)

    def call_position(self, message: discord.Message) -> int:
        """How early she's called in the message: a reply to her or an @mention first, then where her name is."""
        ref = message.reference.resolved if message.reference else None
        if isinstance(ref, discord.Message) and ref.author.id == self.user.id:
            return -2
        if any(u.id == self.user.id for u in message.mentions):
            return -1
        return self.triggers.position(message.content)

    def posted(self, sent: discord.Message) -> None:
        """Store a message she just sent, and let the other characters see it right away."""
        self.remember(sent)
        if self.director:
            self.director.share(self, sent)

    async def answer(self, message: discord.Message) -> None:
        """Answer a message the director picked for her. Message objects belong to the bot that received them,
        and replying through another bot's copy would post as that bot, so she uses her own copy."""
        own = next((m for m in reversed(self.cached_messages) if m.id == message.id), None)
        if own is None:
            channel = self.get_channel(message.channel.id)
            try:
                own = await channel.fetch_message(message.id) if channel else None
            except discord.HTTPException:
                own = None
        if own is not None:
            await self.respond(own)

    def bot_chain(self, channel) -> int:
        """How many bot messages in a row end this channel, i.e. since a person last said something."""
        count = 0
        for m in reversed(self.cached_messages):
            if m.channel.id == channel.id:
                if not m.author.bot:
                    break
                count += 1
        return count

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if self.watching(after.channel) and (text := self.to_text(after)):
            self.db.edit_message(after.id, text)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        self.db.delete_message(payload.message_id)

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        for message_id in payload.message_ids:
            self.db.delete_message(message_id)

    async def catch_up(self):
        """Resume: pull in messages sent while she was offline, and answer the latest one if it was for her."""
        if not self.cfg.get("catch_up_on_start", True):
            return
        limit = self.cfg.get("catch_up_limit", 50)
        max_age = timedelta(minutes=self.cfg.get("catch_up_reply_max_age_minutes", 30))
        for channel_id in self.db.known_channels():
            try:
                channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
                if not self.watching(channel):
                    continue
                last_id = self.db.last_message_id(channel_id) or 0
                missed = [m async for m in channel.history(limit=limit) if m.id > last_id][::-1]
            except discord.HTTPException as e:
                self.log.debug("Skipping catch-up for channel %s: %s", channel_id, e)
                continue
            for m in missed:
                if m.type in (discord.MessageType.default, discord.MessageType.reply):
                    self.remember(m)
            if not missed:
                continue
            self.log.info("Caught up on %d missed message(s) in %s", len(missed), self.describe_place(channel))
            latest = missed[-1]
            if (self.cfg.get("reply_to_missed", True) and not latest.author.bot and self.addressed(latest)
                    and datetime.now(timezone.utc) - latest.created_at < max_age):
                if self.director:
                    self.director.message_seen(latest)
                else:
                    self.schedule(latest)

    # --- replying -----------------------------------------------------------------------------

    def schedule(self, message: discord.Message):
        """Wait until the channel goes quiet for a moment, then answer the newest message."""
        channel_id = message.channel.id
        self.pending[channel_id] = message
        self.last_trigger[channel_id] = time.monotonic()
        worker = self.workers.get(channel_id)
        if worker is None or worker.done():
            self.workers[channel_id] = asyncio.create_task(self._worker(channel_id))

    async def _worker(self, channel_id: int):
        while channel_id in self.pending:
            while (wait := self.last_trigger[channel_id] + self.debounce - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            message = self.pending.pop(channel_id)
            try:
                await self.respond(message)
            except Exception:
                self.log.exception("Failed to reply in channel %s", channel_id)

    async def collect_images(self, message: discord.Message) -> list[str]:
        """Images attached to the message and to the message it replies to (so "nijika, what's wrong here?"
        as a reply to someone's screenshot works), ready for the model. None if the model is text-only."""
        if not self.vision or not await self.character.llm.supports_images():
            return []
        attachments = list(message.attachments)
        if ref := await self.replied_to(message):
            attachments += ref.attachments
        images = []
        for a in attachments:
            if len(images) >= MAX_IMAGES:
                break
            if (a.content_type or "").startswith("image/") and a.size <= MAX_IMAGE_BYTES:
                if url := await asyncio.to_thread(image_to_data_url, await a.read()):
                    images.append(url)
                else:
                    self.log.warning("Couldn't read image %s", a.filename)
        return images

    async def respond(self, message: discord.Message):
        channel = message.channel
        label = self.character.labels[channel.id] = channel_label(channel)

        async def say_searching(text: str):  # "wait, let me look that up!" while the search runs
            self.posted(await channel.send(text))

        with self.character.busy(f"writing in {label}"):
            images = await self.collect_images(message)
            async with channel.typing():
                reply = await self.character.reply(channel.id, self.describe_place(channel), message.id, images,
                                                   on_search=say_searching)
        if not reply:
            self.log.warning("Model returned an empty reply in %s", self.describe_place(channel))
            return
        reply = self.link_mentions(reply, channel)
        # Answer a person with Discord's reply feature, which tags them (always_reply, mention_author). Other
        # characters and DMs get it only if others have talked since, so it's clear who she's answering: a
        # reply to a character would call that character back every time.
        to_person = not message.author.bot and not isinstance(channel, discord.DMChannel)
        quote = (to_person and self.cfg.get("always_reply", True)) or channel.last_message_id != message.id
        reference = message.to_reference(fail_if_not_exists=False)  # deleted meanwhile: just post it
        for i, chunk in enumerate(split_message(reply)):
            sent = await channel.send(chunk, reference=reference if i == 0 and quote else None)
            self.posted(sent)
        task = asyncio.create_task(self.character.maybe_summarize(channel.id))
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def close(self):
        await self.character.llm.close()
        await super().close()


async def run_cli(character: Character, db: ContextDB, user: str, say: str | None):
    """Terminal chat using the same brain and memory. /forget wipes this chat, /quit exits."""
    name = character.name
    channel_id = CLI_CHANNEL_ID
    place = f"a private chat with {user} (not on Discord, in a terminal)"
    if say is None:
        print(f"Chatting with {name} as {user}. Commands: /forget, /quit")
        for row in db.recent_messages(channel_id, 0, 6):
            print(f"{name if row['character'] == character.key else row['author_name']}> {row['content']}")
    while True:
        text = say if say is not None else (await asyncio.to_thread(input, f"{user}> ")).strip()
        if text in ("/quit", "/exit"):
            break
        if text == "/forget":
            db.forget_channel(channel_id)
            print("(memory of this chat wiped)")
            continue
        if text:
            db.add_message(make_snowflake(), channel_id, None, 0, user, text, None, now_iso())

            async def say_searching(waiting: str):
                db.add_message(make_snowflake(), channel_id, None, None, name, waiting, character.key, now_iso())
                print(f"{name}> {waiting}")

            reply = await character.reply(channel_id, place, on_search=say_searching)
            if reply:
                db.add_message(make_snowflake(), channel_id, None, None, name, reply, character.key, now_iso())
            print(f"{name}> {reply or '(no reply)'}")
            await character.maybe_summarize(channel_id)
        if say is not None:
            break
    await character.llm.close()


def move_old_db(base_dir: Path, db_path: Path, char_id: str):
    """Earlier versions kept memory next to the config: context.db (Sakurako only), then context.<char>.db.
    Move it into the context folder the first time she starts."""
    if db_path.exists():
        return
    old_names = [f"context.{char_id}.db"] + ([LEGACY_DB[0]] if char_id == LEGACY_DB[1] else [])
    old = next((base_dir / n for n in old_names if (base_dir / n).exists()), None)
    if old is None:
        return
    try:
        old.rename(db_path)
    except PermissionError:
        sys.exit(f"{old.name} is still in use. Stop the copy of this character that is still running "
                 "(close its window or press Q in it), then run this again.")
    for suffix in ("-wal", "-shm"):  # SQLite's journal files must move with the database
        journal = old.with_name(old.name + suffix)
        if journal.exists():
            journal.rename(db_path.with_name(db_path.name + suffix))
    print(f"Moved {old.name} to {db_path.relative_to(base_dir)}")


def load_character(config_path: Path) -> tuple[dict, Character, ContextDB, Path]:
    """Read a character's config and open her memory. Returns (config, character, database, database path)."""
    cfg = load_config(config_path)
    base_dir = config_path.parent
    char_cfg = cfg["character"]
    db_path = base_dir / cfg["memory"].get("db_path", f"{DB_FOLDER}/context.{char_cfg['id']}.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    move_old_db(base_dir, db_path, char_cfg["id"])
    db = ContextDB(str(db_path), owner=char_cfg["id"])
    others = []  # the other characters (their config files sit next to this one), so she knows who's around
    for name in available_characters(base_dir):
        path = base_dir / f"config.{name}.json"
        try:
            other = load_config(path)["character"] if path.resolve() != config_path else None
        except (OSError, ValueError, KeyError):
            other = None
        if other and other.get("id") != char_cfg["id"]:
            others.append(other)
    return cfg, Character(char_cfg, cfg, db, LMStudioClient(cfg["llm"]), base_dir, others), db, db_path


def quiet_logs():
    for chatty in ("ddgs", "primp"):  # the web search libraries log every request they make
        logging.getLogger(chatty).setLevel(logging.WARNING)
    discord.VoiceClient.warn_nacl = discord.VoiceClient.warn_dave = False  # they never join voice


def make_bot(cfg: dict, character: Character, db: ContextDB, db_path: Path) -> CharacterBot:
    return CharacterBot(cfg["discord"], character, db, db_path, cfg["llm"].get("vision", True))


def run_bots(bots: list[tuple[CharacterBot, str]], dbs: list[ContextDB], skipped: dict[str, str],
             director: Director | None, use_tui: bool):
    """Log the characters in and keep them running until Q (or Ctrl+C), with the status screen or with plain
    log lines."""
    quiet_logs()
    screen = None
    if use_tui:
        from tui import StatusScreen  # only needed here; --cli and --no-tui work without it
        screen = StatusScreen([bot for bot, _ in bots], skipped, director)
    else:
        discord.utils.setup_logging(level=logging.INFO)

    async def run_one(bot: CharacterBot, token: str):
        try:
            async with bot:
                await bot.start(token)
        except discord.LoginFailure:
            bot.problem = "token rejected"
            bot.log.error("Discord rejected %s's bot token (it was reset, or copied incompletely). %s",
                          bot.character.name, GET_TOKEN)
        except discord.PrivilegedIntentsRequired:
            bot.problem = "Message Content Intent is off"
            bot.log.error("Turn on Message Content Intent for %s: discord.com/developers > the app > Bot > "
                          "Privileged Gateway Intents.", bot.character.name)

    async def stop_on_q():
        if has_console():
            log.info("Running %s. Press Q in this window to stop.", ", ".join(bot.character.name for bot, _ in bots))
            await wait_for_q()
            log.info("Q pressed: logging out and saving memory...")
            await asyncio.gather(*(bot.close() for bot, _ in bots))

    async def together():
        helpers = [asyncio.create_task(stop_on_q())]
        if screen:
            helpers.append(asyncio.create_task(screen.refresh()))
        try:
            await asyncio.gather(*(run_one(bot, token) for bot, token in bots))
        finally:
            for task in helpers:
                task.cancel()

    try:
        with screen or contextlib.nullcontext():
            for name, why in skipped.items():
                log.warning("Not running %s: %s", name, why)
            asyncio.run(together())
        log.info("Everyone has logged out. Bye!" if len(bots) > 1 else
                 f"{bots[0][0].character.name} has logged out. Bye!")
    except KeyboardInterrupt:
        pass
    finally:
        for db in dbs:
            db.close()  # flushes SQLite's journal into the database file


def run_all(folder: Path, use_tui: bool):
    """Every character in one process, taking turns through the director, with one Q to stop them all."""
    bots, dbs, skipped = [], [], {}
    for name in available_characters(folder):
        cfg, character, db, db_path = load_character(folder / f"config.{name}.json")
        dbs.append(db)
        token = cfg["discord"].get("token", "").strip()
        if problem := token_problem(token):
            skipped[character.name] = f"discord.token in config.{name}.json {problem}"
            continue
        bots.append((make_bot(cfg, character, db, db_path), token))
    if not bots:
        sys.exit("No character has a usable bot token.\n" + "\n".join(skipped.values()))
    director = Director([bot for bot, _ in bots], debounce=min(bot.debounce for bot, _ in bots),
                        max_bot_chain=min(bot.cfg.get("max_bot_chain", 3) for bot, _ in bots))
    run_bots(bots, dbs, skipped, director, use_tui)


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Run a character bot on Discord, or chat with one "
                                                 "in the terminal.")
    who = parser.add_mutually_exclusive_group()
    who.add_argument("--char", help="character to run, e.g. sakurako or rin (loads config.<char>.json), "
                                    "or 'all' to run every character together, taking turns")
    who.add_argument("--config", help="config file to load, e.g. config.sakurako.json")
    parser.add_argument("--cli", action="store_true", help="chat in the terminal instead of Discord")
    parser.add_argument("--user", help="your name in --cli mode")
    parser.add_argument("--say", help="with --cli: send one message, print the reply, and exit")
    parser.add_argument("--no-tui", action="store_true", help="print plain log lines instead of the status screen")
    args = parser.parse_args()
    use_tui = not args.no_tui and sys.stdout.isatty()

    choices = ", ".join(available_characters(here)) or "none yet (copy config.example.json to config.<char>.json)"
    if args.char and args.char.lower() == "all":
        if args.cli:
            sys.exit("--cli talks to one character: pick one with --char")
        run_all(here, use_tui)
        return
    if args.config:
        config_path = Path(args.config).resolve()
    elif args.char:
        config_path = here / f"config.{args.char.lower()}.json"
    else:
        parser.error(f"choose a character with --char ({choices}, or all) or --config")
    if not config_path.exists():
        sys.exit(f"{config_path} not found. Available characters: {choices}")

    cfg, character, db, db_path = load_character(config_path)
    if args.cli:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
            user = args.user or cfg.get("cli", {}).get("user_name") or getpass.getuser()
            asyncio.run(run_cli(character, db, user, args.say))
        finally:
            db.close()  # flushes SQLite's journal into the database file
        return
    token = cfg["discord"].get("token", "").strip()
    if problem := token_problem(token):
        db.close()
        sys.exit(f"discord.token in {config_path.name} {problem}\n{GET_TOKEN}")
    run_bots([(make_bot(cfg, character, db, db_path), token)], [db], {}, None, use_tui)


if __name__ == "__main__":
    main()
