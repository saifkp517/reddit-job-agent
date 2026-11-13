"""Reddit hiring-post watcher with a Discord control room.

- Filters Reddit posts with keywords + regex only (no LLM, no cost).
- Drafts the email from a template (mail_mode=template, free) or with Claude
  using the editable prompt in mail_prompt.md (mail_mode=ai).
- Dry run: every draft goes to Discord with Send / Edit / AI draft / Skip buttons.
- Control it with free !commands in Discord. Claude is only used to write a draft
  when you ask (!draft <link> or the AI draft button). There is no AI chat.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import smtplib
import sqlite3
import time
from email.message import EmailMessage
from pathlib import Path

import asyncpraw
import discord
from dotenv import load_dotenv
from pydantic import BaseModel

from email_extract import find_email

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name) or default
    if value is None:
        raise SystemExit(f"Missing required env var: {name}")
    return value


DISCORD_TOKEN = env("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(env("DISCORD_CHANNEL_ID"))
DISCORD_OWNER_ID = int(env("DISCORD_OWNER_ID"))

SMTP_HOST = env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
FROM_NAME = env("FROM_NAME", "")
RESUME_PATH = env("RESUME_PATH", "")  # optional PDF attachment; the template already links the resume

MAIL_MODEL = env("MAIL_MODEL", "claude-haiku-5-5")  # AI drafts: !draft, the AI draft button, mail_mode=ai

PROFILE_FILE = HERE / "profile.md"
PROMPT_FILE = HERE / "mail_prompt.md"
TEMPLATE_FILE = HERE / "mail_template.md"
DB_PATH = HERE / "state.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("job-agent")

# ------------------------------------------------------------------- db -----
db = sqlite3.connect(DB_PATH)
db.execute(
    """CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY, url TEXT, title TEXT,
        status TEXT,  -- skipped | pending | sent | rejected | failed
        email TEXT, subject TEXT, body TEXT, created REAL)"""
)
db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
# One row per Reddit check, so the assistant can tell whether the bot is working.
db.execute(
    """CREATE TABLE IF NOT EXISTS polls (
        ts REAL, fetched INTEGER, new INTEGER, drafted INTEGER, error TEXT)"""
)
if "reason" not in [c[1] for c in db.execute("PRAGMA table_info(posts)")]:
    db.execute("ALTER TABLE posts ADD COLUMN reason TEXT")  # why a post was skipped
db.commit()

# Settings live in the db so the Discord assistant can change them at runtime.
# .env values are only the first-run defaults.
DEFAULTS = {
    "dry_run": env("DRY_RUN", "true"),
    "mail_mode": env("MAIL_MODE", "template"),  # template | ai
    "paused": "false",
    "daily_send_limit": env("DAILY_SEND_LIMIT", "10"),
    "poll_seconds": env("POLL_SECONDS", "300"),
    "subreddits": env("SUBREDDITS", "forhire+hiring+remotejs+jobbit"),
    "include_keywords": env("INCLUDE_KEYWORDS", "[hiring],hiring,we're hiring,looking for a"),
    "exclude_keywords": env("EXCLUDE_KEYWORDS", "[for hire],for hire,unpaid,equity only,rev share,telegram"),
}


def setting(key: str) -> str:
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else DEFAULTS[key]


def set_setting(key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise ValueError(f"Unknown setting {key}")
    db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    db.commit()


def as_bool(v: str) -> bool:
    return str(v).lower() in {"true", "1", "yes", "on"}


def keywords(key: str) -> list[str]:
    return [k.strip().lower() for k in setting(key).split(",") if k.strip()]


def seen(post_id: str) -> bool:
    return db.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone() is not None


def already_emailed(email: str) -> bool:
    return db.execute(
        "SELECT 1 FROM posts WHERE lower(email)=lower(?) AND status IN ('sent','pending')", (email,)
    ).fetchone() is not None


def sent_last_24h() -> int:
    return db.execute(
        "SELECT count(*) FROM posts WHERE status='sent' AND created > ?", (time.time() - 86400,)
    ).fetchone()[0]


def save(post_id: str, **fields) -> None:
    if seen(post_id):
        sets = ", ".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE posts SET {sets} WHERE id=?", (*fields.values(), post_id))
    else:
        fields.setdefault("created", time.time())
        cols = ", ".join(["id", *fields])
        db.execute(f"INSERT INTO posts ({cols}) VALUES ({', '.join('?' * (len(fields) + 1))})",
                   (post_id, *fields.values()))
    db.commit()


def load(post_id: str) -> dict | None:
    cur = db.execute("SELECT id,url,title,status,email,subject,body FROM posts WHERE id=?", (post_id,))
    row = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], row)) if row else None


# ------------------------------------------------------------ filtering -----
def has_phrase(haystack: str, phrase: str) -> bool:
    # whole-word match, so "us only" doesn't hit "focus only"
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", haystack) is not None


def skip_reason(title: str, text: str) -> str | None:
    """Why a post is not a hiring post we want, or None if it passes the keyword filters."""
    haystack = f"{title}\n{text}".lower().replace("’", "'")
    for k in keywords("exclude_keywords"):
        if has_phrase(haystack, k):
            return f"exclude keyword: {k}"
    if not any(has_phrase(haystack, k) for k in keywords("include_keywords")):
        return "no include keyword"
    return None


def guess_role(title: str) -> str:
    role = re.sub(r"[\[\(][^\]\)]*[\]\)]", "", title)  # drop [Hiring], (Remote) ...
    role = re.sub(r"^\W*(hiring|we'?re hiring|looking for)\W*", "", role, flags=re.I).strip(" -:|")
    return (role or "open")[:80]


# ---------------------------------------------------------------- mails -----
def template_draft(title: str, subreddit: str) -> tuple[str, str]:
    raw = TEMPLATE_FILE.read_text(encoding="utf-8")
    filled = raw.replace("{role}", guess_role(title)).replace("{subreddit}", subreddit)
    first, _, rest = filled.partition("\n")
    return first.removeprefix("Subject:").strip(), rest.strip()


class Draft(BaseModel):
    subject: str
    body: str


_claude = None


def claude_client():
    """Import the Anthropic SDK only when Claude is actually used (an AI draft)."""
    global _claude
    if _claude is None:
        import anthropic
        _claude = anthropic.AsyncAnthropic()
    return _claude


async def ai_draft(title: str, text: str, subreddit: str) -> tuple[str, str]:
    system = (PROMPT_FILE.read_text(encoding="utf-8")
              + "\n\n<profile>\n" + PROFILE_FILE.read_text(encoding="utf-8") + "\n</profile>")
    response = await claude_client().messages.parse(
        model=MAIL_MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=system,
        messages=[{"role": "user", "content":
                   f"<post subreddit=\"r/{subreddit}\">\n<title>{title}</title>\n<body>\n{text[:8000]}\n</body>\n</post>"}],
        output_format=Draft,
    )
    if response.stop_reason == "refusal" or not response.parsed_output:
        raise RuntimeError("Model declined to draft this email")
    return response.parsed_output.subject, response.parsed_output.body


async def make_draft(title: str, text: str, subreddit: str) -> tuple[str, str]:
    if setting("mail_mode") == "ai":
        return await ai_draft(title, text, subreddit)
    return template_draft(title, subreddit)


def _send_email_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{SMTP_USER}>" if FROM_NAME else SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if RESUME_PATH:
        path = Path(RESUME_PATH)
        msg.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


async def send_email(post_id: str) -> str:
    row = load(post_id)
    if not row:
        return "Unknown post."
    if row["status"] == "sent":
        return "Already sent."
    limit = int(setting("daily_send_limit"))
    if sent_last_24h() >= limit:
        return f"Daily limit ({limit}) reached; try again later."
    try:
        await asyncio.to_thread(_send_email_sync, row["email"], row["subject"], row["body"])
    except Exception as e:
        log.exception("Email to %s failed", row["email"])
        save(post_id, status="failed")
        return f"Failed: {e}"
    save(post_id, status="sent", created=time.time())
    return f"Sent to {row['email']}."


# ------------------------------------------------------- discord buttons ----
intents = discord.Intents.default()
intents.message_content = True  # also enable "Message Content Intent" in the Developer Portal
bot = discord.Client(intents=intents)
reddit: asyncpraw.Reddit | None = None


def draft_embed(row: dict, color: discord.Color = discord.Color.blurple()) -> discord.Embed:
    e = discord.Embed(title=(row["title"] or "")[:250], url=row["url"], color=color)
    e.add_field(name="To", value=row["email"] or "-", inline=True)
    e.add_field(name="Subject", value=(row["subject"] or "-")[:1000], inline=True)
    e.add_field(name="Body", value=(row["body"] or "-")[:1024], inline=False)
    return e


def draft_buttons(post_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    row = load(post_id) or {}
    if row.get("email"):  # no address = draft is for a DM/form you send yourself
        view.add_item(discord.ui.Button(label="Send", style=discord.ButtonStyle.success, custom_id=f"send:{post_id}"))
    view.add_item(discord.ui.Button(label="Edit", style=discord.ButtonStyle.secondary, custom_id=f"edit:{post_id}"))
    view.add_item(discord.ui.Button(label="AI draft", style=discord.ButtonStyle.primary, custom_id=f"ai:{post_id}"))
    view.add_item(discord.ui.Button(label="Skip", style=discord.ButtonStyle.danger, custom_id=f"skip:{post_id}"))
    return view


async def ai_redraft(post_id: str) -> str:
    """Rewrite one post's draft with Claude. Only runs when you ask (button or !draft)."""
    post = await reddit.submission(id=post_id)
    await post.load()
    subject, body = await ai_draft(post.title, post.selftext or "", post.subreddit.display_name)
    save(post_id, subject=subject, body=body)
    return "AI draft ready."


