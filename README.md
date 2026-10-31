# Character bots

AI characters from anime and TV that chat in the Pururin Collective Discord server, powered by your local LM Studio model:

| Character | From | Persona | Config | Memory |
|---|---|---|---|---|
| Oomuro Sakurako (大室櫻子) | *Yuru Yuri* | [persona/sakurako.md](persona/sakurako.md) | `config/sakurako.json` | `context/sakurako.db` |
| Shima Rin (志摩リン) | *Yuru Camp△* | [persona/rin.md](persona/rin.md) | `config/rin.json` | `context/rin.db` |
| Ijichi Nijika (伊地知虹夏) | *Bocchi the Rock!* | [persona/nijika.md](persona/nijika.md) | `config/nijika.json` | `context/nijika.db` |
| Gotoh Hitori (後藤ひとり) | *Bocchi the Rock!* | [persona/gotoh.md](persona/gotoh.md) | `config/gotoh.json` | `context/gotoh.db` |
| Hitori Bocchi (一里ぼっち) | *Hitoribocchi no ○○ Seikatsu* | [persona/bocchi.md](persona/bocchi.md) | `config/bocchi.json` | `context/bocchi.db` |
| Raymond "Red" Reddington | *The Blacklist* | [persona/raymond.md](persona/raymond.md) | `config/raymond.json` | `context/raymond.db` |
| Naruto Uzumaki (うずまきナルト) | *Naruto* / *Boruto* | [persona/naruto.md](persona/naruto.md) | `config/naruto.json` | `context/naruto.db` |
| GLaDOS | *Portal* | [persona/glados.md](persona/glados.md) | `config/glados.json` | `context/glados.db` |

- Each character is a separate Discord bot with its own name, avatar and token.
- Each runs in its own process, with its own config file and its own memory database. After a restart, each picks up where it left off.

## Setup

The characters run as Discord **bot accounts**. Automating a normal user account like `oomuro.sakurako` (a "self-bot") breaks Discord's Terms of Service and can get that account banned.

Repeat steps 1–3 once per character:

1. Go to <https://discord.com/developers/applications>, click **New Application**, and name it after the character, e.g. `Shima Rin`.
2. On the **Bot** tab:
   - Set the username and avatar.
   - Click **Reset Token** and paste the token into her config file, e.g. `config/rin.json` → `discord.token`. A bot token has three parts separated by dots. The Public Key, Application ID or Client Secret won't work.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent**.
3. Invite her to your server. Replace `APP_ID` with the Application ID from the **General Information** tab:
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=274877975552`
   (permissions: View Channels, Send Messages, Send Messages in Threads, Read Message History)

Then:

4. Make sure LM Studio's server is running at `http://192.168.88.193:1234` with a model loaded.
5. Start every character together in one window. They take turns (see **Taking turns** below):

   ```
   run.bat --char all
   ```

   Or start one character on her own:

   ```
   run.bat --char rin
   ```

The first run creates `.venv`. Every start installs anything missing from `requirements.txt` (`discord.py`, `pillow`, `ddgs`, `rich`).

`--char <name>` loads `config/<name>.json` and keeps memory in `context/<name>.db`. You can also give the file directly: `run.bat --config config/rin.json`.

`--char all` runs every `config/<name>.json` except `config/example.json`, so a new character is included automatically. Characters whose token isn't set yet are skipped with a warning. All of them share the one model loaded in LM Studio.

**To stop, press `Q` in the window.** Everyone logs out of Discord and their memories are saved cleanly. With a single character, `Q` stops just her. Ctrl+C still works, but `Q` is the tidy way.

Don't run the same character twice (for example `--char all` plus `--char rin` in another window). She'd answer every message twice.

## Status screen

While the bots run, the window shows a live status screen ([tui.py](tui.py)) instead of scrolling log lines:

```
 VonikoDiscord  LM Studio 192.168.88.193:1234 · huihui-qwen3.8-27b-abliterated · vision · context 50,688     01:38:36
┌─ Characters ──────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Character   Discord          Doing                Context (compacts at 75%)              Memory                   │
│ ───────────────────────────────────────────────────────────────────────────────────────────────────────────────── │
│ Nijika      ● online 42 ms   writing in #gener…   ████████░░  79% 39.8k/50.7k #general   238 msgs · 1 note        │
│ Rin         ● online 40 ms   idle                 ░░░░░░░░░░   4% 2.0k/50.7k #general    249 msgs · 1 note        │
│ Sakurako    ● online 41 ms   idle                 compacted; measured on next reply      compacted 2x · 188 msgs… │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ Turns ───────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ #general  Nijika is answering Anime4000; next: Sakurako                                                    3s ago │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ Log ─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 01:38:36 INFO director: Nijika, Sakurako answer(s) Anime4000, in that order                                       │
│ 01:38:36 INFO brain: Compacting Sakurako's memory of #general (the prompt was 38,410 of 50,688 tokens)            │
│ 01:38:36 INFO brain: Updated Sakurako's memory of #general (52 message(s) folded in, 10 kept word for word)       │
└───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 Q  quit   the log is printed again after quitting                                                         up 0:12:04
```

- **Characters:** whether she's online, what she's doing (writing, searching the web, compacting her memory), and how big her latest prompt was compared to the model's context. The bar turns yellow as it nears `compact_at` and red when it gets there. Windows at least 116 columns wide also show Memory (stored messages, memory notes, how many times she has compacted), and at least 144 columns wide, Replies (how many, when the last one was, and how many tokens she thought before it). The header shows the thinking level and the model's speed.
- **Turns** (with `--char all`): what the director is doing in each channel: who is answering, who's next, or who is waiting.
- **Log:** the newest log lines. When you quit, the screen closes and the last 2,000 log lines are printed back into the window.

`--no-tui` prints plain log lines instead, as before, for example when you redirect the output to a file. The screen is also switched off automatically when the output isn't a console window.

## Taking turns

With `--char all`, a neutral **director** ([director.py](director.py)) decides who speaks in each channel, so the characters don't all write at once and talk past each other:

1. **Wait:** it waits until the chat has been quiet for `debounce_seconds`, so a few quick messages in a row get one answer.
2. **Choose:** it picks who answers and in what order:
   - whoever is replied to or @mentioned goes first;
   - then the others who were called, in the order their names come up;
   - everyone else just reads.
