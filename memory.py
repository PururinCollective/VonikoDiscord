"""context/<character>.db: persistent chat memory so a character can pick up where she left off after a restart.

Each character has her own database: every message she sees, plus a rolling per-channel summary of older
messages (her "long-term memory"), so the prompt stays small while nothing is forgotten.

The databases are encrypted with SQLCipher (AES-256, every page checked with HMAC-SHA512). The key is her bot
token, stretched with PBKDF2-HMAC-SHA512, so the file is unreadable without her config.
"""
import itertools
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlite3  # SQLite with encryption, same API as the standard sqlite3

log = logging.getLogger("memory")
PLAIN_HEADER = b"SQLite format 3\x00"  # how an unencrypted SQLite file starts; an encrypted one looks random

DISCORD_EPOCH_MS = 1420070400000
_counter = itertools.count()

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id  INTEGER PRIMARY KEY,          -- Discord snowflake (time-ordered)
    channel_id  INTEGER NOT NULL,
    guild_id    INTEGER,
    author_id   INTEGER,
    author_name TEXT NOT NULL,
    character   TEXT,                         -- id of the character who wrote it; NULL = a person or other bot
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id, message_id);

CREATE TABLE IF NOT EXISTS summaries (
    character   TEXT NOT NULL,
    channel_id  INTEGER NOT NULL,
    summary     TEXT NOT NULL,
    upto_id     INTEGER NOT NULL,             -- messages up to this id are covered by the summary
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (character, channel_id)
);
"""

# The first version had an is_self flag instead of a character column.
MIGRATE_V1 = """
BEGIN;
DROP INDEX IF EXISTS idx_messages_channel;
ALTER TABLE messages RENAME TO messages_v1;
ALTER TABLE summaries RENAME TO summaries_v1;
""" + SCHEMA + """
INSERT INTO messages SELECT message_id, channel_id, guild_id, author_id, author_name,
    CASE WHEN is_self THEN {owner} END, content, created_at FROM messages_v1;
INSERT INTO summaries SELECT {owner}, channel_id, summary, upto_id, updated_at FROM summaries_v1;
DROP TABLE messages_v1;
DROP TABLE summaries_v1;
COMMIT;
"""


def make_snowflake() -> int:
    """Time-ordered id for messages that don't come from Discord (terminal chat)."""
    ms = int(time.time() * 1000) - DISCORD_EPOCH_MS
    return (ms << 22) | (next(_counter) & 0x3FFFFF)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WrongKey(Exception):
    """The database is encrypted with a different key: her bot token was reset since."""


def connect(path: str, key: str) -> sqlite3.Connection:
    """Open an encrypted database (a new one is created encrypted). Raises WrongKey if the key doesn't fit."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA cipher_log_level = NONE")  # it would print its own errors over the status screen
    conn.execute("PRAGMA key = '%s'" % key.replace("'", "''"))  # PRAGMA can't take a bound parameter
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError:  # "file is not a database": the key doesn't decrypt it
        conn.close()
        raise WrongKey(path) from None
    return conn


def is_plain(path: str) -> bool:
    """An unencrypted database, from before memory was encrypted."""
    try:
        with open(path, "rb") as f:
            return f.read(len(PLAIN_HEADER)) == PLAIN_HEADER
    except FileNotFoundError:
        return False


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {t: conn.execute('SELECT count(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0] for t in tables}


def reencrypt(path: str, old_key: str | None, new_key: str) -> None:
    """Write the database again, encrypted with new_key: a plain one (old_key None) or one encrypted with
    old_key. The copy is checked table by table before it replaces the original. Raises WrongKey if old_key
    doesn't fit, PermissionError if the database is still open in another program."""
    tmp = path + ".new"
    Path(tmp).unlink(missing_ok=True)
    source = connect(path, old_key) if old_key else sqlite3.connect(path)
    try:
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # everything into the main file, nothing left in -wal
        source.execute("ATTACH DATABASE ? AS target KEY ?", (tmp, new_key))
        source.execute("SELECT sqlcipher_export('target')")
        source.execute("DETACH DATABASE target")
        expected = table_counts(source)
    finally:
        source.close()
    check = connect(tmp, new_key)
    try:
        copied = table_counts(check)
    finally:
        check.close()
    try:
        if copied != expected:
            raise RuntimeError(f"encrypting {path} lost rows ({expected} became {copied}); it was left as it was")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):  # the old file's (empty) journal files don't belong to the new one
        Path(path + suffix).unlink(missing_ok=True)


