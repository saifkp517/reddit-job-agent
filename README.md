# Reddit job agent

Watches hiring subreddits, keeps posts that match your keywords **and** contain an email, drafts an
application, and posts it to Discord with **Send / Edit / Skip** buttons. You can also chat with the bot
in that channel (Claude Haiku) to change settings or rewrite how emails are written.

- **Filtering:** keywords + regex only. Costs nothing.
- **Email drafts:** `mail_mode=template` fills `mail_template.md` (free, for testing the flow).
  `mail_mode=ai` has Claude write each email from `mail_prompt.md` + `profile.md`.
- **Limits:** 10 emails per 24h by default. One email per address, ever.

## Files you edit

| File | What it is |
|---|---|
| `.env` | keys and first-run defaults |
| `profile.md` | what you can do; used by the AI mail mode |
| `mail_template.md` | the fixed email for template mode (`{role}`, `{subreddit}` get filled in) |
| `mail_prompt.md` | instructions for the AI mail writer |
| `SONNET_SUBREDDIT_PROMPT.md` | paste into Sonnet to research subreddits and keywords |

## Run it on your PC (PowerShell)

```powershell
cd D:\reddit-job-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then fill it in
python bot.py
```

Discord setup: Developer Portal → your app → **Bot** → copy the token and turn on **Message Content Intent**.
OAuth2 → URL generator → scope `bot`, permissions *Send Messages, Embed Links, Read Message History*. Invite it.
Turn on Developer Mode in Discord to copy the channel id and your user id.

## Talk to it in Discord

- "status"
- "pause" / "resume"
- "switch to ai mode" / "back to template mode"
- "set the daily limit to 5"
- "add r/startups to the subreddits"
- "make the emails shorter and mention I can start this week"  → rewrites `mail_prompt.md` (old version backed up)
- "preview https://www.reddit.com/r/forhire/comments/..."  → drafts an email for that post with buttons

Settings changed from Discord are stored in `state.db` and override `.env`.

## Run it 24/7 on the VPS

```bash
sudo useradd -r -m -d /opt/job-agent jobagent
sudo cp -r . /opt/job-agent && sudo chown -R jobagent /opt/job-agent
cd /opt/job-agent && sudo -u jobagent python3 -m venv .venv
sudo -u jobagent .venv/bin/pip install -r requirements.txt
sudo cp job-agent.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now job-agent
journalctl -u job-agent -f
```
