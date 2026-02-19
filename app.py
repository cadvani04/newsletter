import os
import re
import json
import csv
import io
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import (Flask, render_template, request, jsonify,
                   redirect, url_for, flash, Response)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "scll-secret-2024")

BASE_DIR      = os.path.dirname(__file__)
TEMPLATE_PATH = os.path.join(BASE_DIR, "email_template.html")

# DATA_DIR can be overridden via env var so Railway Volumes (or any mounted
# persistent disk) keep data across redeploys.  Default = next to app.py.
DATA_DIR  = os.getenv("DATA_DIR", BASE_DIR)
DATA_PATH = os.path.join(DATA_DIR, "data.json")

# ── Persistent data helpers ──────────────────────────────────────────────────

def load_data() -> dict:
    """Load the entire data.json file, creating it with defaults if missing."""
    defaults = {
        "issue_counter": 1,
        "draft": {},
        "subscribers": [],      # list of {"email": ..., "name": ..., "added": ...}
        "send_history": []       # list of {"issue": N, "date": ..., "subject": ..., "recipients": N}
    }
    if not os.path.exists(DATA_PATH):
        save_data(defaults)
        return defaults
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        stored = json.load(f)
    # merge in any missing keys
    for k, v in defaults.items():
        stored.setdefault(k, v)
    return stored


def save_data(data: dict):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Template fields definition ───────────────────────────────────────────────

TEMPLATE_FIELDS = {
    "issue": {
        "label": "Issue Info",
        "fields": [
            ("ISSUE_NUMBER",    "Issue Number",        "text",     ""),
            ("ISSUE_DATE",      "Issue Date",          "text",     ""),
            ("ISSUE_INTRO_LINE","Intro Banner Line",   "text",     "Three Santa Cruz spots worth knowing this week."),
        ]
    },
    "biz1": {
        "label": "Business 1",
        "fields": [
            ("BIZ_1_NAME",        "Name",         "text",     ""),
            ("BIZ_1_TYPE",        "Type / Badge", "text",     "Restaurant"),
            ("BIZ_1_NEIGHBORHOOD","Neighborhood", "text",     ""),
            ("BIZ_1_DESCRIPTION", "Description",  "textarea", ""),
            ("BIZ_1_ORDER_THIS",  "Order This",   "text",     ""),
            ("BIZ_1_ADDRESS",     "Address",      "text",     ""),
            ("BIZ_1_HOURS",       "Hours",        "text",     ""),
            ("BIZ_1_PRICE",       "Price Range",  "text",     "$$"),
            ("BIZ_1_WEBSITE",     "Website URL",  "url",      "https://"),
            ("BIZ_1_PHOTO",       "Photo URL",    "url",      ""),
        ]
    },
    "biz2": {
        "label": "Business 2",
        "fields": [
            ("BIZ_2_NAME",        "Name",         "text",     ""),
            ("BIZ_2_TYPE",        "Type / Badge", "text",     "Café"),
            ("BIZ_2_NEIGHBORHOOD","Neighborhood", "text",     ""),
            ("BIZ_2_DESCRIPTION", "Description",  "textarea", ""),
            ("BIZ_2_ORDER_THIS",  "Order This",   "text",     ""),
            ("BIZ_2_ADDRESS",     "Address",      "text",     ""),
            ("BIZ_2_HOURS",       "Hours",        "text",     ""),
            ("BIZ_2_PRICE",       "Price Range",  "text",     "$$"),
            ("BIZ_2_WEBSITE",     "Website URL",  "url",      "https://"),
            ("BIZ_2_PHOTO",       "Photo URL",    "url",      ""),
        ]
    },
    "biz3": {
        "label": "Business 3",
        "fields": [
            ("BIZ_3_NAME",        "Name",         "text",     ""),
            ("BIZ_3_TYPE",        "Type / Badge", "text",     "Shop"),
            ("BIZ_3_NEIGHBORHOOD","Neighborhood", "text",     ""),
            ("BIZ_3_DESCRIPTION", "Description",  "textarea", ""),
            ("BIZ_3_ORDER_THIS",  "Order This",   "text",     ""),
            ("BIZ_3_ADDRESS",     "Address",      "text",     ""),
            ("BIZ_3_HOURS",       "Hours",        "text",     ""),
            ("BIZ_3_PRICE",       "Price Range",  "text",     "$$"),
            ("BIZ_3_WEBSITE",     "Website URL",  "url",      "https://"),
            ("BIZ_3_PHOTO",       "Photo URL",    "url",      ""),
        ]
    },
    "observations": {
        "label": "What We're Seeing",
        "fields": [
            ("OBSERVATION_1_HEADLINE", "Observation 1 — Headline", "text",     ""),
            ("OBSERVATION_1",          "Observation 1 — Body",     "textarea", ""),
            ("OBSERVATION_2_HEADLINE", "Observation 2 — Headline", "text",     ""),
            ("OBSERVATION_2",          "Observation 2 — Body",     "textarea", ""),
            ("OBSERVATION_3_HEADLINE", "Observation 3 — Headline", "text",     ""),
            ("OBSERVATION_3",          "Observation 3 — Body",     "textarea", ""),
        ]
    },
    "meta": {
        "label": "Footer & Links",
        "fields": [
            ("REPLY_EMAIL",      "Reply-To Email",        "email", os.getenv("REPLY_EMAIL", "")),
            ("FORWARD_URL",      "Forward URL",           "url",   ""),
            ("SIGNUP_URL",       "Sign-Up URL",           "url",   ""),
            ("COMPANY_ADDRESS",  "Company Street Address","text",  ""),
            ("PREFERENCES_URL",  "Preferences URL",       "url",   "#"),
            ("UNSUBSCRIBE_URL",  "Unsubscribe URL",       "url",   "#"),
        ]
    },
}

