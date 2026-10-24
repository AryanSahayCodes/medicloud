"""MediCloud: hospital OPD appointment booking service.

Runs against PostgreSQL when DATABASE_URL is set (Docker / AWS),
or a local SQLite file otherwise (quick testing on a laptop).
"""
import json
import logging
import os
import re
import socket
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

from flask import Flask, g, jsonify, render_template, request

APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = DATABASE_URL.startswith("postgres")
SQLITE_PATH = os.environ.get("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "medicloud.db"))
HOSTNAME = socket.gethostname()
STARTED_AT = time.time()
IST = timezone(timedelta(hours=5, minutes=30))  # hospital's local time, whatever the server's TZ


def now_ist():
    return datetime.now(IST)

SLOTS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
         "14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]
BOOKING_WINDOW_DAYS = 90

DOCTORS = [
    ("Dr. Ananya Rao", "General Medicine"),
    ("Dr. Vikram Mehta", "Cardiology"),
    ("Dr. Sneha Kulkarni", "Paediatrics"),
    ("Dr. Arjun Nair", "Orthopaedics"),
    ("Dr. Priya Sharma", "Dermatology"),
    ("Dr. Rahul Verma", "ENT"),
]

if USE_PG:
    import psycopg

app = Flask(__name__)

# ---------------------------------------------------------------- logging
# One JSON line per request on stdout; Docker ships stdout to CloudWatch Logs.
log = logging.getLogger("medicloud")
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_h)
log.setLevel(logging.INFO)