class EditModal(discord.ui.Modal, title="Edit email"):
    def __init__(self, row: dict):
        super().__init__()
        self.post_id = row["id"]
        self.subject = discord.ui.TextInput(label="Subject", default=(row["subject"] or "")[:200], max_length=200)
        self.body = discord.ui.TextInput(label="Body", default=(row["body"] or "")[:4000],
                                         style=discord.TextStyle.paragraph, max_length=4000)
        self.add_item(self.subject)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        save(self.post_id, subject=self.subject.value, body=self.body.value)
        await interaction.response.edit_message(embed=draft_embed(load(self.post_id)),
                                                view=draft_buttons(self.post_id))


@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    action, _, post_id = (interaction.data or {}).get("custom_id", "").partition(":")
    if action not in {"send", "edit", "skip", "ai"}:
        return
    if interaction.user.id != DISCORD_OWNER_ID:
        await interaction.response.send_message("Not yours.", ephemeral=True)
        return
    row = load(post_id)
    if not row:
        await interaction.response.send_message("Unknown post.", ephemeral=True)
    elif action == "edit":
        await interaction.response.send_modal(EditModal(row))
    elif action == "skip":
        save(post_id, status="rejected")
        await interaction.response.edit_message(content="Skipped.",
                                                embed=draft_embed(row, discord.Color.dark_grey()), view=None)
    elif action == "ai":
        await interaction.response.defer()
        try:
            result = await ai_redraft(post_id)
        except Exception as e:
            result = f"AI draft failed: {e}"
        await interaction.message.edit(content=result, embed=draft_embed(load(post_id)),
                                       view=draft_buttons(post_id))
    else:
        await interaction.response.defer()
        result = await send_email(post_id)
        ok = result.startswith("Sent")
        await interaction.message.edit(
            content=result,
            embed=draft_embed(load(post_id), discord.Color.green() if ok else discord.Color.red()),
            view=None if ok else draft_buttons(post_id))