# Fields that contain URLs we want to track clicks on
TRACKED_URL_FIELDS = {
    "BIZ_1_WEBSITE", "BIZ_2_WEBSITE", "BIZ_3_WEBSITE",
    "FORWARD_URL", "SIGNUP_URL",
}


# ── Template / link helpers ──────────────────────────────────────────────────

def make_tracking_url(original_url: str, issue_number: str, field_key: str) -> str:
    """Wrap a URL in our click-tracking redirect.
    Uses APP_URL env var (set this to your Railway public URL) so links in
    sent emails point to the right host — not localhost.
    """
    if not original_url or original_url in ("#", "https://"):
        return original_url
    import urllib.parse
    # APP_URL should be like https://your-app.up.railway.app (no trailing slash)
    base = os.getenv("APP_URL", "").rstrip("/")
    if not base:
        # Fall back to the current request host when called inside a request context
        try:
            from flask import request as _req
            base = _req.host_url.rstrip("/")
        except RuntimeError:
            base = "http://localhost:5050"
    params = urllib.parse.urlencode({"url": original_url, "issue": issue_number, "ref": field_key})
    return f"{base}/track?{params}"


def fill_template(data: dict, track_links: bool = False) -> str:
    """Replace all {{VARIABLE}} placeholders. Optionally wrap tracked URLs."""
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    issue_number = data.get("ISSUE_NUMBER", "")

    for key, value in data.items():
        val = value or ""
        if track_links and key in TRACKED_URL_FIELDS and val:
            val = make_tracking_url(val, issue_number, key)
        html = html.replace("{{" + key + "}}", val)
    return html


def send_email(html_body: str, subject: str, recipients: list) -> tuple:
    """Send the rendered HTML email via SMTP."""
    smtp_host  = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port  = int(os.getenv("SMTP_PORT", 587))
    smtp_user  = os.getenv("SMTP_USER", "")
    smtp_pass  = os.getenv("SMTP_PASS", "")
    from_name  = os.getenv("FROM_NAME", "Santa Cruz Local Legends")
    from_email = os.getenv("FROM_EMAIL", smtp_user)

    if not smtp_user or not smtp_pass:
        return False, "SMTP credentials are not configured. Go to Settings first."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{from_email}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, recipients, msg.as_string())
        return True, f"Sent to {len(recipients)} recipient(s)."
    except Exception as e:
        return False, f"Send failed: {str(e)}"


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    db = load_data()
    # Auto-populate issue number and today's date if draft is empty
    draft = db.get("draft", {})
    if not draft.get("ISSUE_NUMBER"):
        draft["ISSUE_NUMBER"] = str(db["issue_counter"])
    if not draft.get("ISSUE_DATE"):
        draft["ISSUE_DATE"] = datetime.now().strftime("%B %-d, %Y")
    return render_template(
        "form.html",
        sections=TEMPLATE_FIELDS,
        draft=draft,
        subscriber_count=len(db.get("subscribers", [])),
        send_history=db.get("send_history", [])[-10:][::-1],  # last 10, newest first
        issue_counter=db["issue_counter"],
    )


