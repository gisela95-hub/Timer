# Twitch Subathon Timer

A Flask + Twitch EventSub subathon timer designed for OBS Browser Source.

## Default rules

- Tier 1 sub: +5 minutes
- Tier 2 sub: +10 minutes
- Tier 3 sub: +25 minutes
- Gifted sub: +8 minutes (gifted value takes priority over tier)
- Raid: +1 minute per viewer
- Bits: +3 minutes per 100 bits (partial blocks of 100 do not add time)
- Maximum timer: 24 hours

## 1. Create the Twitch application

Go to the Twitch Developer Console:
https://dev.twitch.tv/console/apps

Create an application.

Use:
- Name: anything you want
- OAuth Redirect URL: `https://YOUR-RENDER-SERVICE.onrender.com/oauth/callback`
- Category: Website Integration

Copy the Client ID and generate a Client Secret.

## 2. Create the Render service

Push this folder to a GitHub repository.

In Render:
- New > Web Service
- Select the GitHub repository
- Runtime: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`

Render requires a web service to listen on the PORT supplied by the environment; this app does that.

## 3. Add environment variables

Required:

TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
TWITCH_BROADCASTER_LOGIN=your_twitch_login
PUBLIC_BASE_URL=https://YOUR-RENDER-SERVICE.onrender.com
ADMIN_TOKEN=make-a-long-random-password
EVENTSUB_SECRET=another-long-random-secret

Optional rules:

OWN_SUB_SECONDS=300
GIFTED_SUB_SECONDS=480
TIER2_SUB_SECONDS=600
TIER3_SUB_SECONDS=1500
SECONDS_PER_100_BITS=180
MAX_TIMER_SECONDS=86400

## 4. Authorize Twitch

After the Render deployment is live, open:

`https://YOUR-RENDER-SERVICE.onrender.com/oauth/login`

Authorize your broadcaster account.

Then open:

`https://YOUR-RENDER-SERVICE.onrender.com/setup?token=YOUR_ADMIN_TOKEN`

You should see Twitch EventSub subscription responses with HTTP 202.

## 5. Add to OBS

In OBS:
1. Sources > Browser
2. URL:
   `https://YOUR-RENDER-SERVICE.onrender.com/`
3. Set Width/Height to your desired overlay size.
4. Leave custom CSS empty.
5. Enable "Refresh browser when scene becomes active" only if you want that behavior.

## 6. Start the timer

Open:

`https://YOUR-RENDER-SERVICE.onrender.com/admin?token=YOUR_ADMIN_TOKEN`

Enter the initial number of minutes and click Start.

The same page can manually add time, pause, or reset.

## Important Render note

Render's free web services can spin down after 15 minutes of inactivity. The OBS Browser Source requests `/api/state` every second while it is open, which counts as incoming activity. For the actual subathon, keep the Browser Source loaded.

The timer state is held in memory. A service restart/redeploy will reset the timer. For a production subathon that must survive restarts, add persistent storage (for example a database) before the event.

## Security

- Never publish `TWITCH_CLIENT_SECRET`.
- Never publish `ADMIN_TOKEN`.
- Never publish `EVENTSUB_SECRET`.
- Do not put secrets into the GitHub repository.


## Current subathon values

The code is already configured for:
- Tier 1 = 300 seconds (5 min)
- Tier 2 = 600 seconds (10 min)
- Tier 3 = 1500 seconds (25 min)
- Gifted = 480 seconds (8 min)
- 100 bits = 180 seconds (3 min)
- Raid = 60 seconds per viewer

For raids, Twitch EventSub sends the raid viewer count in the `channel.raid` event.
