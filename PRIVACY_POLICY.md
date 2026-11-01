# Privacy Policy: Pururin Collective character bots

**Effective date:** 1 November 2026

This policy explains what the AI character bots run for the Pururin Collective Discord server collect, why, and what you can do about it. Currently these are "Oomuro Sakurako", "Oomuro Nadeshiko", "Oomuro Hanako", "Shima Rin", "Ijichi Nijika", "Gotoh Hitori", "Yamada Ryo", "Kita Ikuyo", "Hitori Bocchi", "Raymond Reddington", "Naruto Uzumaki" and "GLaDOS" (each a "Bot", together the "Bots"). The Bots are run by the owner of the Pururin Collective Discord server (the "Operator").

**In short:** The Bots save messages from the channels they can read, and their DMs, so they can remember conversations. Each Bot keeps its own database on the Operator's own computer. Your messages are processed by an AI model on that computer, not by a cloud AI service. Nothing is sold or shared. You can delete your data at any time with the `/forgetme` command on any of the Bots.

## 1. What the Bots collect

**They do not only collect messages sent to them.** The Bots save every normal message in the channels they watch. By default a Bot watches every channel it can see in servers it has been added to, plus DMs with that Bot. The server can limit this to specific channels.

- **One copy per Bot.** Each Bot has its own database, so a message seen by two Bots is stored twice, once by each.
- **Bots read each other.** Each Bot can read what the other Bots said, stores it like any other message, and uses it in its replies.

For each message the Bots store:

| Data | Example |
|---|---|
| Message text | Mentions are turned into display names. It also notes who the message replies to. A forwarded message's text is stored too. |
| Attachments and stickers | The file name or sticker name only, not the file itself (also for forwarded messages) |
| Your Discord user ID and current display name | `123456789012345678`, `Hikari` |
| Message, channel and server IDs | Used to keep conversations apart and to handle edits and deletions |
| When the message was sent | `2026-10-29T10:15:00Z` |
| Which Bot wrote it | Only for the Bots' own messages |

They also keep:

- **Conversation summaries.** For each channel, the AI writes short notes that summarize older messages from the character's point of view. This is each Bot's long-term memory. The notes can include facts people mentioned about themselves, such as names, hobbies or allergies.
- **Missed messages.** When a Bot starts up, it fetches up to 50 recent messages per channel that arrived while it was offline. This only covers channels that Bot has already saved messages from, and the messages are stored the same way.

**Images.** When a Bot replies to a message, it downloads the images in it and shows them to the AI model for that one reply. This covers attached images, images in forwarded messages, and pictures in link previews. It also covers the message it replies to, and a message the same person posted up to 2 minutes before. Images are not saved.

**Web searches.** When a question needs current information, a Bot can search the web.

- **What is sent.** The AI writes a short search query based on the conversation, so it can include words from your message. The query is sent to the search provider: DuckDuckGo by default, or a SearXNG or Brave Search server if the Operator configures one. The Bot then downloads the top result pages, and those websites see an ordinary visit from the Operator's internet connection.
- **What is saved.** The query and the page text are used for that one reply and are not saved. The Bot's "let me look that up" message and its answer are stored like its other messages.

**Not collected:** server member lists, online status or activity, voice, or any data from outside Discord. The Bots don't ask for the Server Members or Presence intents.

## 2. Why it is used

All of this is used only to:

- Write the Bots' replies in character, using recent conversation for context.
- Let the Bots remember conversations after they restart.
- Fix problems and keep the Bots from being abused.

It is not used for advertising or profiling, and it is not sold.

## 3. Where it goes

- **Discord.** Messages travel through Discord as usual; see [Discord's Privacy Policy](https://discord.com/privacy).
- **The Operator's computer.** Stored data lives in local SQLite databases, one per Bot, in a `context` folder (for example `context/sakurako.db`, `context/rin.db` and `context/nijika.db`). The databases are encrypted with AES-256 (SQLCipher). Each one is locked with its Bot's Discord bot token, which is also kept on the Operator's computer. So the encryption protects copies of the database files, such as backups, but not against someone with full access to that computer.
- **The AI model.** Message text and images are sent to an AI model (LM Studio) running on a computer on the Operator's private home network. No cloud AI service such as OpenAI, Anthropic or Google receives your messages.
- **Search providers.** Search queries go to the web search provider described in section 1, under that provider's own privacy policy.
- **Nobody else.** Data is not shared with other third parties, except where the law requires it.
- **Logs.** The Bots print status information, such as channel names and errors, to the Operator's console. These logs are not saved to a file by default.

## 4. How long it is kept

- Stored messages and summaries are kept until the Operator deletes them. They do not expire automatically.
- **Deletions.** If a message is deleted on Discord while a Bot that can see it is online, it is removed from that Bot's database. This includes moderators bulk-deleting messages. A Bot that was offline at the time keeps its copy.
- **Edits.** Edits to recent messages update the stored copy. Edits to older messages may not be picked up.
- **Summaries.** A summary is not rewritten when a single message it covers is deleted. That information stays until the summary is cleared or you use `/forgetme`.
- **Removing a Bot.** If a Bot is removed from a server, it stops collecting there, but data already stored is kept until it is deleted.

## 5. Your choices and rights

- **Delete everything with `/forgetme`.** Run the `/forgetme` slash command with any of the Bots, in a server or in a DM. Only you see the reply. It shows how many of your messages are stored, and after you confirm it deletes, straight away and from **every** Bot's database:
  - every stored message you wrote, in every server;
  - your whole DM history with every Bot;
  - the Bots' own replies that mention any of your names;
  - you from every Bot's memory summaries. An AI rewrites each summary that mentions your name to leave you out. If the rewrite still mentions you, or fails, that summary is deleted entirely.

  What `/forgetme` does not delete:
  - messages other people wrote, even if they mention you;
  - summary notes that refer to you without using your username, global name or display name;
  - anything you send after running it. New messages are stored again.
- **Don't talk near them.** The Bots only save messages in channels they can read. Server staff can restrict them to certain channels.
- **Delete a message.** Deleting a message removes it from the database of every Bot that is online at the time.
- **Ask the Operator.** For a copy of the messages stored under your user ID, or for anything `/forgetme` doesn't cover, contact the Operator. Requests are handled by hand, usually within 30 days.

Depending on where you live, data-protection law (for example the EU/UK GDPR) may give you more rights. These include the right to access, correct and delete your data and to object to its processing. Contact the Operator to use them.

## 6. Children

The Bots are meant only for people who meet Discord's minimum age where they live. If you think the Bots have stored messages from someone under that age, contact the Operator and the messages will be deleted.

## 7. Changes

This policy may be updated when the Bots change, including when characters are added or removed. The new version will be published in this file with a new effective date.

## 8. Contact

Privacy questions and data requests: contact the owner or staff of the Pururin Collective Discord server.