@app.route("/draft/save", methods=["POST"])
def save_draft():
    """Auto-save the current form state."""
    db = load_data()
    db["draft"] = {key: request.form.get(key, "")
                   for section in TEMPLATE_FIELDS.values()
                   for key, *_ in section["fields"]}
    save_data(db)
    return jsonify({"ok": True})


@app.route("/draft/clear", methods=["POST"])
def clear_draft():
    """Clear draft and advance issue counter for a fresh issue."""
    db = load_data()
    db["issue_counter"] += 1
    db["draft"] = {}
    save_data(db)
    return jsonify({"ok": True, "next_issue": db["issue_counter"]})


@app.route("/preview", methods=["POST"])
def preview():
    data = {key: request.form.get(key, "")
            for section in TEMPLATE_FIELDS.values()
            for key, *_ in section["fields"]}
    html = fill_template(data, track_links=False)
    return html


@app.route("/send", methods=["POST"])
def send():
    db = load_data()
    data = {key: request.form.get(key, "")
            for section in TEMPLATE_FIELDS.values()
            for key, *_ in section["fields"]}

    send_to = request.form.get("send_to", "list")   # "list" | "custom"
    recipients_raw = request.form.get("recipients", "").strip()
    subject = (request.form.get("email_subject", "").strip()
               or f"Santa Cruz Local Legends — Issue #{data.get('ISSUE_NUMBER', '')}")

    if send_to == "list":
        recipients = [s["email"] for s in db.get("subscribers", [])]
        if not recipients:
            return jsonify({"ok": False, "message": "Subscriber list is empty. Add subscribers first, or switch to custom recipients."})
    else:
        if not recipients_raw:
            return jsonify({"ok": False, "message": "Please enter at least one recipient email."})
        recipients = [e.strip() for e in re.split(r"[,\n]+", recipients_raw) if e.strip()]

    # Render with link tracking enabled for real sends
    html_body = fill_template(data, track_links=True)
    ok, message = send_email(html_body, subject, recipients)

    if ok:
        # Record in history
        db["send_history"].append({
            "issue":      data.get("ISSUE_NUMBER", "?"),
            "date":       datetime.now().strftime("%b %-d, %Y at %-I:%M %p"),
            "subject":    subject,
            "recipients": len(recipients),
        })
        # Advance issue counter for next time
        try:
            next_num = int(data.get("ISSUE_NUMBER", db["issue_counter"])) + 1
        except ValueError:
            next_num = db["issue_counter"] + 1
        db["issue_counter"] = next_num
        db["draft"] = {}
        save_data(db)

    return jsonify({"ok": ok, "message": message})


# ── Link click tracking ───────────────────────────────────────────────────────

@app.route("/track")
def track():
    """Log a click and redirect to the real URL."""
    import urllib.parse
    dest    = request.args.get("url", "/")
    issue   = request.args.get("issue", "?")
    ref     = request.args.get("ref", "?")

    db = load_data()
    clicks = db.setdefault("click_log", [])
    clicks.append({
        "issue": issue,
        "ref":   ref,
        "url":   dest,
        "time":  datetime.now().isoformat(),
    })
    save_data(db)
    return redirect(dest)


# ── Subscriber management ─────────────────────────────────────────────────────

@app.route("/subscribers", methods=["GET"])
def subscribers():
    db = load_data()
    return render_template("subscribers.html", subscribers=db.get("subscribers", []))