def log_json(**fields):
    fields["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    fields["host"] = HOSTNAME
    log.info(json.dumps(fields))


# ---------------------------------------------------------------- database
def connect():
    if USE_PG:
        return psycopg.connect(DATABASE_URL, connect_timeout=3)
    conn = sqlite3.connect(SQLITE_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def q(sql, args=(), one=False, commit=False):
    """Run SQL written with '?' placeholders on either database."""
    if USE_PG:
        sql = sql.replace("?", "%s")
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else []
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
    return (rows[0] if rows else None) if one else rows


def is_unique_violation(exc):
    if USE_PG:
        return isinstance(exc, psycopg.errors.UniqueViolation)
    return isinstance(exc, sqlite3.IntegrityError)


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS doctors (
        id {pk},
        name TEXT NOT NULL,
        specialty TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS appointments (
        id {pk},
        doctor_id INTEGER NOT NULL REFERENCES doctors(id),
        patient_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        appt_date TEXT NOT NULL,
        slot TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'booked',
        created_at TEXT NOT NULL)""",
    # A slot can only be booked once per doctor (cancelled rows don't count).
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_active_slot
        ON appointments (doctor_id, appt_date, slot) WHERE status = 'booked'""",
    "CREATE INDEX IF NOT EXISTS ix_appt_phone ON appointments (phone)",
]

_schema_lock = threading.Lock()
_schema_ready = False


def ensure_schema(retries=1, delay=2.0):
    """Create tables and seed doctors. Retries so the app survives a slow DB start."""
    global _schema_ready
    if _schema_ready:
        return True
    with _schema_lock:
        if _schema_ready:
            return True
        pk = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
        for attempt in range(retries):
            try:
                conn = connect()
                try:
                    cur = conn.cursor()
                    for stmt in SCHEMA:
                        cur.execute(stmt.format(pk=pk))
                    cur.execute("SELECT COUNT(*) FROM doctors")
                    if cur.fetchone()[0] == 0:
                        ph = "%s" if USE_PG else "?"
                        for name, spec in DOCTORS:
                            cur.execute(f"INSERT INTO doctors (name, specialty) VALUES ({ph}, {ph})", (name, spec))
                    conn.commit()
                finally:
                    conn.close()
                _schema_ready = True
                log_json(level="info", event="schema_ready", backend="postgres" if USE_PG else "sqlite")
                return True
            except Exception as exc:  # DB not up yet
                log_json(level="warn", event="schema_retry", attempt=attempt + 1, error=str(exc)[:200])
                time.sleep(delay)
        return False


# ---------------------------------------------------------------- request logging
@app.before_request
def _start_timer():
    g.t0 = time.perf_counter()


@app.after_request
def _log_request(resp):
    ms = round((time.perf_counter() - g.get("t0", time.perf_counter())) * 1000, 1)
    resp.headers["X-Served-By"] = HOSTNAME
    resp.headers["X-Response-Time-ms"] = str(ms)
    # Skip successful health checks so the probe doesn't flood the logs.
    if not (request.path == "/health" and resp.status_code == 200):
        log_json(level="error" if resp.status_code >= 500 else "info", method=request.method,
                 path=request.path, status=resp.status_code, ms=ms,
                 ip=request.headers.get("X-Forwarded-For", request.remote_addr))
    return resp


@app.errorhandler(Exception)
def _unhandled(exc):
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        return jsonify(error=exc.description), exc.code
    log_json(level="error", event="unhandled_exception", error=repr(exc)[:300], path=request.path)
    return jsonify(error="Service temporarily unavailable. Please retry."), 503


# ---------------------------------------------------------------- pages
@app.get("/")
def index():
    return render_template("index.html", version=APP_VERSION, host=HOSTNAME)


@app.get("/health")
def health():
    """Liveness + DB check. Used by Docker, CloudWatch probes and the uptime probe."""
    body = {"status": "ok", "version": APP_VERSION, "host": HOSTNAME,
            "uptime_s": int(time.time() - STARTED_AT), "db": "ok"}
    try:
        if not ensure_schema():
            raise RuntimeError("schema not ready")
        q("SELECT 1")
    except Exception as exc:
        body.update(status="degraded", db="unavailable", error=str(exc)[:120])
        return jsonify(body), 503
    return jsonify(body)


# ---------------------------------------------------------------- API
PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def clean_phone(value):
    """'+91 98765-43210', '098765 43210' -> '9876543210'."""
    p = re.sub(r"[\s-]", "", str(value or ""))
    return re.sub(r"^(\+?91|0)(?=\d{10}$)", "", p)


def _require_schema():
    if not ensure_schema():
        raise RuntimeError("database unavailable")


@app.get("/api/doctors")
def doctors():
    _require_schema()
    rows = q("SELECT id, name, specialty FROM doctors ORDER BY id")
    return jsonify([{"id": r[0], "name": r[1], "specialty": r[2]} for r in rows])


def _parse_date(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


@app.get("/api/slots")
def slots():
    _require_schema()
    doctor_id = request.args.get("doctor_id", type=int)
    d = _parse_date(request.args.get("date"))
    if not doctor_id or not d:
        return jsonify(error="doctor_id and date (YYYY-MM-DD) are required"), 400
    taken = {r[0] for r in q("SELECT slot FROM appointments WHERE doctor_id = ? AND appt_date = ? AND status = 'booked'",
                             (doctor_id, d.isoformat()))}
    now = now_ist()
    out = []
    for s in SLOTS:
        past = d == now.date() and s <= now.strftime("%H:%M")
        out.append({"slot": s, "available": s not in taken and not past and d >= now.date()})
    return jsonify(out)


@app.post("/api/appointments")
def book():
    _require_schema()
    data = request.get_json(silent=True) or {}
    name = str(data.get("patient_name", "")).strip()
    phone = clean_phone(data.get("phone"))
    slot = str(data.get("slot", "")).strip()
    d = _parse_date(data.get("date"))
    try:
        doctor_id = int(data.get("doctor_id"))
    except (TypeError, ValueError):
        doctor_id = None

    errors = []
    if not 2 <= len(name) <= 80:
        errors.append("Patient name must be 2–80 characters.")
    if not PHONE_RE.match(phone):
        errors.append("Phone must be a 10-digit Indian mobile number.")
    if slot not in SLOTS:
        errors.append("Choose a valid time slot.")
    today = now_ist().date()
    if not d or d < today or d > today + timedelta(days=BOOKING_WINDOW_DAYS):
        errors.append(f"Date must be between today and {BOOKING_WINDOW_DAYS} days ahead.")
    if not doctor_id or not q("SELECT id FROM doctors WHERE id = ?", (doctor_id,), one=True):
        errors.append("Choose a valid doctor.")
    if errors:
        return jsonify(error=" ".join(errors)), 400

    try:
        row = q("""INSERT INTO appointments (doctor_id, patient_name, phone, appt_date, slot, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'booked', ?) RETURNING id""",
                (doctor_id, name, phone, d.isoformat(), slot,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")), one=True, commit=True)
    except Exception as exc:
        if is_unique_violation(exc):
            return jsonify(error="That slot was just booked by someone else. Please pick another."), 409
        raise
    log_json(level="info", event="appointment_booked", appointment_id=row[0], doctor_id=doctor_id)
    return jsonify(id=row[0], doctor_id=doctor_id, date=d.isoformat(), slot=slot, status="booked"), 201


@app.get("/api/appointments")
def list_appointments():
    """?phone=... for a patient's bookings, or ?doctor_id=...&date=... for a doctor's day."""
    _require_schema()
    phone = clean_phone(request.args.get("phone"))
    doctor_id = request.args.get("doctor_id", type=int)
    d = _parse_date(request.args.get("date"))
    base = """SELECT a.id, a.patient_name, a.phone, a.appt_date, a.slot, a.status, d.name, d.specialty
              FROM appointments a JOIN doctors d ON d.id = a.doctor_id"""
    if phone:
        rows = q(base + " WHERE a.phone = ? ORDER BY a.appt_date DESC, a.slot LIMIT 50", (phone,))
    elif doctor_id and d:
        rows = q(base + " WHERE a.doctor_id = ? AND a.appt_date = ? AND a.status = 'booked' ORDER BY a.slot",
                 (doctor_id, d.isoformat()))
    else:
        return jsonify(error="Pass phone, or doctor_id and date"), 400
    keys = ["id", "patient_name", "phone", "date", "slot", "status", "doctor", "specialty"]
    out = [dict(zip(keys, r)) for r in rows]
    if not phone:  # doctor view: mask phone numbers
        for o in out:
            o["phone"] = o["phone"][:2] + "******" + o["phone"][-2:]
    return jsonify(out)


@app.post("/api/appointments/<int:appt_id>/cancel")
def cancel(appt_id):
    _require_schema()
    data = request.get_json(silent=True) or {}
    phone = clean_phone(data.get("phone"))
    row = q("SELECT status FROM appointments WHERE id = ? AND phone = ?", (appt_id, phone), one=True)
    if not row:
        return jsonify(error="Appointment not found for this phone number."), 404
    if row[0] != "booked":
        return jsonify(error="Appointment is already cancelled."), 409
    q("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (appt_id,), commit=True)
    log_json(level="info", event="appointment_cancelled", appointment_id=appt_id)
    return jsonify(id=appt_id, status="cancelled")


@app.get("/api/stats")
def stats():
    _require_schema()
    total = q("SELECT COUNT(*) FROM appointments", one=True)[0]
    booked = q("SELECT COUNT(*) FROM appointments WHERE status = 'booked'", one=True)[0]
    today_n = q("SELECT COUNT(*) FROM appointments WHERE status = 'booked' AND appt_date = ?",
                (now_ist().date().isoformat(),), one=True)[0]
    return jsonify(total=total, active=booked, cancelled=total - booked, today=today_n,
                   version=APP_VERSION, host=HOSTNAME, uptime_s=int(time.time() - STARTED_AT))


# In Docker, gunicorn --preload imports this module once in the master process,
# so the schema is created once (waiting up to ~60 s for Postgres) before workers fork.
if os.environ.get("DB_INIT_ON_START") == "1":
    ensure_schema(retries=30)

if __name__ == "__main__":
    ensure_schema(retries=3)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
