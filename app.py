import os
import time
import hmac
import hashlib
import secrets
import threading
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, request, redirect, render_template, abort

app = Flask(__name__)

CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]
BROADCASTER_LOGIN = os.environ["TWITCH_BROADCASTER_LOGIN"].lower()
PUBLIC_BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")
EVENTSUB_SECRET = os.environ.get("EVENTSUB_SECRET") or secrets.token_urlsafe(32)
ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]

# Default rules — edit these environment variables in Render if desired.
OWN_SUB_SECONDS = int(os.environ.get("OWN_SUB_SECONDS", "300"))          # 5 min
GIFTED_SUB_SECONDS = int(os.environ.get("GIFTED_SUB_SECONDS", "480"))    # 8 min
TIER2_SUB_SECONDS = int(os.environ.get("TIER2_SUB_SECONDS", "600"))     # 10 min
TIER3_SUB_SECONDS = int(os.environ.get("TIER3_SUB_SECONDS", "1500"))    # 25 min
SECONDS_PER_100_BITS = int(os.environ.get("SECONDS_PER_100_BITS", "180")) # 3 min
MAX_TIMER_SECONDS = int(os.environ.get("MAX_TIMER_SECONDS", str(24 * 3600)))

state_lock = threading.Lock()
state = {
    "end_at": 0.0,
    "running": False,
    "total_added": 0,
    "subs": 0,
    "gifted_subs": 0,
    "bits": 0,
    "last_event": None,
}
oauth_state = None
access_token = None
broadcaster_user_id = None


def current_remaining():
    with state_lock:
        if not state["running"]:
            return max(0, int(state["end_at"] - time.time()))
        remaining = max(0, int(state["end_at"] - time.time()))
        if remaining == 0:
            state["running"] = False
        return remaining


def add_seconds(seconds, label):
    global state
    seconds = max(0, int(seconds))
    with state_lock:
        now = time.time()
        remaining = max(0, state["end_at"] - now) if state["running"] else max(0, state["end_at"] - now)
        new_remaining = min(remaining + seconds, MAX_TIMER_SECONDS)
        state["end_at"] = now + new_remaining
        state["running"] = True
        state["total_added"] += seconds
        state["last_event"] = {"label": label, "seconds": seconds, "at": int(now)}
        return int(new_remaining)


def require_admin():
    supplied = request.headers.get("X-Admin-Token") or request.args.get("token")
    if not supplied or not hmac.compare_digest(supplied, ADMIN_TOKEN):
        abort(401)


def get_broadcaster():
    global access_token, broadcaster_user_id
    if access_token and broadcaster_user_id:
        return broadcaster_user_id

    raise RuntimeError("Not authenticated with Twitch. Visit /oauth/login first.")


def twitch_headers(token):
    return {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def verify_eventsub_signature():
    message_id = request.headers.get("Twitch-Eventsub-Message-Id", "")
    timestamp = request.headers.get("Twitch-Eventsub-Message-Timestamp", "")
    signature = request.headers.get("Twitch-Eventsub-Message-Signature", "")
    body = request.get_data(as_text=True)

    if not message_id or not timestamp or not signature:
        return False

    # Reject very old/replayed messages (10 minutes).
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
        if abs(time.time() - ts) > 600:
            return False
    except ValueError:
        return False

    message = message_id + timestamp + body
    digest = hmac.new(
        EVENTSUB_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    expected = "sha256=" + digest
    return hmac.compare_digest(expected, signature)


@app.get("/")
def index():
    return render_template("timer.html")


@app.get("/admin")
def admin():
    require_admin()
    return render_template("admin.html", token=ADMIN_TOKEN)


@app.get("/api/state")
def api_state():
    remaining = current_remaining()
    with state_lock:
        return jsonify({
            "remaining": remaining,
            "running": state["running"],
            "subs": state["subs"],
            "gifted_subs": state["gifted_subs"],
            "bits": state["bits"],
            "total_added": state["total_added"],
            "last_event": state["last_event"],
        })


@app.post("/api/admin/start")
def admin_start():
    require_admin()
    seconds = int(request.json.get("seconds", 3600)) if request.is_json else 3600
    with state_lock:
        state["end_at"] = time.time() + max(0, min(seconds, MAX_TIMER_SECONDS))
        state["running"] = True
    return jsonify({"ok": True})


@app.post("/api/admin/add")
def admin_add():
    require_admin()
    data = request.get_json(force=True)
    seconds = int(data.get("seconds", 0))
    remaining = add_seconds(seconds, "manual")
    return jsonify({"ok": True, "remaining": remaining})


@app.post("/api/admin/pause")
def admin_pause():
    require_admin()
    remaining = current_remaining()
    with state_lock:
        state["end_at"] = time.time() + remaining
        state["running"] = False
    return jsonify({"ok": True})


@app.post("/api/admin/reset")
def admin_reset():
    require_admin()
    with state_lock:
        state["end_at"] = 0
        state["running"] = False
        state["total_added"] = 0
        state["subs"] = 0
        state["gifted_subs"] = 0
        state["bits"] = 0
        state["last_event"] = None
    return jsonify({"ok": True})


@app.get("/oauth/login")
def oauth_login():
    global oauth_state
    oauth_state = secrets.token_urlsafe(32)
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": f"{PUBLIC_BASE_URL}/oauth/callback",
        "response_type": "code",
        "scope": "channel:read:subscriptions",
        "state": oauth_state,
    }
    return redirect("https://id.twitch.tv/oauth2/authorize?" + urlencode(params))


@app.get("/oauth/callback")
def oauth_callback():
    global oauth_state, access_token, broadcaster_user_id

    code = request.args.get("code")
    returned_state = request.args.get("state")
    if not code or not returned_state or not oauth_state or not hmac.compare_digest(returned_state, oauth_state):
        return "Invalid OAuth state.", 400

    token_response = requests.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": f"{PUBLIC_BASE_URL}/oauth/callback",
        },
        timeout=15,
    )
    if token_response.status_code != 200:
        return f"Twitch OAuth failed: {token_response.text}", 400

    access_token = token_response.json()["access_token"]

    me = requests.get(
        "https://api.twitch.tv/helix/users",
        headers=twitch_headers(access_token),
        timeout=15,
    )
    if me.status_code != 200:
        return f"Could not read Twitch user: {me.text}", 400

    user = me.json()["data"][0]
    broadcaster_user_id = user["id"]

    if user["login"].lower() != BROADCASTER_LOGIN:
        access_token = None
        broadcaster_user_id = None
        return "The Twitch account you authorized does not match TWITCH_BROADCASTER_LOGIN.", 400

    return redirect("/setup")