@app.route("/subscribers/add", methods=["POST"])
def add_subscriber():
    email = request.form.get("email", "").strip().lower()
    name  = request.form.get("name", "").strip()
    if not email or "@" not in email:
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("subscribers"))
    db = load_data()
    if any(s["email"] == email for s in db["subscribers"]):
        flash(f"{email} is already on the list.", "warning")
        return redirect(url_for("subscribers"))
    db["subscribers"].append({
        "email": email,
        "name":  name,
        "added": datetime.now().strftime("%b %-d, %Y"),
    })
    save_data(db)
    flash(f"Added {email}.", "success")
    return redirect(url_for("subscribers"))


@app.route("/subscribers/remove/<path:email>", methods=["POST"])
def remove_subscriber(email):
    db = load_data()
    db["subscribers"] = [s for s in db["subscribers"] if s["email"] != email]
    save_data(db)
    flash(f"Removed {email}.", "success")
    return redirect(url_for("subscribers"))


@app.route("/subscribers/import", methods=["POST"])
def import_subscribers():
    """Accept a CSV file with columns: email, name (name optional)."""
    f = request.files.get("csv_file")
    if not f:
        flash("No file uploaded.", "error")
        return redirect(url_for("subscribers"))
    db = load_data()
    existing = {s["email"] for s in db["subscribers"]}
    reader = csv.DictReader(io.StringIO(f.read().decode("utf-8", errors="ignore")))
    added = 0
    for row in reader:
        email = (row.get("email") or row.get("Email") or "").strip().lower()
        name  = (row.get("name")  or row.get("Name")  or "").strip()
        if email and "@" in email and email not in existing:
            db["subscribers"].append({
                "email": email,
                "name":  name,
                "added": datetime.now().strftime("%b %-d, %Y"),
            })
            existing.add(email)
            added += 1
    save_data(db)
    flash(f"Imported {added} new subscriber(s).", "success")
    return redirect(url_for("subscribers"))


@app.route("/subscribers/export")
def export_subscribers():
    db = load_data()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["email", "name", "added"])
    writer.writeheader()
    writer.writerows(db.get("subscribers", []))
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


# ── Analytics ────────────────────────────────────────────────────────────────

@app.route("/analytics")
def analytics():
    db = load_data()
    click_log = db.get("click_log", [])
    # Group by issue
    by_issue = {}
    for c in click_log:
        key = c["issue"]
        by_issue.setdefault(key, []).append(c)
    return render_template(
        "analytics.html",
        by_issue=by_issue,
        total_clicks=len(click_log),
        send_history=db.get("send_history", [])[::-1],
    )


# ── Settings ──────────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    env_path = os.path.join(BASE_DIR, ".env")
    env_vars = [
        ("SMTP_HOST",   "SMTP Host",                    "text",     "smtp.gmail.com"),
        ("SMTP_PORT",   "SMTP Port",                    "number",   "587"),
        ("SMTP_USER",   "SMTP Username / Email",         "email",    ""),
        ("SMTP_PASS",   "SMTP Password / App Password", "password", ""),
        ("FROM_NAME",   "From Name",                    "text",     "Santa Cruz Local Legends"),
        ("FROM_EMAIL",  "From Email",                   "email",    ""),
        ("REPLY_EMAIL", "Default Reply-To Email",       "email",    ""),
        ("SECRET_KEY",  "Flask Secret Key",             "text",     ""),
    ]
    if request.method == "POST":
        lines = []
        for var, *_ in env_vars:
            val = request.form.get(var, "").strip()
            if val:
                lines.append(f'{var}="{val}"')
        # Try writing .env (works locally). On Railway the filesystem may be
        # read-only for the project root — in that case silently skip the write
        # and instruct the user to set vars in the Railway dashboard instead.
        try:
            os.makedirs(os.path.dirname(env_path), exist_ok=True)
            with open(env_path, "w") as f:
                f.write("\n".join(lines) + "\n")
            load_dotenv(override=True)
            flash("Settings saved to .env!", "success")
        except OSError:
            flash(
                "Could not write .env (read-only filesystem). "
                "Set these variables in your Railway dashboard under Variables instead.",
                "warning",
            )
        return redirect(url_for("settings"))
    current = {var: os.getenv(var, default) for var, _, __, default in env_vars}
    return render_template("settings.html", env_vars=env_vars, current=current)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    # Never use the reloader — it spawns a child process Railway can't route to
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