3. **One at a time:** only one character writes at a time, and only she shows "typing…". The next speaker starts after the previous one has posted, with that reply already in front of her.
4. **Characters answering characters:** if a reply calls another character by name, she answers next, about a second later, up to `max_bot_chain` messages between characters in a row (their answers to a person don't count).
5. **Interruptions:** if a person says something new while a round is going, the rest of that round is dropped, and the characters answer the new message instead.

Running characters one at a time (`--char rin`) works as before, without turn-taking: each answers as soon as she's called.

## When they reply

Each character replies:

- when someone @mentions her, replies to one of her messages, or DMs her;
- when someone calls her by one of her `name_triggers` (e.g. `sakurako`, `櫻子`, `rin`, `しまりん`);
- when someone carries on talking with her (see below);
- to every message in her `free_talk_channel_ids`, and to a `random_reply_chance` share of other messages.

**Carrying on a conversation.** Once she has answered you, you don't have to say her name or press Reply for your next messages. She answers them if all of these are true:

- she was the last character to answer you in that channel, less than `follow_up_minutes` ago (default 5);
- nobody else has said anything since (other characters don't count);
- your new message doesn't call another character by name, @mention anyone, or reply to someone else's message.

So "rin, how's school?" → Rin answers → "oh really? let me check" goes to Rin. If someone else joins in, or you turn to Naruto ("naruto, what about you?"), call her by name again to get her back. Set `follow_up_minutes` to `0` to turn this off. A character only knows what happened since she started, so after a restart, call her by name first.

**What counts as calling her by name.** Her name has to be one of the first 3 words of a sentence or its last word:

- **Counts:** "Rin, …", "hey Rin, …", "…what do you think, Rin?", "櫻子、…".
- **Doesn't count:** just mentioning her ("nijika, can you ask sakurako about rin camping?" calls only Nijika).
- **Honorifics:** like `-chan`, `-sama`, `-niichan`, `-ojisan`, ちゃん, 様 and 兄ちゃん are fine.
- **Similar words:** "Rin's", "string" or "プリン" never call Rin.
- **Stutters:** "N-Nijika-chan" and "b-b-bocchi" count.
- **Full names win:** a name inside another character's full name doesn't call her. "Gotoh Hitori" calls only Gotoh, even though "hitori" is Hitori Bocchi's trigger.
- **The two Bocchis:** "Bocchi" and "Hitori" (ぼっち, 一里) call Hitori Bocchi. Bocchi the Rock!'s Gotoh Hitori answers to "Gotoh" (後藤, ごとう) or "guitarhero". Nijika knows to call her bandmate "Gotoh-chan" here.

Call several at once ("Sakurako and Nijika, come here!") and they'll all answer. A list of names counts as a whole when it starts or ends the sentence, if it has an "and" (or "&", "or", "dan") in it: "bocchi, my friends want to meet you: gotoh hitori, ijichi nijika, shima rin and naruto" calls all five. They answer in the order you named them. "hitori, gotoh wants to be friends" has no "and", so it calls only Bocchi.

**Characters talking to each other.** Each character knows who the other characters are (from the other files in `config/`).

- **Asking another character:** asked to ask another character something, she calls her by name ("Sakurako, can you tell Anime4000…"), and the other one answers. This is `reply_to_characters`, on by default.
- **Chain limit:** after `max_bot_chain` (default 3) messages between characters, no character starts another reply until a person speaks, so they can't talk forever. Their answers to a person (the ones quoting your message) don't count, so after five of them greet you, they still have 3 messages to talk among themselves.
- **`reply_to_bots`:** with this on, she answers any bot that calls her, not only the other characters. The same chain limit applies.

**She always knows which message she's answering**, even when several people (or characters) talked in between.

**Replies to other people's messages.** She can be asked about another member's message by replying to it with her name, e.g. "nijika, can you look why it crashes?". She sees the first 300 characters of the replied-to message, plus its images, so a reply to someone's screenshot works.

Each waits `debounce_seconds` for people to finish typing and then answers once. She answers a person with Discord's reply feature, so they're tagged (`always_reply`, with `mention_author` for the ping). In DMs, and when answering another character, she only does that if other messages arrived in the meantime.

**Formatting.** Characters can use Discord markdown when it fits: **bold** and *italic* for stress, ~~strikethrough~~, `||spoilers||`, `inline code` and code blocks with a language (```` ```python ````). They're told not to format every message. Code can run past the usual 1-3 sentences. A reply over Discord's 2000-character limit is sent in parts. A code block cut in two is closed at the end of one part and reopened, with its language, at the start of the next, so both parts still show as code.

**Actions.** Now and then a character does something as well as saying it. The action goes on its own first line as a Discord quote, never in `*asterisks*`:

```
> points at the guitar
hitori, your guitar needs tuning
```

Names in a quote line never call anyone, whether it's a character's action ("> hides behind Nijika") or a person quoting someone else's words. The line after it still does: the example above calls Hitori Bocchi.

**Tagging people.** A character can tag someone by writing @ and their display name, e.g. "Nijika, call Ryukura over" → "@Ryukura hey! …". The bot turns that into a real Discord mention for anyone who has talked in that channel (and isn't forgotten with `/forgetme`). @everyone and @here are never pinged.

**Images.** She looks at up to 4 images when `llm.vision` is on and LM Studio's loaded model is a vision model (type `vlm` in LM Studio, e.g. `huihui-qwen3.8-27b-abliterated`). They come from the message she's answering, the message it replies to, and the message its author posted just before it (up to 2 minutes earlier). That last one covers forwards: Discord sends the comment you add to a forward as a separate message, and people often post a picture and then ask about it.

- Attached images, images in forwarded messages, and the picture in a link preview (e.g. a tweet) all count. Forwarded messages are also stored as text, as `[forwarded message: ...]`.

- Each image is converted to a JPEG of at most 1024 px first, because LM Studio rejects WebP and large photos are slow.
- With a text-only model (e.g. `qwen3.8-27b-defiance-uncensored-mtp`), images are skipped. She only sees the file name.
- When `llm.model` is empty, the bot checks every 30 seconds which model is loaded, so you can swap models in LM Studio without restarting her.

## Web search

The characters can look things up online: news, release dates, prices, weather, opening hours, and so on. The model decides when a question needs it; small talk is answered straight away.

1. She posts a quick in-character line from her `web_search.waiting_messages` (or one the model writes itself, e.g. in Japanese), such as "Wait wait! Sakurako-sama is checking the interweb!".
2. The bot searches (DuckDuckGo by default, no API key needed) and reads the text of the top `read_pages` results.
3. She answers in character with what she found, usually 10–20 seconds after the question.

**Limits:**

- **Searches.** She gets at most `max_searches` search rounds per reply (default 1). Raise it to 2 for harder questions. Each round also thinks first (see `llm.reasoning_effort`), so a reply with a search takes about 15 seconds.
- **Not searched.** She won't search about herself (she already knows her own story) or for anything sexual. The search provider's SafeSearch is set to strict (`safesearch: "on"`).
- **Local network.** Result pages on your local network (localhost, `192.168.x.x`, ...) are never fetched.
- **Untrusted pages.** Page text is marked as untrusted, and she's told never to follow instructions inside it.

**How the default provider works:** it uses the `ddgs` package, which scrapes several free search engines: real Wikipedia first, then DuckDuckGo, with Yahoo, Brave and Startpage as backups. These scrapers sometimes come back empty, especially after many searches in a short time. If every engine fails, she says she couldn't look it up.

**Other providers:** set `web_search.provider` to `"searxng"` with your own `searxng_url`, or to `"brave"` with a free `brave_api_key` from <https://brave.com/search/api/>. Both are official APIs, so they're more reliable. Set `web_search.enabled` to `false` to turn searching off for a character.

## Slash commands

`/forgetme` lets any member delete their own stored data, for **every** character. It works with any of the bots, in servers and in DMs, and only the person who runs it sees the reply. It goes through every `.db` file in the `context` folder. It shows how many of their messages are stored and asks them to confirm. Confirming deletes:

- their messages;
- their DM history with every character;
- the characters' replies that mention their name;
- them from every character's memory summaries.

The [privacy policy](PRIVACY_POLICY.md) lists exactly what it does and doesn't remove.

The command is registered every time a bot starts. If a new command doesn't appear in Discord, restart Discord (Ctrl+R).

## Memory (`context/<char>.db`)

- **Message log.** Every message in the channels a character watches is saved in her database. Edits update the saved copy, and deleted messages are removed.
- **Long-term memory.** The last `history_messages` messages per channel go into the prompt word for word. Older ones are folded into a per-channel summary written from her point of view.
- **Compacting when the context fills up.** LM Studio reports how many tokens the loaded model's context holds (`loaded_context_length`), and every reply reports how big the prompt was. Compaction folds all but the newest `keep_after_compact` messages into her memory notes. It happens in three cases:
  - when a prompt uses `compact_at` of the context (75% by default), right after she replies;
  - before sending, if a prompt would be too big to fit, leaving room for her reply. If it still doesn't fit after compacting, the oldest messages are left out;
  - when LM Studio rejects a prompt as too long, before she tries once more.

  The notes are written in batches small enough for the model's context too. Compactions show in the log and on the status screen.
- **Catch-up after a restart.** On startup she fetches up to `catch_up_limit` messages she missed while offline. If the newest one was addressed to her and is less than `catch_up_reply_max_age_minutes` old, she answers it.
- **Older memory files.** Earlier versions kept memory next to `bot.py` as `context.db` (Sakurako only), then `context.<char>.db`, and later in `context/` as `context.<char>.db`. The next time a character starts, her file is moved to `context/<char>.db` (and upgraded if needed). Stop the running copy of her first.
- **To wipe a character's memory**, stop her and delete `context/<char>.db`.

## Talk to them in the terminal

```
run.bat --char rin --cli
```

This uses the same brain and memory database, in a private "terminal" channel. Type `/forget` to clear that chat and `/quit` to exit. You can also send a single message with `--say "hi"`.

## config/&lt;char&gt;.json

Paths in it (`persona_file`, `db_path`) are relative to the project folder. Older versions kept these files next to `bot.py` as `config.<char>.json`; they're moved into `config/` the next time you run it.

| Key | Meaning |
|---|---|
| `character.id` | Short id, also used for her `context/<id>.db`. Don't change it once she has memories |
| `character.name`, `full_name` | Name used in prompts, and for removing a "Name:" prefix the model sometimes copies into replies |
| `character.series` | Her series, shown to the other characters so they know who she is |
| `character.persona_file` | Her character sheet |
| `discord.token` | Her bot token (keep it secret; `config/*.json` is in `.gitignore`, except `example.json`) |
| `discord.name_triggers` | Words that count as calling her |
| `discord.status_text` | Her Discord status |
| `discord.guild_ids` | Only work in these server IDs. Empty = any server she's in |
| `discord.channel_ids` | Only read/reply in these channels (threads inside them included). Empty = every channel she can see |
| `discord.free_talk_channel_ids` | Channels where she joins every conversation without being called |
| `discord.follow_up_minutes` | How long after she answers someone their next message still goes to her without her name (default `5`, `0` = off). See **Carrying on a conversation** |
| `discord.reply_to_dms` | Answer DMs |
| `discord.reply_to_characters` | Answer another bot (e.g. another character) when it calls her by name, @mentions her or replies to her |
| `discord.reply_to_bots` | Answer any bot the way she answers people (name, free-talk channels, random chance) |
| `discord.max_bot_chain` | After this many messages between characters (or other bots), no character starts another reply until a person speaks (default 3). Answers to a person don't count |
| `discord.random_reply_chance` | 0.0–1.0 chance to jump into any other message |
| `discord.mention_author` | Ping the person when she uses Discord's reply feature |
| `discord.always_reply` | Answer people in server channels with Discord's reply feature, quoting their message (default `true`). With `mention_author` on, that tags them. `false` = only when other messages came in between. Other characters' messages are never quoted just for this, since a reply to a character calls it back |
| `discord.catch_up_*`, `reply_to_missed` | Resume behaviour after a restart |
| `llm.base_url` | LM Studio server |
| `llm.model` | Model id. Empty = whichever chat model LM Studio has loaded (re-checked every 30 seconds) |
| `llm.vision` | Show her attached images (only works with a vision model) |
| `llm.context_length` | Tokens the model's context holds. `0` = ask LM Studio (the context length the model was loaded with). Set it if your LM Studio is too old to report it |
| `llm.reasoning_effort` | How much the model thinks before replying: `"low"` (default), `"medium"`, `"high"`, `"none"` (off) or `""` (the model decides). With this model, low thinks about 100–350 tokens and makes a reply take 7–15 seconds instead of 3–5, but she answers questions properly instead of brushing them off. Medium thinks about as much as low. Memory notes are always written without thinking, so they don't hold up replies. If thinking ever uses up `max_tokens` before she answers, she tries once more without thinking |
| `llm.max_tokens` | Longest reply, thinking included (default `4096`). Leave room for thinking: at `2048` a long think can leave nothing for the answer |
| `llm.temperature`, `top_p`, `extra_params` | Sampling settings (`extra_params` is merged into the API request as-is) |
| `web_search.enabled` | Let her search the web |
| `web_search.provider` | `"duckduckgo"` (no key), `"searxng"` (with `searxng_url`) or `"brave"` (with `brave_api_key`) |
| `web_search.safesearch` | `"on"` / `"moderate"` / `"off"` |
| `web_search.region` | `"country-language"`, e.g. `"us-en"` (default), `"my-en"`, `"jp-jp"`. The language part also picks which Wikipedia is used (`jp-jp` → ja.wikipedia.org). Don't use DuckDuckGo's `"wt-wt"`: it makes ddgs look up `wt.wikipedia.org`, which doesn't exist |
| `web_search.backend` | Engines the `duckduckgo` provider asks: `"wikipedia,duckduckgo,yahoo,brave,startpage"` by default. Available: `wikipedia`, `duckduckgo`, `yahoo`, `brave`, `startpage`, `mojeek`, `grokipedia`. `"auto"` adds Grokipedia (xAI's AI-written encyclopedia) and random others |
| `web_search.max_results`, `read_pages`, `max_page_chars` | How many results she sees, how many top pages she reads, and how much of each page |
| `web_search.max_searches` | Search rounds per reply |
| `web_search.waiting_messages` | What she says while she looks something up (one is picked at random) |
| `memory.history_messages` | Messages kept word for word in the prompt |
| `memory.summarize_batch` | How many extra messages pile up before older ones are summarized |
| `memory.compact_at` | Compact her memory of a channel once a prompt there uses this share of the context (default `0.75`) |
| `memory.keep_after_compact` | Messages kept word for word after compacting (default `10`) |
| `memory.db_path` | Optional. Defaults to `context/<id>.db`. Keep every character's database in the same folder, so `/forgetme` finds them all |
| `cli.user_name` | Your name in terminal mode (defaults to your Windows user name) |

Get channel and server IDs by turning on **Developer Mode** (Discord Settings → Advanced), then right-clicking a channel or server → **Copy ID**.

## Adding another character

1. Copy [persona/example.md](persona/example.md) to `persona/<id>.md` and fill it in: Profile, Your story (optional), Personality, Friends, How you talk. The notes in it (`<!-- ... -->`) explain each part and are removed before the file reaches the model. [persona/rin.md](persona/rin.md) is a finished example.
   - To work from the character's wiki article, save the page into `dump/` (browser **Save page as**) and run `.venv\Scripts\python wiki2md.py`. It writes `profile/<first name>.md`: just the article text (infobox, sections, trivia), a few KB instead of several MB of site code. Pages about the same character from different wikis go into one file. Rewrite it in your own words for the persona; don't paste it.
2. Copy `config/example.json` to `config/<id>.json`. Set the `character` block, `discord.token`, `name_triggers`, `status_text` and `web_search.waiting_messages`.
3. Create and invite the bot as in **Setup**, then run `run.bat --char <id>`. From now on `run.bat --char all` starts it too.
4. Add the character to the list in the Terms of Service and Privacy Policy, and add the wiki source and copyright line to `persona/LICENSE.md`.

## Terms and privacy

- [TERMS_OF_SERVICE.md](TERMS_OF_SERVICE.md) and [PRIVACY_POLICY.md](PRIVACY_POLICY.md) cover all the characters.
- Once the repo is on GitHub, paste their links into each bot's Developer Portal page: **General Information** → **Terms of Service URL** and **Privacy Policy URL**.
- If you change what the bots store or where data goes (for example `channel_ids`, `catch_up_limit`, `vision` or `web_search.provider`), update the privacy policy to match.

## License

- **Code and everything else** (`*.py`, `run.bat`, `config/example.json`, `persona/example.md`, the docs): [MIT](LICENSE).
- **Character sheets** (the other `persona/*.md`): [CC BY-SA 4.0](persona/LICENSE.md), because they're based on Fandom wiki articles under CC BY-SA. That file credits the wiki pages.
- **The characters** (Oomuro Sakurako, Shima Rin, Ijichi Nijika, Gotoh Hitori, Hitori Bocchi, Raymond Reddington, Naruto Uzumaki, GLaDOS), their names and their series belong to their creators and publishers, and neither license covers them. This is an unofficial fan project, not affiliated with or endorsed by them.
- **Never publish** `dump/` (saved wiki pages, including Fandom's site code and images), `config/*.json` (bot tokens) or `context/` (members' messages). All three are in `.gitignore`.
- `profile/` (the wiki text from `wiki2md.py`, copied word for word) is in `.gitignore` too, so it stays out of the MIT-licensed repo.

When you add a character, add the wiki source and copyright line to [persona/LICENSE.md](persona/LICENSE.md).

## Files

- `bot.py`: Discord bot and terminal mode
- `brain.py`: builds the character's prompt, cleans replies, writes long-term memory
- `llm.py`: LM Studio API client
- `websearch.py`: the web search tool (DuckDuckGo / SearXNG / Brave, plus reading result pages)
- `director.py`: turn-taking when all characters run together (`--char all`)
- `tui.py`: the status screen
- `memory.py`: the `context/<char>.db` memory databases (SQLite)
- `persona/*.md`: character sheets
- `persona/example.md`: template for a new character sheet
- `wiki2md.py`: turns saved wiki pages (`dump/*.html`) into short Markdown (`profile/*.md`)
- `config/example.json`: template for a new character's config
