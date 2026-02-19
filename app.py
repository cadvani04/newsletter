import os
import re
import json
import csv
import io
import smtplib
import uvicorn
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

import traceback

load_dotenv()

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "scll-secret-2024"))
templates = Jinja2Templates(directory="templates")

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    return HTMLResponse(
        f"<pre style='background:#1a1a1a;color:#f55;padding:40px;font-size:13px;'>"
        f"ERROR: {type(exc).__name__}: {exc}\n\n{traceback.format_exc()}</pre>",
        status_code=500,
    )

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "email_template.html")
DATA_DIR      = os.getenv("DATA_DIR", BASE_DIR)
DATA_PATH     = os.path.join(DATA_DIR, "data.json")


# ── Flash helpers ─────────────────────────────────────────────────────────────

def flash(request: Request, message: str, category: str = "success"):
    request.session["_flash"] = {"message": message, "category": category}

def get_flash(request: Request):
    return request.session.pop("_flash", None)

def render(request: Request, template: str, context: dict = {}):
    return templates.TemplateResponse(
        template, {"request": request, "flash": get_flash(request), **context}
    )


# ── Persistent data ───────────────────────────────────────────────────────────

def load_data() -> dict:
    defaults = {
        "issue_counter": 1,
        "draft": {},
        "subscribers": [],
        "send_history": [],
        "click_log": [],
    }
    if not os.path.exists(DATA_PATH):
        save_data(defaults)
        return defaults
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        stored = json.load(f)
    for k, v in defaults.items():
        stored.setdefault(k, v)
    return stored

def save_data(data: dict):
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Template fields ───────────────────────────────────────────────────────────

TEMPLATE_FIELDS = {
    "issue": {
        "label": "Issue Info",
        "fields": [
            ("ISSUE_NUMBER",    "Issue Number",      "text",     ""),
            ("ISSUE_DATE",      "Issue Date",        "text",     ""),
            ("ISSUE_INTRO_LINE","Intro Banner Line", "text",     "Three Santa Cruz spots worth knowing this week."),
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
            ("REPLY_EMAIL",     "Reply-To Email",         "email", os.getenv("REPLY_EMAIL", "")),
            ("FORWARD_URL",     "Forward URL",            "url",   ""),
            ("SIGNUP_URL",      "Sign-Up URL",            "url",   ""),
            ("COMPANY_ADDRESS", "Company Street Address", "text",  ""),
            ("PREFERENCES_URL", "Preferences URL",        "url",   "#"),
            ("UNSUBSCRIBE_URL", "Unsubscribe URL",        "url",   "#"),
        ]
    },
}

TRACKED_URL_FIELDS = {"BIZ_1_WEBSITE", "BIZ_2_WEBSITE", "BIZ_3_WEBSITE", "FORWARD_URL", "SIGNUP_URL"}

ALL_KEYS = [key for section in TEMPLATE_FIELDS.values() for key, *_ in section["fields"]]


# ── Email helpers ─────────────────────────────────────────────────────────────

def make_tracking_url(original_url: str, issue_number: str, field_key: str, base: str) -> str:
    if not original_url or original_url in ("#", "https://"):
        return original_url
    params = urllib.parse.urlencode({"url": original_url, "issue": issue_number, "ref": field_key})
    return f"{base}/track?{params}"

def fill_template(data: dict, track_links: bool = False, base_url: str = "") -> str:
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    issue_number = data.get("ISSUE_NUMBER", "")
    for key, value in data.items():
        val = value or ""
        if track_links and key in TRACKED_URL_FIELDS and val:
            val = make_tracking_url(val, issue_number, key, base_url)
        html = html.replace("{{" + key + "}}", val)
    return html