async def channel():
    return bot.get_channel(DISCORD_CHANNEL_ID) or await bot.fetch_channel(DISCORD_CHANNEL_ID)


# ------------------------------------------------------- reddit polling -----
async def process(post, *, force: bool = False) -> str:
    """Filter a post, draft an email, and post it to Discord. Returns what happened."""
    title, text = post.title, post.selftext or ""
    url = f"https://www.reddit.com{post.permalink}"
    sub = post.subreddit.display_name

    reason = None if force else skip_reason(title, text)
    if reason:
        save(post.id, url=url, title=title, status="skipped", reason=reason)
        return reason
    email = find_email(f"{title}\n{text}")
    if not email:
        save(post.id, url=url, title=title, status="skipped", reason="hiring post, but no email")
        return "hiring post, but no email"
    if not force and already_emailed(email):
        save(post.id, url=url, title=title, status="skipped", email=email, reason="already contacted")
        return f"already contacted {email}"

    subject, body = await make_draft(title, text, sub)
    save(post.id, url=url, title=title, status="pending", email=email, subject=subject, body=body)
    row = load(post.id)
    ch = await channel()
    if as_bool(setting("dry_run")) or force:
        await ch.send(f"New match ({setting('mail_mode')} draft) — send it?", embed=draft_embed(row),
                      view=draft_buttons(post.id))
        return f"draft posted for {email}"
    result = await send_email(post.id)
    await ch.send(f"Auto: {result}", embed=draft_embed(load(post.id)))
    return result


