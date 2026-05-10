import html
import json
import os
import re
import secrets
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import wraps
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus

import requests
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "fnd.sqlite3"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def load_env_file():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file()


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me-with-a-real-secret")
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

    DB_PATH.parent.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    @app.context_processor
    def inject_globals():
        return {
            "current_year": datetime.utcnow().year,
            "current_user": get_current_user(),
            "is_admin": session.get("is_admin", False),
        }

    @app.route("/")
    def index():
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        return render_template("index.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not is_alphabetic_name(name):
                flash("Name must contain alphabetic characters only.", "danger")
                return redirect(url_for("register"))
            if not email or len(password) < 8:
                flash("Use a valid email and an 8+ character password.", "danger")
                return redirect(url_for("register"))
            try:
                execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    (name, email, generate_password_hash(password)),
                )
                flash("Account created. Welcome to the verification grid.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("An account with that email already exists.", "danger")
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = query_one("SELECT * FROM users WHERE email = ?", (email,))
            if user and user["is_blocked"]:
                flash("This account is blocked. Contact the administrator.", "danger")
            elif user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["is_admin"] = bool(user["is_admin"])
                log_activity(user["id"], "login", request.remote_addr)
                flash("Signed in successfully.", "success")
                return redirect(url_for("admin_dashboard" if user["is_admin"] else "dashboard"))
            else:
                flash("Invalid email or password.", "danger")
        return render_template("auth.html", mode="login")

    @app.route("/admin-login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = query_one("SELECT * FROM users WHERE email = ? AND is_admin = 1", (email,))
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["is_admin"] = True
                log_activity(user["id"], "admin_login", request.remote_addr)
                flash("Admin command center unlocked.", "success")
                return redirect(url_for("admin_dashboard"))
            flash("Invalid administrator credentials.", "danger")
        return render_template("auth.html", mode="admin")

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        reset_token = None
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = query_one("SELECT * FROM users WHERE email = ?", (email,))
            if user:
                reset_token = secrets.token_urlsafe(18)
                execute(
                    "UPDATE users SET reset_token = ?, reset_requested_at = ? WHERE id = ?",
                    (reset_token, now(), user["id"]),
                )
            flash("If that email exists, a reset token has been generated.", "info")
        return render_template("forgot_password.html", reset_token=reset_token)

    @app.route("/logout")
    @login_required
    def logout():
        log_activity(session["user_id"], "logout", request.remote_addr)
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("index"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats = user_stats(session["user_id"])
        return render_template("dashboard.html", stats=stats)

    @app.route("/api/analyze", methods=["POST"])
    @login_required
    def analyze():
        payload = request.get_json(force=True)
        headline = (payload.get("headline") or "").strip()
        content = (payload.get("content") or "").strip()
        source_url = (payload.get("source_url") or "").strip()
        if not headline and not content:
            return jsonify({"error": "Headline or content is required."}), 400
        try:
            result = analyze_news(headline, content, source_url)
        except Exception as exc:
            return jsonify({"error": f"AI analysis failed: {exc}"}), 502
        execute(
            """
            INSERT INTO verifications
            (user_id, headline, content, source_url, verdict, confidence, risk_level, analysis_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                headline,
                content,
                source_url,
                result["prediction"],
                result["confidence"],
                result["risk_level"],
                json.dumps(result),
                now(),
            ),
        )
        execute("UPDATE api_usage SET ai_calls = ai_calls + 1 WHERE id = 1")
        return jsonify(result)

    @app.route("/report/latest")
    @login_required
    def latest_report():
        row = query_one(
            "SELECT * FROM verifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (session["user_id"],),
        )
        if not row:
            flash("Run an analysis before downloading a report.", "warning")
            return redirect(url_for("dashboard"))
        return send_pdf_report(row)

    @app.route("/report/<int:verification_id>")
    @login_required
    def report(verification_id):
        row = query_one("SELECT * FROM verifications WHERE id = ?", (verification_id,))
        if not row or (row["user_id"] != session["user_id"] and not session.get("is_admin")):
            flash("Report not found.", "danger")
            return redirect(url_for("history"))
        return send_pdf_report(row)

    @app.route("/live-news")
    @login_required
    def live_news():
        category = request.args.get("category", "general")
        search = request.args.get("q", "")
        page = int(request.args.get("page", 1))
        articles = fetch_live_news(category, search, page)
        execute("UPDATE api_usage SET news_calls = news_calls + 1 WHERE id = 1")
        return render_template("live_news.html", articles=articles, category=category, search=search, page=page)

    @app.route("/featured")
    @login_required
    def featured():
        q = request.args.get("q", "").strip()
        tag = request.args.get("tag", "").strip()
        sql = "SELECT f.*, u.name AS author FROM featured_news f JOIN users u ON u.id = f.admin_id WHERE 1=1"
        params = []
        if q:
            sql += " AND (f.title LIKE ? OR f.content LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if tag:
            sql += " AND f.tags LIKE ?"
            params.append(f"%{tag}%")
        sql += " ORDER BY f.created_at DESC"
        items = query_all(sql, tuple(params))
        return render_template("featured.html", items=items, q=q, tag=tag)

    @app.route("/history")
    @login_required
    def history():
        q = request.args.get("q", "").strip()
        verdict = request.args.get("verdict", "").strip()
        sql = "SELECT * FROM verifications WHERE user_id = ?"
        params = [session["user_id"]]
        if q:
            sql += " AND (headline LIKE ? OR content LIKE ?)"
            params += [f"%{q}%", f"%{q}%"]
        if verdict:
            sql += " AND verdict = ?"
            params.append(verdict)
        sql += " ORDER BY created_at DESC"
        rows = query_all(sql, tuple(params))
        return render_template("history.html", rows=rows, q=q, verdict=verdict)

    @app.route("/history/<int:verification_id>/delete", methods=["POST"])
    @login_required
    def delete_history(verification_id):
        execute("DELETE FROM verifications WHERE id = ? AND user_id = ?", (verification_id, session["user_id"]))
        flash("Verification deleted.", "info")
        return redirect(url_for("history"))

    @app.route("/connect", methods=["GET", "POST"])
    @login_required
    def connect():
        if request.method == "POST":
            execute(
                "INSERT INTO feedback (user_id, name, email, subject, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session["user_id"],
                    request.form.get("name", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("subject", "").strip(),
                    request.form.get("message", "").strip(),
                    now(),
                ),
            )
            flash("Feedback transmitted. Thank you for sharpening the system.", "success")
            return redirect(url_for("connect"))
        return render_template("connect.html")

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        user = get_current_user()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            execute("UPDATE users SET name = ? WHERE id = ?", (name, user["id"]))
            flash("Profile updated.", "success")
            return redirect(url_for("profile"))
        return render_template("profile.html", user=user, stats=user_stats(user["id"]))

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        stats = {
            "users": query_one("SELECT COUNT(*) c FROM users WHERE is_admin = 0")["c"],
            "verifications": query_one("SELECT COUNT(*) c FROM verifications")["c"],
            "fake": query_one("SELECT COUNT(*) c FROM verifications WHERE verdict = 'Fake'")["c"],
            "real": query_one("SELECT COUNT(*) c FROM verifications WHERE verdict = 'Real'")["c"],
            "feedback": query_one("SELECT COUNT(*) c FROM feedback")["c"],
            "usage": query_one("SELECT * FROM api_usage WHERE id = 1"),
        }
        recent = query_all(
            "SELECT v.*, u.name FROM verifications v JOIN users u ON u.id = v.user_id ORDER BY v.created_at DESC LIMIT 8"
        )
        return render_template("admin/dashboard.html", stats=stats, recent=recent)

    @app.route("/admin/users")
    @admin_required
    def admin_users():
        users = query_all("SELECT * FROM users ORDER BY created_at DESC")
        logs = query_all(
            "SELECT l.*, u.email FROM login_logs l JOIN users u ON u.id = l.user_id ORDER BY l.created_at DESC LIMIT 80"
        )
        return render_template("admin/users.html", users=users, logs=logs)

    @app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
    @admin_required
    def admin_toggle_user(user_id):
        execute("UPDATE users SET is_blocked = 1 - is_blocked WHERE id = ? AND is_admin = 0", (user_id,))
        flash("User status updated.", "info")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/reset", methods=["POST"])
    @admin_required
    def admin_reset_password(user_id):
        temp_password = secrets.token_urlsafe(8)
        execute(
            "UPDATE users SET password_hash = ?, reset_token = NULL WHERE id = ? AND is_admin = 0",
            (generate_password_hash(temp_password), user_id),
        )
        flash(f"Temporary password: {temp_password}", "warning")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(user_id):
        execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
        flash("User removed.", "info")
        return redirect(url_for("admin_users"))

    @app.route("/admin/featured", methods=["GET", "POST"])
    @admin_required
    def admin_featured():
        if request.method == "POST":
            image_path = save_upload(request.files.get("image"))
            execute(
                """
                INSERT INTO featured_news (admin_id, title, content, image_path, tags, category, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    request.form.get("title", "").strip(),
                    request.form.get("content", "").strip(),
                    image_path,
                    request.form.get("tags", "").strip(),
                    request.form.get("category", "").strip(),
                    now(),
                ),
            )
            flash("Featured article published.", "success")
            return redirect(url_for("admin_featured"))
        items = query_all("SELECT * FROM featured_news ORDER BY created_at DESC")
        return render_template("admin/featured.html", items=items)

    @app.route("/admin/featured/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_featured(item_id):
        execute("DELETE FROM featured_news WHERE id = ?", (item_id,))
        flash("Featured article deleted.", "info")
        return redirect(url_for("admin_featured"))

    @app.route("/admin/feedback")
    @admin_required
    def admin_feedback():
        messages = query_all(
            "SELECT f.*, u.email AS user_email FROM feedback f LEFT JOIN users u ON u.id = f.user_id ORDER BY f.created_at DESC"
        )
        return render_template("admin/feedback.html", messages=messages)

    @app.route("/admin/feedback/<int:feedback_id>/delete", methods=["POST"])
    @admin_required
    def admin_delete_feedback(feedback_id):
        execute("DELETE FROM feedback WHERE id = ?", (feedback_id,))
        flash("Feedback archived.", "info")
        return redirect(url_for("admin_feedback"))

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404, message="Signal not found"), 404

    @app.errorhandler(500)
    def server_error(_):
        return render_template("error.html", code=500, message="The analysis engine hit turbulence"), 500

    return app


def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def execute(sql, params=()):
    with connect_db() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def query_one(sql, params=()):
    with connect_db() as conn:
        return conn.execute(sql, params).fetchone()


def query_all(sql, params=()):
    with connect_db() as conn:
        return conn.execute(sql, params).fetchall()


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        is_blocked INTEGER DEFAULT 0,
        reset_token TEXT,
        reset_requested_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        headline TEXT,
        content TEXT,
        source_url TEXT,
        verdict TEXT,
        confidence REAL,
        risk_level TEXT,
        analysis_json TEXT,
        created_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS featured_news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        image_path TEXT,
        tags TEXT,
        category TEXT,
        created_at TEXT,
        FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        email TEXT,
        subject TEXT,
        message TEXT,
        created_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS login_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        ip_address TEXT,
        created_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS api_usage (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        ai_calls INTEGER DEFAULT 0,
        news_calls INTEGER DEFAULT 0
    );
    """
    with connect_db() as conn:
        conn.executescript(schema)
        conn.execute("INSERT OR IGNORE INTO api_usage (id, ai_calls, news_calls) VALUES (1, 0, 0)")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@truthlens.ai")
        admin_password = os.getenv("ADMIN_PASSWORD", "Admin@12345")
        exists = conn.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (name, email, password_hash, is_admin) VALUES (?, ?, ?, 1)",
                ("TruthLens Admin", admin_email, generate_password_hash(admin_password)),
            )
        conn.commit()


def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        user = get_current_user()
        if user and user["is_blocked"]:
            session.clear()
            flash("This account is blocked.", "danger")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id") or not session.get("is_admin"):
            flash("Administrator access required.", "danger")
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)

    return wrapper


def log_activity(user_id, action, ip):
    execute(
        "INSERT INTO login_logs (user_id, action, ip_address, created_at) VALUES (?, ?, ?, ?)",
        (user_id, action, ip, now()),
    )


def user_stats(user_id):
    return {
        "total": query_one("SELECT COUNT(*) c FROM verifications WHERE user_id = ?", (user_id,))["c"],
        "fake": query_one("SELECT COUNT(*) c FROM verifications WHERE user_id = ? AND verdict = 'Fake'", (user_id,))["c"],
        "real": query_one("SELECT COUNT(*) c FROM verifications WHERE user_id = ? AND verdict = 'Real'", (user_id,))["c"],
    }


def analyze_news(headline, content, source_url):
    provider = os.getenv("AI_PROVIDER", "fallback").lower()
    if provider in {"openrouter", "open_router"} and valid_key("OPENROUTER_API_KEY"):
        return analyze_with_openrouter(headline, content, source_url)
    if provider == "openai" and valid_key("OPENAI_API_KEY"):
        return analyze_with_openai(headline, content, source_url)
    if provider == "gemini" and valid_key("GEMINI_API_KEY"):
        return analyze_with_gemini(headline, content, source_url)
    return heuristic_analysis(headline, content, source_url)


def valid_key(env_name):
    value = (os.getenv(env_name) or "").strip()
    if not value:
        return False
    invalid_markers = ("replace-with", "your_key_here", "your-api-key", "sk-...")
    return not any(marker in value.lower() for marker in invalid_markers)


def is_alphabetic_name(name):
    return bool(re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", name or ""))


def ai_prompt(headline, content, source_url):
    return f"""
You are a careful misinformation analyst. Return strict JSON only with keys:
prediction, confidence, detailed_reasoning, source_credibility, bias_detection,
emotional_manipulation, similar_references, risk_level, timestamp, verification_summary.
Prediction must be Real or Fake. Confidence is 0-100.

Headline: {headline}
Content: {content}
Source URL: {source_url or "Not provided"}
"""


def normalize_ai_result(data):
    data.setdefault("prediction", "Needs Review")
    data.setdefault("confidence", 64)
    data.setdefault("detailed_reasoning", "The model returned a partial assessment.")
    data.setdefault("source_credibility", "Insufficient public source metadata supplied.")
    data.setdefault("bias_detection", "Moderate framing signals detected.")
    data.setdefault("emotional_manipulation", "No extreme manipulation markers found.")
    data.setdefault("similar_references", ["Check reputable outlets and official releases."])
    data.setdefault("risk_level", "Medium")
    data["timestamp"] = now()
    data.setdefault("verification_summary", "Use this AI result as a decision-support signal, not final proof.")
    data["confidence"] = float(data.get("confidence", 64))
    return data


def parse_json_response(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def analyze_with_openai(headline, content, source_url):
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}", "Content-Type": "application/json"},
        json={
            "model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            "input": ai_prompt(headline, content, source_url),
            "temperature": 0.2,
        },
        timeout=30,
    )
    response.raise_for_status()
    raw = response.json().get("output_text", "{}")
    return normalize_ai_result(parse_json_response(raw))


def analyze_with_openrouter(headline, content, source_url):
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://127.0.0.1:5000"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "TruthLens AI"),
        },
        json={
            "model": os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful misinformation analyst. Return strict JSON only.",
                },
                {"role": "user", "content": ai_prompt(headline, content, source_url)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        timeout=30,
    )
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]
    return normalize_ai_result(parse_json_response(raw))


def analyze_with_gemini(headline, content, source_url):
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": ai_prompt(headline, content, source_url)}]}]},
        timeout=30,
    )
    response.raise_for_status()
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    return normalize_ai_result(parse_json_response(text))


def heuristic_analysis(headline, content, source_url):
    text = f"{headline} {content}".lower()
    red_flags = ["shocking", "miracle", "secret", "they don't want", "100%", "must share", "breaking!!!", "exposed"]
    credibility_terms = ["according to", "official", "study", "data", "statement", "reported by", "court", "ministry"]
    red_score = sum(term in text for term in red_flags)
    credible_score = sum(term in text for term in credibility_terms)
    source_bonus = 1 if source_url and any(x in source_url for x in [".gov", ".edu", "reuters", "apnews", "bbc"]) else 0
    fake_probability = max(8, min(92, 48 + red_score * 12 - credible_score * 7 - source_bonus * 15))
    prediction = "Fake" if fake_probability >= 55 else "Real"
    confidence = fake_probability if prediction == "Fake" else 100 - fake_probability
    risk = "High" if fake_probability >= 70 else "Medium" if fake_probability >= 42 else "Low"
    return normalize_ai_result(
        {
            "prediction": prediction,
            "confidence": round(confidence, 1),
            "detailed_reasoning": (
                "Fallback analysis weighed sensational language, attribution quality, source hints, and evidence density. "
                "Configure OPENAI_API_KEY or GEMINI_API_KEY for deeper model-based verification."
            ),
            "source_credibility": (
                "The supplied source appears stronger than average." if source_bonus else
                "No strong trusted-domain signal was found; verify source ownership and editorial history."
            ),
            "bias_detection": "Language framing suggests elevated bias." if red_score else "No strong partisan framing was detected.",
            "emotional_manipulation": (
                "Sensational or urgency-driven wording was detected." if red_score else
                "The text does not strongly rely on fear, outrage, or forced urgency."
            ),
            "similar_references": [
                "Search reputable wires such as AP, Reuters, BBC, and official agency releases.",
                "Compare dates, named people, quotes, and original documents before sharing.",
            ],
            "risk_level": risk,
            "verification_summary": f"{prediction} leaning result with {round(confidence, 1)}% confidence.",
        }
    )


def fetch_live_news(category, search, page):
    api_key = os.getenv("NEWS_API_KEY") or os.getenv("GNEWS_API_KEY")
    if valid_key("NEWS_API_KEY"):
        url = "https://newsapi.org/v2/top-headlines"
        params = {"apiKey": api_key, "category": category if category != "general" else None, "q": search, "page": page, "pageSize": 20}
        data = requests.get(url, params={k: v for k, v in params.items() if v}, timeout=20).json()
        return data.get("articles", [])
    if valid_key("GNEWS_API_KEY"):
        url = "https://gnews.io/api/v4/top-headlines"
        data = requests.get(url, params={"token": api_key, "topic": category, "q": search, "page": page, "max": 20}, timeout=20).json()
        return data.get("articles", [])
    try:
        return fetch_gdelt_news(category, search, page)
    except requests.RequestException:
        return fetch_google_news_rss(category, search, page)


def fetch_gdelt_news(category, search, page):
    category_queries = {
        "general": "news",
        "technology": "(technology OR artificial intelligence OR cybersecurity OR software)",
        "politics": "(election OR government OR parliament OR president OR minister)",
        "sports": "(sports OR football OR cricket OR tennis OR olympics)",
        "business": "(business OR economy OR markets OR finance OR startup)",
        "science": "(science OR research OR space OR climate OR discovery)",
        "entertainment": "(entertainment OR film OR music OR celebrity OR streaming)",
        "health": "(health OR medicine OR disease OR hospital OR vaccine)",
    }
    query = search.strip() if search else category_queries.get(category, "news")
    start_record = max(1, ((page - 1) * 20) + 1)
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 20,
        "startrecord": start_record,
        "sort": "datedesc",
        "timespan": os.getenv("GDELT_TIMESPAN", "48h"),
    }
    response = requests.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    articles = []
    for index, item in enumerate(data.get("articles", []), start=1):
        image = item.get("socialimage") or item.get("image") or f"https://picsum.photos/seed/gdelt-{quote_plus(item.get('domain', 'news'))}-{page}-{index}/900/540"
        articles.append(
            {
                "title": item.get("title") or "Untitled live news article",
                "description": item.get("seendate") or "Live article indexed by GDELT.",
                "url": item.get("url") or "#",
                "urlToImage": image,
                "publishedAt": item.get("seendate") or now(),
                "source": {"name": item.get("domain") or item.get("sourcecountry") or "GDELT"},
            }
        )
    return articles


def fetch_google_news_rss(category, search, page):
    category_queries = {
        "general": "world news",
        "technology": "technology news",
        "politics": "politics news",
        "sports": "sports news",
        "business": "business news",
        "science": "science news",
        "entertainment": "entertainment news",
        "health": "health news",
    }
    query = search.strip() if search else category_queries.get(category, "world news")
    params = {
        "q": f"{query} when:{os.getenv('GOOGLE_NEWS_TIMESPAN', '2d')}",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    response = requests.get("https://news.google.com/rss/search", params=params, timeout=20)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    items = root.findall("./channel/item")
    start = max(0, (page - 1) * 20)
    articles = []
    for index, item in enumerate(items[start : start + 20], start=1):
        title = item.findtext("title") or "Untitled live news article"
        description = clean_html(item.findtext("description") or "Live article from Google News RSS.")
        source = item.find("source")
        source_name = source.text if source is not None and source.text else "Google News"
        articles.append(
            {
                "title": title,
                "description": description,
                "url": item.findtext("link") or "#",
                "urlToImage": f"https://picsum.photos/seed/google-news-{quote_plus(source_name)}-{page}-{index}/900/540",
                "publishedAt": item.findtext("pubDate") or now(),
                "source": {"name": source_name},
            }
        )
    return articles


def clean_html(value):
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()


def save_upload(file):
    if not file or not file.filename:
        return ""
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        flash("Unsupported image type.", "danger")
        return ""
    filename = f"{secrets.token_hex(10)}-{secure_filename(file.filename)}"
    path = UPLOAD_DIR / filename
    file.save(path)
    return f"uploads/{filename}"


def send_pdf_report(row):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=42, bottomMargin=42)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("TitleNeon", parent=styles["Title"], textColor=colors.HexColor("#111827"), fontSize=22, spaceAfter=18)
    h = ParagraphStyle("Heading", parent=styles["Heading2"], textColor=colors.HexColor("#2563eb"), spaceBefore=12)
    body = ParagraphStyle("Body", parent=styles["BodyText"], leading=15)
    analysis = json.loads(row["analysis_json"])
    story = [
        Paragraph("TruthLens AI Verification Report", title),
        Paragraph(f"Generated: {now()}", body),
        Spacer(1, 0.15 * inch),
        Table(
            [["Verdict", analysis["prediction"]], ["Confidence", f"{analysis['confidence']}%"], ["Risk Level", analysis["risk_level"]]],
            colWidths=[1.5 * inch, 4.7 * inch],
            style=[
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("PADDING", (0, 0), (-1, -1), 9),
            ],
        ),
        Paragraph("Entered News", h),
        Paragraph(f"<b>Headline:</b> {escape_pdf(row['headline'] or 'N/A')}", body),
        Paragraph(f"<b>Source:</b> {escape_pdf(row['source_url'] or 'N/A')}", body),
        Paragraph(escape_pdf(row["content"] or "No article body provided."), body),
        Paragraph("AI Reasoning", h),
        Paragraph(escape_pdf(analysis["detailed_reasoning"]), body),
        Paragraph("Source Credibility", h),
        Paragraph(escape_pdf(analysis["source_credibility"]), body),
        Paragraph("Bias and Manipulation", h),
        Paragraph(escape_pdf(analysis["bias_detection"]), body),
        Paragraph(escape_pdf(analysis["emotional_manipulation"]), body),
        Paragraph("Verification Summary", h),
        Paragraph(escape_pdf(analysis["verification_summary"]), body),
    ]
    doc.build(story)
    buffer.seek(0)
    filename = f"truthlens-report-{row['id']}.pdf"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


def escape_pdf(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
