# Reddit job agent

Watches hiring subreddits, keeps posts that match your keywords **and** contain an email, drafts an
application, and posts it to Discord with **Send / Edit / AI draft / Skip** buttons. You control it with
free `!commands`; there is no AI chat.

- **Filtering:** keywords + regex only. Costs nothing.
- **Email drafts:** filled from `mail_template.md` (free). Claude only writes a draft when you ask:
  the **AI draft** button, or `!draft <reddit link>` (uses `mail_prompt.md` + `profile.md`).
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

## Commands in Discord

Type `!help` in the channel for the full list. Main ones:

- `!status`, `!skipped`, `!reasons`, `!jobs`, `!pending`, `!sent`
- `!settings` and `!set <name> <value>`, e.g. `!set paused true`, `!set daily_send_limit 5`
- `!draft <reddit link>` — Claude writes an email for that post (costs a fraction of a cent)

Settings changed with `!set` are stored in `state.db` and override `.env`.
To change how AI drafts are written, edit `mail_prompt.md` on the VPS.

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
