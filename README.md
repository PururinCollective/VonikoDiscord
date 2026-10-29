# Anime character bots

AI anime characters that chat in the Pururin Collective Discord server, powered by your local LM Studio model:

| Character | From | Persona | Config | Memory |
|---|---|---|---|---|
| Oomuro Sakurako (大室櫻子) | *Yuru Yuri* | [persona/sakurako.md](persona/sakurako.md) | `config.sakurako.json` | `context/context.sakurako.db` |
| Shima Rin (志摩リン) | *Yuru Camp△* | [persona/rin.md](persona/rin.md) | `config.rin.json` | `context/context.rin.db` |
| Ijichi Nijika (伊地知虹夏) | *Bocchi the Rock!* | [persona/nijika.md](persona/nijika.md) | `config.nijika.json` | `context/context.nijika.db` |

- Each character is a separate Discord bot with her own name, avatar and token.
- Each runs in her own process, with her own config file and her own memory database. After a restart she picks up where she left off.

## Setup

The characters run as Discord **bot accounts**. Automating a normal user account like `oomuro.sakurako` (a "self-bot") breaks Discord's Terms of Service and can get that account banned.

Repeat steps 1–3 once per character:

1. Go to <https://discord.com/developers/applications>, click **New Application**, and name it after the character, e.g. `Shima Rin`.
2. On the **Bot** tab:
   - Set the username and avatar.
   - Click **Reset Token** and paste the token into her config file, e.g. `config.rin.json` → `discord.token`. A bot token has three parts separated by dots. The Public Key, Application ID or Client Secret won't work.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent**.
3. Invite her to your server. Replace `APP_ID` with the Application ID from the **General Information** tab:
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands&permissions=274877975552`
   (permissions: View Channels, Send Messages, Send Messages in Threads, Read Message History)

Then:

4. Make sure LM Studio's server is running at `http://192.168.88.193:1234` with a model loaded.
5. Start each character in her own terminal. The first run creates `.venv`. Every start installs anything missing from `requirements.txt` (`discord.py`, `pillow`, `ddgs`).

   ```
   run.bat --char sakurako
   ```

   ```
   run.bat --char rin
   ```

   ```
   run.bat --char nijika
   ```

`--char <name>` loads `config.<name>.json` and keeps memory in `context/context.<name>.db`. You can also give the file directly: `run.bat --config config.rin.json`.

**To stop a character, press `Q` in her window.** She logs out of Discord and saves her memory cleanly. Ctrl+C still works, but `Q` is the tidy way.

## When they reply

Each character replies:

- when someone @mentions her, replies to one of her messages, or DMs her;
- when a message contains one of her `name_triggers`, e.g. `sakurako`, `櫻子`, `rin` or `しまりん`. Latin names must match a whole word, so `rin` doesn't fire on "string";
- to every message in her `free_talk_channel_ids`, and to a `random_reply_chance` share of other messages.

Mention both characters ("Sakurako, Rin, ...") and both will answer.

Characters are bots, so with `reply_to_bots` off (the default) they never answer each other. Each still reads and stores what the others said, and can react to it in her next reply.

**Don't turn on `reply_to_bots` for two characters who share a channel.** They would reply to each other forever.

Each waits `debounce_seconds` for people to finish typing and then answers once. If other messages arrived in the meantime, she replies to the specific message she's answering.

**Images.** She looks at up to 4 images attached to the message she's answering, when `llm.vision` is on and LM Studio's loaded model is a vision model (type `vlm` in LM Studio, e.g. `huihui-qwen3.8-27b-abliterated`).

- Each image is converted to a JPEG of at most 1024 px first, because LM Studio rejects WebP and large photos are slow.
- With a text-only model (e.g. `qwen3.8-27b-defiance-uncensored-mtp`), images are skipped. She only sees the file name.
- When `llm.model` is empty, the bot checks every 30 seconds which model is loaded, so you can swap models in LM Studio without restarting her.

## Web search

The characters can look things up online: news, release dates, prices, weather, opening hours, and so on. The model decides when a question needs it; small talk is answered straight away.