async def poll_reddit() -> None:
    while True:
        if not as_bool(setting("paused")):
            fetched = new = drafted = 0
            errors = []
            posts = []
            # One request per subreddit (50 newest each): small responses, and one slow
            # or missing subreddit doesn't fail the whole check.
            for name in [s.strip() for s in setting("subreddits").split("+") if s.strip()]:
                try:
                    sub = await reddit.subreddit(name)
                    posts += [p async for p in sub.new(limit=50)]
                except Exception as e:
                    log.warning("r/%s failed: %s", name, e)
                    errors.append(f"r/{name}: {type(e).__name__}")
            fetched = len(posts)
            error = "; ".join(errors)[:300] or None
            posts.sort(key=lambda p: p.created_utc)
            try:
                for post in posts:
                    if seen(post.id):
                        continue
                    new += 1
                    try:
                        result = await process(post)
                        drafted += result.startswith(("draft posted", "Sent"))
                    except Exception as e:
                        if type(e).__name__ == "RateLimitError":
                            break  # Claude rate limit: retry the rest next poll
                        log.exception("Failed handling %s", post.id)
                        save(post.id, url=post.permalink, title=post.title, status="failed",
                             reason=f"error: {e}"[:300])
            except Exception as e:
                log.exception("Processing posts failed")
                error = f"{error or ''} {type(e).__name__}: {e}".strip()[:300]
            db.execute("INSERT INTO polls VALUES (?,?,?,?,?)", (time.time(), fetched, new, drafted, error))
            db.commit()
            log.info("Poll: fetched=%s new=%s drafted=%s error=%s", fetched, new, drafted, error)
        await asyncio.sleep(int(setting("poll_seconds")))


# ------------------------------------------------- !commands (no AI) -----
COMMANDS_HELP = """**Commands** (free, no AI)
`!status` — settings, post counts, last Reddit checks
`!skipped [n] [word]` — every skipped post with the reason (or just the last n). Add a word to filter by reason or title, e.g. `!skipped email`, `!skipped 30 exclude`. Long lists come as a .txt file
`!reasons` — how many posts were skipped for each reason
`!jobs [n]` — last n hiring posts that had no email (apply to them yourself)
`!pending` — drafts waiting for Send/Skip
`!sent` — emails already sent
`!sub` — list subreddits · `!sub add <name>` · `!sub remove <name>`
`!settings` — all settings
`!set <name> <value>` — change one, e.g. `!set dry_run false`, `!set subreddits forhire+remotejobs`
`!help` — this list

**AI (costs tokens, only when you ask)**
`!draft <reddit link>` — Claude writes an email for that post
**AI draft** button on any draft — rewrite it with Claude"""