class ContextDB:
    def __init__(self, path: str, owner: str, key: str):
        """`owner` is the id of the character this database belongs to, `key` what it's encrypted with (her
        bot token). A database from before encryption is encrypted with it right away."""
        self.owner = owner
        if is_plain(path):
            reencrypt(path, None, key)
            log.info("Encrypted %s with %s's bot token", Path(path).name, owner)
        self.conn = connect(path, key)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(messages)")}
        quoted_owner = "'" + owner.replace("'", "''") + "'"
        self.conn.executescript(MIGRATE_V1.format(owner=quoted_owner) if "is_self" in columns else SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- messages -------------------------------------------------------------------------

    def add_message(self, message_id: int, channel_id: int, guild_id: int | None, author_id: int | None,
                    author_name: str, content: str, character: str | None = None,
                    created_at: str | None = None) -> None:
        """Store a message (idempotent; a later copy that knows which character wrote it fills that in)."""
        self.conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(message_id) DO UPDATE SET "
            "character = COALESCE(messages.character, excluded.character)",
            (message_id, channel_id, guild_id, author_id, author_name, character, content,
             created_at or now_iso()),
        )
        self.conn.commit()

    def edit_message(self, message_id: int, content: str) -> None:
        self.conn.execute("UPDATE messages SET content = ? WHERE message_id = ?", (content, message_id))
        self.conn.commit()

    def delete_message(self, message_id: int) -> None:
        self.conn.execute("DELETE FROM messages WHERE message_id = ?", (message_id,))
        self.conn.commit()

    def get_message(self, message_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()

    def recent_messages(self, channel_id: int, after_id: int, limit: int) -> list[sqlite3.Row]:
        """The newest `limit` messages after `after_id`, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND message_id > ? ORDER BY message_id DESC LIMIT ?",
            (channel_id, after_id, limit),
        ).fetchall()
        return rows[::-1]

    def people(self, channel_id: int, limit: int = 500) -> dict[str, int]:
        """Display name -> user id of everyone who wrote among the newest `limit` messages here (the newer
        name wins). Someone who used /forgetme isn't in it."""
        rows = self.conn.execute(
            "SELECT author_name, author_id FROM messages WHERE channel_id = ? AND author_id IS NOT NULL "
            "ORDER BY message_id DESC LIMIT ?", (channel_id, limit),
        ).fetchall()
        return {name: author_id for name, author_id in reversed(rows)}

    def oldest_messages(self, channel_id: int, after_id: int, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM messages WHERE channel_id = ? AND message_id > ? ORDER BY message_id LIMIT ?",
            (channel_id, after_id, limit),
        ).fetchall()

    def count_after(self, channel_id: int, after_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE channel_id = ? AND message_id > ?", (channel_id, after_id)
        ).fetchone()[0]

    def last_message_id(self, channel_id: int) -> int | None:
        return self.conn.execute(
            "SELECT MAX(message_id) FROM messages WHERE channel_id = ?", (channel_id,)
        ).fetchone()[0]

    def known_channels(self) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT channel_id FROM messages")]

    # --- long-term memory (one summary per character per channel) ------------------------------

    def get_summary(self, character: str, channel_id: int) -> tuple[str, int]:
        row = self.conn.execute(
            "SELECT summary, upto_id FROM summaries WHERE character = ? AND channel_id = ?", (character, channel_id)
        ).fetchone()
        return (row["summary"], row["upto_id"]) if row else ("", 0)

    def set_summary(self, character: str, channel_id: int, summary: str, upto_id: int) -> None:
        self.conn.execute(
            "INSERT INTO summaries VALUES (?, ?, ?, ?, ?) ON CONFLICT(character, channel_id) DO UPDATE SET "
            "summary = excluded.summary, upto_id = excluded.upto_id, updated_at = excluded.updated_at",
            (character, channel_id, summary, upto_id, now_iso()),
        )
        self.conn.commit()

    def counts(self) -> tuple[int, int]:
        """(messages stored, channels she has memory notes about), for the status screen."""
        messages = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        notes = self.conn.execute("SELECT COUNT(*) FROM summaries WHERE character = ? AND summary != ''",
                                  (self.owner,)).fetchone()[0]
        return messages, notes

    def all_summaries(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT character, channel_id, summary FROM summaries").fetchall()

    def forget_channel(self, channel_id: int) -> None:
        self.conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        self.conn.execute("DELETE FROM summaries WHERE channel_id = ?", (channel_id,))
        self.conn.commit()

    # --- per-user data (/forgetme) ----------------------------------------------------------

    def user_message_ids(self, user_id: int) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT message_id FROM messages WHERE author_id = ?", (user_id,))}

    def author_names(self, user_id: int) -> set[str]:
        """Every display name this user has had in stored messages."""
        return {r[0] for r in self.conn.execute(
            "SELECT DISTINCT author_name FROM messages WHERE author_id = ?", (user_id,))}

    def forget_user(self, user_id: int, names: set[str]) -> int:
        """Delete the user's messages, their whole DM history, and the character's replies that name them.
        Returns the number of messages deleted."""
        deleted = 0
        dm_channels = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT channel_id FROM messages WHERE author_id = ? AND guild_id IS NULL", (user_id,))]
        for channel_id in dm_channels:
            deleted += self.conn.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,)).rowcount
            self.conn.execute("DELETE FROM summaries WHERE channel_id = ?", (channel_id,))
        deleted += self.conn.execute("DELETE FROM messages WHERE author_id = ?", (user_id,)).rowcount
        for name in names:
            deleted += self.conn.execute(
                "DELETE FROM messages WHERE character IS NOT NULL AND instr(lower(content), lower(?)) > 0",
                (name,)).rowcount
        self.conn.commit()
        return deleted
