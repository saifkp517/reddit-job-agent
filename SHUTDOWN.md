# How to turn off and delete the job agent

Welcome back from the heat death. Here's how to kill it.

**Where it lives:** DigitalOcean droplet `139.59.70.83`, folder `/opt/job-agent`, systemd service `job-agent`, Linux user `jobagent`.

## 1. Log in
```bash
ssh root@139.59.70.83
```

## 2. Just turn it off (keeps everything, can restart later)
```bash
systemctl disable --now job-agent
```
Turn it back on: `systemctl enable --now job-agent`

## 3. Delete it completely
```bash
systemctl disable --now job-agent
rm /etc/systemd/system/job-agent.service
systemctl daemon-reload
userdel -r jobagent        # deletes /opt/job-agent too (code, .env with keys, state.db)
```

Check it's gone: `systemctl status job-agent` should say "could not be found".

## 4. Revoke the keys (do this even if the droplet is already gone)
| Key | Where to revoke |
|---|---|
| Anthropic API key | console.anthropic.com → API Keys → delete `job-agent` |
| Gmail App Password | myaccount.google.com/apppasswords → delete `job-agent` |
| Discord bot | discord.com/developers/applications → your app → Delete App (or just kick it from the server) |
| Reddit app | reddit.com/prefs/apps → delete the app |

## 5. Local copies (optional)
- Windows: `D:\reddit-job-agent`
- WSL: `~/reddit-job-agent`

Delete them if you don't need them. They also contain `.env` with your keys.

## Droplet gone entirely?
If you destroyed the DigitalOcean droplet, steps 1–3 are done for you. Still do step 4.