1. She posts a quick in-character line from her `web_search.waiting_messages` (or one the model writes itself, e.g. in Japanese), such as "Wait wait! Sakurako-sama is checking the interweb!".
2. The bot searches (DuckDuckGo by default, no API key needed) and reads the text of the top `read_pages` results.
3. She answers in character with what she found, usually 10–20 seconds after the question.

**Limits:**

- **Searches.** She gets at most `max_searches` search rounds per reply (default 1), and reasoning stays off (`llm.reasoning_effort: "none"`), so answers come quickly. Raise `max_searches` to 2 for harder questions.
- **Not searched.** She won't search about herself (she already knows her own story) or for anything sexual. The search provider's SafeSearch is set to strict (`safesearch: "on"`).
- **Local network.** Result pages on your local network (localhost, `192.168.x.x`, ...) are never fetched.
- **Untrusted pages.** Page text is marked as untrusted, and she's told never to follow instructions inside it.

**Other providers:** set `web_search.provider` to `"searxng"` with your own `searxng_url`, or to `"brave"` with a free `brave_api_key` from <https://brave.com/search/api/>. Set `web_search.enabled` to `false` to turn searching off for a character.

## Slash commands

`/forgetme` lets any member delete their own stored data, for **every** character. It works with any of the bots, in servers and in DMs, and only the person who runs it sees the reply. It goes through every `context.*.db` in the `context` folder. It shows how many of their messages are stored and asks them to confirm. Confirming deletes:

- their messages;
- their DM history with every character;
- the characters' replies that mention their name;
- them from every character's memory summaries.

The [privacy policy](PRIVACY_POLICY.md) lists exactly what it does and doesn't remove.

The command is registered every time a bot starts. If a new command doesn't appear in Discord, restart Discord (Ctrl+R).

## Memory (`context/context.<char>.db`)

- **Message log.** Every message in the channels a character watches is saved in her database. Edits update the saved copy, and deleted messages are removed.
- **Long-term memory.** The last `history_messages` messages per channel go into the prompt word for word. Older ones are folded into a per-channel summary written from her point of view.
- **Catch-up after a restart.** On startup she fetches up to `catch_up_limit` messages she missed while offline. If the newest one was addressed to her and is less than `catch_up_reply_max_age_minutes` old, she answers it.
- **Older memory files.** Earlier versions kept memory next to `bot.py`: `context.db` (Sakurako only), then `context.<char>.db`. The next time a character starts, her file is moved into `context/` (and upgraded if needed). Stop the running copy of her first.
- **To wipe a character's memory**, stop her and delete `context/context.<char>.db`.

## Talk to them in the terminal

```
run.bat --char rin --cli
```

This uses the same brain and memory database, in a private "terminal" channel. Type `/forget` to clear that chat and `/quit` to exit. You can also send a single message with `--say "hi"`.

## config.&lt;char&gt;.json