def ago(ts: float | None) -> str:
    if not ts:
        return "?"
    mins = int((time.time() - ts) / 60)
    return f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago" if mins < 1440 else f"{mins // 1440}d ago"


def post_lines(rows) -> str:
    if not rows:
        return "Nothing found."
    lines = []
    for title, url, reason, email, created in rows:
        extra = reason or email or "reason not recorded"
        safe = title[:80].replace("[", "(").replace("]", ")")  # brackets break Discord link text
        lines.append(f"• [{safe}](<{url}>) — {extra} · {ago(created)}")
    return "\n".join(lines)


def run_command(text: str) -> str:
    parts = text.strip().split()
    cmd, args = parts[0].lower(), parts[1:]
    n = next((int(a) for a in args if a.isdigit()), 20)
    n = max(1, n)
    word = " ".join(a for a in args if not a.isdigit())

    if cmd == "!status":
        counts = dict(db.execute("SELECT status, count(*) FROM posts GROUP BY status").fetchall())
        polls = db.execute("SELECT ts, fetched, new, drafted, error FROM polls ORDER BY ts DESC LIMIT 5").fetchall()
        lines = [
            f"**Status** · paused={setting('paused')} · dry_run={setting('dry_run')} · "
            f"mail_mode={setting('mail_mode')} · sent today {sent_last_24h()}/{setting('daily_send_limit')}",
            "**Posts:** " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none yet"),
            f"**Checking every {int(setting('poll_seconds')) // 60} min.** Last checks:",
        ]
        lines += [f"• {ago(ts)}: fetched {f}, new {nw}, drafted {d}" + (f", ERROR {e}" if e else "")
                  for ts, f, nw, d, e in polls] or ["• no checks recorded yet"]
        return "\n".join(lines)

    if cmd == "!skipped":
        sql = "SELECT title, url, reason, NULL, created FROM posts WHERE status='skipped'"
        params: list = []
        if word:
            sql += " AND (reason LIKE ? OR title LIKE ?)"
            params += [f"%{word}%", f"%{word}%"]
        limit = n if any(a.isdigit() for a in args) else -1  # no number given = all of them
        rows = db.execute(sql + " ORDER BY created DESC LIMIT ?", (*params, limit)).fetchall()
        total = db.execute("SELECT count(*) FROM posts WHERE status='skipped'").fetchone()[0]
        return f"**{len(rows)} skipped** (of {total} total)\n" + post_lines(rows)

    if cmd == "!reasons":
        rows = db.execute("SELECT coalesce(reason, '(not recorded)'), count(*) FROM posts WHERE status='skipped' "
                          "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        return "**Skip reasons**\n" + ("\n".join(f"• {r}: {c}" for r, c in rows) or "Nothing skipped yet.")

    if cmd == "!jobs":
        rows = db.execute("SELECT title, url, NULL, NULL, created FROM posts WHERE reason='hiring post, but no email' "
                          "ORDER BY created DESC LIMIT ?", (n,)).fetchall()
        return f"**Hiring posts without an email** (apply via DM or link)\n" + post_lines(rows)

    if cmd in ("!pending", "!sent"):
        status = cmd[1:]
        rows = db.execute("SELECT title, url, NULL, email, created FROM posts WHERE status=? "
                          "ORDER BY created DESC LIMIT ?", (status, n)).fetchall()
        return f"**{status.capitalize()}**\n" + post_lines(rows)

    if cmd == "!sub":
        subs = [s for s in setting("subreddits").split("+") if s]
        if len(args) == 2 and args[0] in ("add", "remove"):
            name = args[1].removeprefix("r/")
            if args[0] == "add" and name.lower() not in [s.lower() for s in subs]:
                subs.append(name)
            elif args[0] == "remove":
                subs = [s for s in subs if s.lower() != name.lower()]
            set_setting("subreddits", "+".join(subs))
        return f"**Watching {len(subs)} subreddits:** " + ", ".join(f"r/{s}" for s in subs) + \
            "\nAdd/remove: `!sub add remotejobs`, `!sub remove startups`"

    if cmd == "!settings":
        return "**Settings**\n" + "\n".join(f"• `{k}` = {setting(k)}" for k in DEFAULTS)

    if cmd == "!set":
        if len(args) < 2 or args[0] not in DEFAULTS:
            return "Usage: `!set <name> <value>`. Names: " + ", ".join(f"`{k}`" for k in DEFAULTS)
        set_setting(args[0], text.strip().split(None, 2)[2])
        return f"`{args[0]}` = {setting(args[0])}"

    return COMMANDS_HELP


async def draft_command(text: str) -> tuple[str, str | None]:
    """!draft <url>: AI-draft an email for one post. Returns (message, post_id or None)."""
    parts = text.split()
    if len(parts) < 2:
        return "Usage: `!draft <reddit post link>`", None
    post = await reddit.submission(url=parts[1].strip("<>"))
    await post.load()
    email = find_email(f"{post.title}\n{post.selftext or ''}")
    subject, body = await ai_draft(post.title, post.selftext or "", post.subreddit.display_name)
    save(post.id, url=f"https://www.reddit.com{post.permalink}", title=post.title, status="pending",
         email=email, subject=subject, body=body)
    note = "" if email else " No email in the post, so send it yourself (DM or form)."
    return f"AI draft ready.{note}", post.id


def split_message(text: str, limit: int = 1900) -> list[str]:
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    return [c for c in [*chunks, current] if c.strip()]


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.channel.id != DISCORD_CHANNEL_ID or message.author.id != DISCORD_OWNER_ID:
        return
    if message.content.lower().startswith("!draft"):
        async with message.channel.typing():
            try:
                msg, post_id = await draft_command(message.content)
            except Exception as e:
                msg, post_id = f"Draft failed: {e}", None
        if post_id:
            await message.reply(msg, embed=draft_embed(load(post_id)), view=draft_buttons(post_id),
                                mention_author=False)
        else:
            await message.reply(msg, mention_author=False)
        return
    if message.content.startswith("!"):  # free commands, no AI
        out = run_command(message.content)
        chunks = split_message(out)
        if len(chunks) > 5:  # long lists go out as one text file instead of a wall of messages
            plain = re.sub(r"\[(.*?)\]\(<(.*?)>\)", r"\1  \2", out).replace("**", "")
            await message.reply(chunks[0].split("\n", 1)[0] + " — full list attached", mention_author=False,
                                file=discord.File(io.BytesIO(plain.encode()), filename="posts.txt"))
        else:
            for chunk in chunks:
                await message.reply(chunk, mention_author=False, suppress_embeds=True)
        return
    await message.reply("Chat is off to save tokens. Type `!help` for commands.", mention_author=False)


# ---------------------------------------------------------------- start -----
@bot.event
async def on_ready():
    global reddit
    log.info("Discord logged in as %s", bot.user)
    if getattr(bot, "_poller", None):
        return
    reddit = asyncpraw.Reddit(
        client_id=env("REDDIT_CLIENT_ID"),
        client_secret=env("REDDIT_CLIENT_SECRET"),
        user_agent=env("REDDIT_USER_AGENT", "job-watcher/1.0"),
        timeout=60,  # default is 16s, which slow responses can exceed
    )
    await (await channel()).send(
        f"👋 Hey{' ' + FROM_NAME.split()[0] if FROM_NAME else ''}, I'm back online and hunting jobs across "
        f"{len(setting('subreddits').split('+'))} subreddits. Type `!help` for commands."
    )
    bot._poller = asyncio.create_task(poll_reddit())


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, log_handler=None)
