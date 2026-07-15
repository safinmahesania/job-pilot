# Deploying JobPilot behind a Cloudflare Quick Tunnel

JobPilot runs on your own machine. This makes it reachable from your phone (or anywhere)
without buying a domain, opening a port, or putting it on a server — by running it on
`127.0.0.1` and letting a Cloudflare Quick Tunnel forward a temporary public URL to it.

It stays private because two things are true at once: the app binds only to localhost,
so nothing on your network can reach it directly; and every request is checked against a
password, so the public tunnel URL is useless without it.

## What you need

- The app running locally (see the main README for setup).
- `cloudflared` installed: <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/>
- A password set in `.env` (below). **Do not skip this** — the tunnel URL is public.

## 1. Set a password

Copy `.env.example` to `.env` if you haven't, and set:

```
JOBPILOT_PASSWORD=something-long-and-unguessable
```

The gate reads this on every request. With it unset the app runs open, which is fine on
localhost but **not** behind a tunnel. If it is blank, stop and set it before continuing.

## 2. Start the app on localhost

On Windows:

```powershell
.\run_server.ps1
```

`run_server.ps1` loads `.env` into the process and starts uvicorn bound to `127.0.0.1:8000`
— localhost only, on purpose, so the app never listens on a public interface. On macOS or
Linux the equivalent is:

```bash
set -a; . ./.env; set +a
uvicorn src.api:app --host 127.0.0.1 --port 8000
```

Confirm it works locally first: open <http://127.0.0.1:8000>, and you should be asked for
the password.

## 3. Open the tunnel

In a second terminal:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

It prints a random `https://<something>.trycloudflare.com` URL. That is your app, now
reachable from your phone. The URL changes every time you restart the tunnel — a Quick
Tunnel is deliberately ephemeral, which is a feature here: no fixed public address to be
found and hammered.

## How the two layers protect you

- **Bind address.** The app is on `127.0.0.1`, so nothing binds to `0.0.0.0` and no one
  else on your network can reach it. The tunnel connects from the *same machine*.
- **The gate.** Because the tunnel connects from localhost, every forwarded request looks
  local to the app — so the gate trusts no network fact. It requires the password
  (a `jp_auth` cookie from logging in, or the `x-jobpilot-key` header the extension sends)
  on every request, whatever the source address says.

## Notes and limits

- **It only runs while your laptop is open.** The fetch scheduler is an in-process loop,
  so jobs are fetched only while the app is running. Keeping the fetch going with the lid
  closed is a separate step (a scheduled task, or moving to an always-on host).
- **`cert.pem missing` from cloudflared** means it is trying to use a named tunnel that
  needs a Cloudflare login and a domain. A Quick Tunnel (`--url`, as above) needs neither
  — if you see that error, you are on the wrong command.
- **Never commit `.env`.** It is gitignored. The password and any API keys live only there.