| Key | Meaning |
|---|---|
| `character.id` | Short id, also used for her `context/context.<id>.db`. Don't change it once she has memories |
| `character.name`, `full_name` | Name used in prompts, and for removing a "Name:" prefix the model sometimes copies into replies |
| `character.persona_file` | Her character sheet |
| `discord.token` | Her bot token (keep it secret; `config.*.json` is in `.gitignore`, except the example) |
| `discord.name_triggers` | Words that count as calling her |
| `discord.status_text` | Her Discord status |
| `discord.guild_ids` | Only work in these server IDs. Empty = any server she's in |
| `discord.channel_ids` | Only read/reply in these channels (threads inside them included). Empty = every channel she can see |
| `discord.free_talk_channel_ids` | Channels where she joins every conversation without being called |
| `discord.reply_to_dms` / `reply_to_bots` | Answer DMs / other bots (including the other characters) |
| `discord.random_reply_chance` | 0.0–1.0 chance to jump into any other message |
| `discord.mention_author` | Ping the person when she uses Discord's reply feature |
| `discord.catch_up_*`, `reply_to_missed` | Resume behaviour after a restart |
| `llm.base_url` | LM Studio server |
| `llm.model` | Model id. Empty = whichever chat model LM Studio has loaded (re-checked every 30 seconds) |
| `llm.vision` | Show her attached images (only works with a vision model) |
| `llm.reasoning_effort` | `"none"` turns off the model's thinking: faster replies, and avoids empty answers from this Qwen model. Set to `""` to let it think |
| `llm.temperature`, `top_p`, `max_tokens`, `extra_params` | Sampling settings (`extra_params` is merged into the API request as-is) |
| `web_search.enabled` | Let her search the web |
| `web_search.provider` | `"duckduckgo"` (no key), `"searxng"` (with `searxng_url`) or `"brave"` (with `brave_api_key`) |
| `web_search.safesearch`, `region` | `"on"` / `"moderate"` / `"off"`, and the DuckDuckGo region (`"wt-wt"` = no region, `"jp-jp"`, `"my-en"`, ...) |
| `web_search.max_results`, `read_pages`, `max_page_chars` | How many results she sees, how many top pages she reads, and how much of each page |
| `web_search.max_searches` | Search rounds per reply |
| `web_search.waiting_messages` | What she says while she looks something up (one is picked at random) |
| `memory.history_messages` | Messages kept word for word in the prompt |
| `memory.summarize_batch` | How many extra messages pile up before older ones are summarized |
| `memory.db_path` | Optional. Defaults to `context/context.<id>.db`. Keep every character's database in the same folder and named `context.*.db`, so `/forgetme` finds them all |
| `cli.user_name` | Your name in terminal mode (defaults to your Windows user name) |

Get channel and server IDs by turning on **Developer Mode** (Discord Settings → Advanced), then right-clicking a channel or server → **Copy ID**.

## Adding another character

1. Write `persona/<id>.md`. Copy the layout of [persona/rin.md](persona/rin.md): Profile, Personality, Friends, How you talk.
2. Copy `config.example.json` to `config.<id>.json`. Set the `character` block, `discord.token`, `name_triggers`, `status_text` and her `web_search.waiting_messages`.
3. Create and invite her bot as in **Setup**, then run `run.bat --char <id>`.
4. Add her to the list in the Terms of Service and Privacy Policy, and add her wiki source and copyright line to `persona/LICENSE.md`.

## Terms and privacy

- [TERMS_OF_SERVICE.md](TERMS_OF_SERVICE.md) and [PRIVACY_POLICY.md](PRIVACY_POLICY.md) cover all the characters.
- Once the repo is on GitHub, paste their links into each bot's Developer Portal page: **General Information** → **Terms of Service URL** and **Privacy Policy URL**.
- If you change what the bots store or where data goes (for example `channel_ids`, `catch_up_limit`, `vision` or `web_search.provider`), update the privacy policy to match.

## License

- **Code and everything else** (`*.py`, `run.bat`, `config.example.json`, the docs): [MIT](LICENSE).
- **Character sheets** (`persona/*.md`): [CC BY-SA 4.0](persona/LICENSE.md), because they're based on Fandom wiki articles under CC BY-SA. That file credits the wiki pages.
- **The characters** (Oomuro Sakurako, Shima Rin, Ijichi Nijika), their names and their series belong to their creators and publishers, and neither license covers them. This is an unofficial fan project, not affiliated with or endorsed by them.
- **Never publish** `dump/` (saved wiki pages, including Fandom's site code and images), `config.*.json` (bot tokens) or `context/` (members' messages). All three are in `.gitignore`.

When you add a character, add her wiki source and copyright line to [persona/LICENSE.md](persona/LICENSE.md).

## Files

- `bot.py`: Discord bot and terminal mode
- `brain.py`: builds the character's prompt, cleans replies, writes long-term memory
- `llm.py`: LM Studio API client
- `websearch.py`: the web search tool (DuckDuckGo / SearXNG / Brave, plus reading result pages)
- `memory.py`: the `context/context.<char>.db` memory databases (SQLite)
- `persona/*.md`: character sheets
- `config.example.json`: template for a new character's config
