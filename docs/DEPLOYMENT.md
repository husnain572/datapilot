# Deploying DataPilot

These steps are for the Vultr server (Ubuntu 24.04), where the app runs from `/home/deploy/datapilot` as the `deploy` user.

## What runs where

| Service | Listens on | User | Job |
|---|---|---|---|
| `nginx` | Port 80, public | www-data | Front door, passes visitors to Streamlit |
| `datapilot-frontend` | 127.0.0.1:8501 | deploy | Screens |
| `datapilot-backend` | 127.0.0.1:8000 | deploy | Agent, validation, reports |
| `datapilot-executor` | 127.0.0.1:8100 | dp-executor | The only service that can start sandboxes |

## Updating the app

After changes are merged into `main`:

```bash
# VULTR SERVER, as root
sudo -u deploy git -C /home/deploy/datapilot pull --ff-only
bash /home/deploy/datapilot/scripts/deploy_server.sh
```

The script is safe to re-run. It backs up `.env` to `.env.backup`, adds missing settings without changing existing ones, rebuilds the sandbox image, restarts the services, and checks their health. It doesn't touch uploaded datasets.

## Settings

Settings are in `/home/deploy/datapilot/.env`, which only `deploy` can read. See `.env.example` for all of them.

```bash
# VULTR SERVER, as root: show setting names without their values
sed -E 's/=.*/=<hidden>/' /home/deploy/datapilot/.env

# After editing a setting
systemctl restart datapilot-backend datapilot-frontend
```

To change the executor token, change it in `.env`, then re-run the deployment script.

## AI keys

Keys go only in the backend's `.env`. The frontend reads `frontend.env`, which holds just `BACKEND_URL`; the deployment script creates it.

- **Groq (required):** create a key at https://console.groq.com/keys and put it on the `AI_API_KEY=` line.
- **Gemini (optional fallback):** create a key in Google AI Studio and put it on the `AI_FALLBACK_API_KEY=` line.

To replace a key, delete the old one at the provider, then edit the file:

```bash
# VULTR SERVER, as root
nano /home/deploy/datapilot/.env
systemctl restart datapilot-backend
```

## Checking on it

```bash
# VULTR SERVER, as root
systemctl status datapilot-executor datapilot-backend datapilot-frontend nginx
journalctl -u datapilot-backend -n 50          # recent backend logs
journalctl -u datapilot-executor -n 50         # recent sandbox logs
docker ps -a --filter label=datapilot.sandbox=1   # should be empty when idle
curl -s http://127.0.0.1:8000/ready
```

## Shutting down after the hackathon

The Vultr credit expires 30 days after signup, and a card is on file. After judging, and before the credit expires, **destroy the server in the Vultr console**. Stopping it may not stop billing. Destroying it is permanent and deletes everything on the server, so make sure the code is on GitHub first.