def send_email(html_body: str, subject: str, recipients: list) -> tuple:
    smtp_host  = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port  = int(os.getenv("SMTP_PORT", 587))
    smtp_user  = os.getenv("SMTP_USER", "")
    smtp_pass  = os.getenv("SMTP_PASS", "")
    from_name  = os.getenv("FROM_NAME", "Santa Cruz Local Legends")
    from_email = os.getenv("FROM_EMAIL", smtp_user)
    if not smtp_user or not smtp_pass:
        return False, "SMTP credentials not configured. Go to Settings."
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{from_email}>"
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo(); server.starttls(); server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, recipients, msg.as_string())
        return True, f"Sent to {len(recipients)} recipient(s)."
    except Exception as e:
        return False, f"Send failed: {str(e)}"

def today() -> str:
    now = datetime.now()
    return f"{now.strftime('%B')} {now.day}, {now.year}"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    db = load_data()
    draft = db.get("draft", {})
    if not draft.get("ISSUE_NUMBER"):
        draft["ISSUE_NUMBER"] = str(db["issue_counter"])
    if not draft.get("ISSUE_DATE"):
        draft["ISSUE_DATE"] = today()
    return render(request, "form.html", {
        "sections":         TEMPLATE_FIELDS,
        "draft":            draft,
        "subscriber_count": len(db.get("subscribers", [])),
        "send_history":     db.get("send_history", [])[-10:][::-1],
        "issue_counter":    db["issue_counter"],
    })


@app.post("/draft/save")
async def save_draft(request: Request):
    form = await request.form()
    db = load_data()
    db["draft"] = {k: form.get(k, "") for k in ALL_KEYS}
    save_data(db)
    return JSONResponse({"ok": True})


@app.post("/draft/clear")
async def clear_draft(request: Request):
    db = load_data()
    db["issue_counter"] += 1
    db["draft"] = {}
    save_data(db)
    return JSONResponse({"ok": True, "next_issue": db["issue_counter"]})


@app.post("/preview", response_class=HTMLResponse)
async def preview(request: Request):
    form = await request.form()
    data = {k: form.get(k, "") for k in ALL_KEYS}
    return fill_template(data, track_links=False)