@app.get("/setup")
def setup():
    require_admin()
    if not access_token or not broadcaster_user_id:
        return redirect("/oauth/login")

    result = create_eventsub_subscriptions()
    return jsonify(result)


def create_eventsub_subscriptions():
    callback = f"{PUBLIC_BASE_URL}/webhooks/twitch"
    event_types = ["channel.subscribe", "channel.cheer", "channel.raid"]

    results = []
    for event_type in event_types:
        payload = {
            "type": event_type,
            "version": "1",
            "condition": {"broadcaster_user_id": broadcaster_user_id},
            "transport": {
                "method": "webhook",
                "callback": callback,
                "secret": EVENTSUB_SECRET,
            },
        }
        r = requests.post(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            headers=twitch_headers(access_token),
            json=payload,
            timeout=15,
        )
        results.append({
            "type": event_type,
            "status": r.status_code,
            "response": r.json() if r.text else {},
        })
    return results


@app.post("/webhooks/twitch")
def twitch_webhook():
    if not verify_eventsub_signature():
        return "Invalid signature", 403

    message_type = request.headers.get("Twitch-Eventsub-Message-Type", "")
    body = request.get_json(force=True)

    if message_type == "webhook_callback_verification":
        # Twitch requires the challenge to be returned as plain text.
        return body["challenge"], 200, {"Content-Type": "text/plain"}

    if message_type != "notification":
        return "", 204

    event = body.get("event", {})
    subscription_type = body.get("subscription", {}).get("type")

    if subscription_type == "channel.subscribe":
        tier = event.get("tier", "1000")
        is_gift = bool(event.get("is_gift", False))

        # Gifted subscriptions use the dedicated gifted value, regardless of tier.
        # Non-gifted subscriptions use their tier-specific value.
        if is_gift:
            seconds = GIFTED_SUB_SECONDS
        elif tier == "3000":
            seconds = TIER3_SUB_SECONDS
        elif tier == "2000":
            seconds = TIER2_SUB_SECONDS
        else:
            seconds = OWN_SUB_SECONDS

        add_seconds(seconds, f"{'Gifted' if is_gift else 'Sub'} T{tier}")
        with state_lock:
            state["subs"] += 1
            if is_gift:
                state["gifted_subs"] += 1

    elif subscription_type == "channel.cheer":
        bits = int(event.get("bits", 0))
        if bits > 0:
            # 100 bits = +3 min. Partial blocks do not add time.
            seconds = (bits // 100) * SECONDS_PER_100_BITS
            if seconds:
                add_seconds(seconds, f"{bits} bits")
            with state_lock:
                state["bits"] += bits

    elif subscription_type == "channel.raid":
        viewers = int(event.get("viewers", 0))
        if viewers > 0:
            # Raid: +1 minute per viewer.
            seconds = viewers * 60
            add_seconds(seconds, f"Raid +{viewers} viewers")

    return "", 204


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