@app.post("/send")
async def send(request: Request):
    db   = load_data()
    form = await request.form()
    data = {k: form.get(k, "") for k in ALL_KEYS}

    send_to        = form.get("send_to", "list")
    recipients_raw = form.get("recipients", "").strip()
    subject        = form.get("email_subject", "").strip() or \
                     f"Santa Cruz Local Legends — Issue #{data.get('ISSUE_NUMBER', '')}"

    if send_to == "list":
        recipients = [s["email"] for s in db.get("subscribers", [])]
        if not recipients:
            return JSONResponse({"ok": False, "message": "Subscriber list is empty."})
    else:
        if not recipients_raw:
            return JSONResponse({"ok": False, "message": "Enter at least one recipient."})
        recipients = [e.strip() for e in re.split(r"[,\n]+", recipients_raw) if e.strip()]

    base_url  = os.getenv("APP_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    html_body = fill_template(data, track_links=True, base_url=base_url)
    ok, message = send_email(html_body, subject, recipients)

    if ok:
        db["send_history"].append({
            "issue":      data.get("ISSUE_NUMBER", "?"),
            "date":       datetime.now().strftime("%b") + f" {datetime.now().day}, {datetime.now().year} at " + datetime.now().strftime("%I:%M %p").lstrip("0"),
            "subject":    subject,
            "recipients": len(recipients),
        })
        try:
            db["issue_counter"] = int(data.get("ISSUE_NUMBER", db["issue_counter"])) + 1
        except ValueError:
            db["issue_counter"] += 1
        db["draft"] = {}
        save_data(db)

    return JSONResponse({"ok": ok, "message": message})


@app.get("/track")
async def track(request: Request, url: str = "/", issue: str = "?", ref: str = "?"):
    db = load_data()
    db.setdefault("click_log", []).append({
        "issue": issue, "ref": ref, "url": url,
        "time": datetime.now().isoformat(),
    })
    save_data(db)
    return RedirectResponse(url)


# ── Subscribers ───────────────────────────────────────────────────────────────

@app.get("/subscribers", response_class=HTMLResponse)
async def subscribers_page(request: Request):
    db = load_data()
    return render(request, "subscribers.html", {"subscribers": db.get("subscribers", [])})


@app.post("/subscribers/add")
async def add_subscriber(request: Request):
    form  = await request.form()
    email = form.get("email", "").strip().lower()
    name  = form.get("name", "").strip()
    if not email or "@" not in email:
        flash(request, "Please enter a valid email address.", "error")
        return RedirectResponse("/subscribers", status_code=303)
    db = load_data()
    if any(s["email"] == email for s in db["subscribers"]):
        flash(request, f"{email} is already on the list.", "warning")
        return RedirectResponse("/subscribers", status_code=303)
    db["subscribers"].append({"email": email, "name": name, "added": today()})
    save_data(db)
    flash(request, f"Added {email}.", "success")
    return RedirectResponse("/subscribers", status_code=303)


@app.post("/subscribers/remove/{email:path}")
async def remove_subscriber(request: Request, email: str):
    db = load_data()
    db["subscribers"] = [s for s in db["subscribers"] if s["email"] != email]
    save_data(db)
    flash(request, f"Removed {email}.", "success")
    return RedirectResponse("/subscribers", status_code=303)


@app.post("/subscribers/import")
async def import_subscribers(request: Request, csv_file: UploadFile = File(...)):
    db       = load_data()
    existing = {s["email"] for s in db["subscribers"]}
    content  = await csv_file.read()
    reader   = csv.DictReader(io.StringIO(content.decode("utf-8", errors="ignore")))
    added    = 0
    for row in reader:
        email = (row.get("email") or row.get("Email") or "").strip().lower()
        name  = (row.get("name")  or row.get("Name")  or "").strip()
        if email and "@" in email and email not in existing:
            db["subscribers"].append({"email": email, "name": name, "added": today()})
            existing.add(email)
            added += 1
    save_data(db)
    flash(request, f"Imported {added} new subscriber(s).", "success")
    return RedirectResponse("/subscribers", status_code=303)


@app.get("/subscribers/export")
async def export_subscribers():
    db     = load_data()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["email", "name", "added"])
    writer.writeheader()
    writer.writerows(db.get("subscribers", []))
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/analytics", response_class=HTMLResponse)
async def analytics(request: Request):
    db        = load_data()
    click_log = db.get("click_log", [])
    by_issue  = {}
    for c in click_log:
        by_issue.setdefault(c["issue"], []).append(c)
    return render(request, "analytics.html", {
        "by_issue":     by_issue,
        "total_clicks": len(click_log),
        "send_history": db.get("send_history", [])[::-1],
    })


# ── Settings ──────────────────────────────────────────────────────────────────

ENV_VARS = [
    ("SMTP_HOST",   "SMTP Host",                    "text",     "smtp.gmail.com"),
    ("SMTP_PORT",   "SMTP Port",                    "number",   "587"),
    ("SMTP_USER",   "SMTP Username / Email",         "email",    ""),
    ("SMTP_PASS",   "SMTP Password / App Password", "password", ""),
    ("FROM_NAME",   "From Name",                    "text",     "Santa Cruz Local Legends"),
    ("FROM_EMAIL",  "From Email",                   "email",    ""),
    ("REPLY_EMAIL", "Default Reply-To Email",       "email",    ""),
    ("SECRET_KEY",  "Flask Secret Key",             "text",     ""),
]

@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    current = {var: os.getenv(var, default) for var, _, __, default in ENV_VARS}
    return render(request, "settings.html", {"env_vars": ENV_VARS, "current": current})

@app.post("/settings")
async def settings_post(request: Request):
    form     = await request.form()
    env_path = os.path.join(BASE_DIR, ".env")
    lines    = []
    for var, *_ in ENV_VARS:
        val = form.get(var, "").strip()
        if val:
            lines.append(f'{var}="{val}"')
    try:
        with open(env_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        load_dotenv(env_path, override=True)
        flash(request, "Settings saved to .env!", "success")
    except OSError:
        flash(request, "Could not write .env — set these as Railway Variables instead.", "warning")
    return RedirectResponse("/settings", status_code=303)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
