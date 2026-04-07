"""
The Grand Table — Flask Backend
Run: python app.py
Requires: pip install flask flask-cors mysql-connector-python python-dotenv qrcode[pil]
"""

from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix   # ← FIX 1: Render proxy
import mysql.connector
import hashlib
import os
import uuid
import json
import urllib.request as _urllib_req
import io
import base64
import qrcode
import qrcode.image.svg
from datetime import datetime
from functools import wraps
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="../frontend", static_url_path="")

# ── FIX 2: Trust Render's proxy so HTTPS is detected correctly ──
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.secret_key = os.environ.get("SECRET_KEY", "grand-table-secret-2026")

# ── FIX 3: Secure cookie config for HTTPS (Render) ──
IS_PRODUCTION = os.environ.get("RENDER", "") != ""   # Render sets this automatically

app.config["SESSION_COOKIE_SAMESITE"] = "None" if IS_PRODUCTION else "Lax"
app.config["SESSION_COOKIE_SECURE"]   = IS_PRODUCTION      # True on Render (HTTPS)
app.config["SESSION_COOKIE_HTTPONLY"] = True

# ── FIX 4: CORS — allow both localhost AND the live Render URL ──
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")   # auto-set by Render

ALLOWED_ORIGINS = [
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
]
if RENDER_URL:
    ALLOWED_ORIGINS.append(RENDER_URL)
    ALLOWED_ORIGINS.append(RENDER_URL.replace("http://", "https://"))

# Also allow any custom domain set via env var
CUSTOM_DOMAIN = os.environ.get("CUSTOM_DOMAIN", "")
if CUSTOM_DOMAIN:
    ALLOWED_ORIGINS.append(f"https://{CUSTOM_DOMAIN}")

CORS(app,
     supports_credentials=True,
     origins=ALLOWED_ORIGINS)

# ─────────────────────────────────────────────
# DATABASE CONFIG
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     int(os.environ.get("DB_PORT", 3306)),
    "user":     os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", "Chandra@1"),
    "database": os.environ.get("DB_NAME", "grand_table"),
    "autocommit": False,
    "connection_timeout": 10,
}

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────
# UPI CONFIG
# ─────────────────────────────────────────────
UPI_ID         = os.environ.get("UPI_ID", "yourupiid@upi")
UPI_PAYEE_NAME = os.environ.get("UPI_PAYEE_NAME", "The Grand Table")
UPI_CURRENCY   = "INR"


def get_db():
    return mysql.connector.connect(**DB_CONFIG)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"success": False, "error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def tier_from_points(points: int) -> str:
    if points >= 5000: return "Platinum"
    if points >= 2000: return "Gold"
    if points >= 500:  return "Silver"
    return "Bronze"


def ok(data=None, **kwargs):
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload)


def err(message, code=400):
    return jsonify({"success": False, "error": message}), code


# ─────────────────────────────────────────────
# SCHEMA & DB INIT
# ─────────────────────────────────────────────
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        email         VARCHAR(255) UNIQUE NOT NULL,
        first_name    VARCHAR(100) NOT NULL,
        last_name     VARCHAR(100) DEFAULT '',
        phone         VARCHAR(50)  DEFAULT '',
        room          VARCHAR(50)  DEFAULT '',
        password_hash VARCHAR(64)  NOT NULL,
        dietary       VARCHAR(100) DEFAULT 'None',
        allergies     TEXT,
        points        INT          DEFAULT 500,
        tier          VARCHAR(20)  DEFAULT 'Bronze',
        created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cart_items (
        id        INT AUTO_INCREMENT PRIMARY KEY,
        user_id   INT NOT NULL,
        dish_id   INT NOT NULL,
        qty       INT DEFAULT 1,
        notes     TEXT,
        UNIQUE KEY uq_user_dish (user_id, dish_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wishlist_items (
        id        INT AUTO_INCREMENT PRIMARY KEY,
        user_id   INT NOT NULL,
        dish_id   INT NOT NULL,
        UNIQUE KEY uq_user_wish (user_id, dish_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        order_ref      VARCHAR(20)    NOT NULL,
        user_id        INT            NOT NULL,
        total          DECIMAL(10,2)  NOT NULL,
        status         VARCHAR(30)    DEFAULT 'placed',
        payment_method VARCHAR(30)    DEFAULT 'card',
        notes          TEXT,
        eta            VARCHAR(50)    DEFAULT '25-35 minutes',
        created_at     DATETIME       DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        id        INT AUTO_INCREMENT PRIMARY KEY,
        order_id  INT            NOT NULL,
        dish_id   INT            NOT NULL,
        dish_name VARCHAR(255)   DEFAULT '',
        dish_icon VARCHAR(10)    DEFAULT '🍽️',
        qty       INT            NOT NULL,
        price     DECIMAL(10,2)  NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS addresses (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT         NOT NULL,
        label      VARCHAR(100),
        address    TEXT,
        icon       VARCHAR(10) DEFAULT '📍',
        is_primary TINYINT(1)  DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS preferences (
        user_id      INT PRIMARY KEY,
        winepairing  TINYINT(1) DEFAULT 1,
        dietary      TINYINT(1) DEFAULT 1,
        music        TINYINT(1) DEFAULT 0,
        chefs_table  TINYINT(1) DEFAULT 1,
        express      TINYINT(1) DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        user_id   INT         NOT NULL,
        notif_key VARCHAR(50) NOT NULL,
        enabled   TINYINT(1)  DEFAULT 1,
        PRIMARY KEY (user_id, notif_key),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS upi_payments (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        order_ref    VARCHAR(20)   NOT NULL,
        user_id      INT           NOT NULL,
        amount       DECIMAL(10,2) NOT NULL,
        upi_txn_ref  VARCHAR(100)  DEFAULT '',
        status       VARCHAR(20)   DEFAULT 'pending',
        created_at   DATETIME      DEFAULT CURRENT_TIMESTAMP,
        confirmed_at DATETIME,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
]


def init_db():
    try:
        conn = get_db()
        cur  = conn.cursor()
        for stmt in SCHEMA:
            cur.execute(stmt.strip())
        conn.commit()
        cur.close()
        conn.close()
        print("✅  Database schema ready.")
    except Exception as e:
        print(f"❌  DB init error: {e}")
        raise


# ─────────────────────────────────────────────
# INTERNAL USER HELPERS
# ─────────────────────────────────────────────
def _get_user(cur, user_id):
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cur.description]
    return _clean_user(dict(zip(cols, row)))


def _clean_user(user: dict) -> dict:
    user.pop("password_hash", None)
    if isinstance(user.get("created_at"), datetime):
        user["created_at"] = user["created_at"].isoformat()
    return user


# ─────────────────────────────────────────────
# AUTH  /api/auth/*
# ─────────────────────────────────────────────
@app.route("/api/auth/register", methods=["POST"])
def register():
    d = request.get_json(force=True) or {}
    for field in ("email", "password", "first_name"):
        if not d.get(field, "").strip():
            return err(f"Field '{field}' is required.")

    email = d["email"].strip().lower()
    pw    = d["password"]
    if len(pw) < 6:
        return err("Password must be at least 6 characters.")

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return err("An account with this email already exists.")

        cur.execute(
            """INSERT INTO users
               (email, first_name, last_name, phone, room, password_hash, dietary, allergies)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                email,
                d["first_name"].strip(),
                d.get("last_name", "").strip(),
                d.get("phone", ""),
                d.get("room", ""),
                hash_password(pw),
                d.get("dietary", "None"),
                d.get("allergies", ""),
            ),
        )
        user_id = cur.lastrowid

        if d.get("room"):
            cur.execute(
                "INSERT INTO addresses (user_id, label, address, icon, is_primary) VALUES (%s,%s,%s,%s,1)",
                (user_id, "Hotel Room", f"Room {d['room']}, The Grand Hotel", "🏨"),
            )

        cur.execute("INSERT INTO preferences (user_id) VALUES (%s)", (user_id,))

        default_notifs = {
            "order_status": 1, "exclusive_offers": 1,
            "chef_table": 1, "loyalty": 1, "new_menu": 0,
        }
        for key, enabled in default_notifs.items():
            cur.execute(
                "INSERT INTO notifications (user_id, notif_key, enabled) VALUES (%s,%s,%s)",
                (user_id, key, enabled),
            )

        conn.commit()
        session["user_id"] = user_id
        return ok(_get_user(cur, user_id), message="Account created! Welcome to The Grand Table.")
    except mysql.connector.IntegrityError:
        return err("An account with this email already exists.")
    finally:
        cur.close(); conn.close()


@app.route("/api/auth/login", methods=["POST"])
def login():
    d     = request.get_json(force=True) or {}
    email = d.get("email", "").strip().lower()
    pw    = d.get("password", "")
    if not email or not pw:
        return err("Email and password are required.")

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if not row:
            return err("Invalid email or password.", 401)
        cols = [c[0] for c in cur.description]
        user = dict(zip(cols, row))
        if user["password_hash"] != hash_password(pw):
            return err("Invalid email or password.", 401)
        session["user_id"] = user["id"]
        return ok(_clean_user(user))
    finally:
        cur.close(); conn.close()


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return ok(message="Logged out.")


@app.route("/api/auth/me", methods=["GET"])
@login_required
def me():
    conn = get_db()
    cur  = conn.cursor()
    try:
        user = _get_user(cur, session["user_id"])
        if not user:
            session.clear()
            return err("User not found.", 404)
        return ok(user)
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# PROFILE  /api/profile
# ─────────────────────────────────────────────
@app.route("/api/profile", methods=["PUT"])
@login_required
def update_profile():
    d   = request.get_json(force=True) or {}
    uid = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        fields, vals = [], []
        for col in ("first_name", "last_name", "phone", "room", "dietary", "allergies"):
            if col in d:
                fields.append(f"{col}=%s")
                vals.append(d[col])
        if d.get("new_password"):
            if len(d["new_password"]) < 6:
                return err("Password must be at least 6 characters.")
            fields.append("password_hash=%s")
            vals.append(hash_password(d["new_password"]))
        if fields:
            vals.append(uid)
            cur.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=%s", vals)
            conn.commit()
        return ok(_get_user(cur, uid), message="Profile updated.")
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# CART  /api/cart/*
# ─────────────────────────────────────────────
@app.route("/api/cart", methods=["GET"])
@login_required
def get_cart():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT dish_id, qty, notes FROM cart_items WHERE user_id=%s", (uid,))
        rows = cur.fetchall()
        return ok([{"dish_id": r[0], "qty": r[1], "notes": r[2]} for r in rows])
    finally:
        cur.close(); conn.close()


@app.route("/api/cart/add", methods=["POST"])
@login_required
def cart_add():
    d       = request.get_json(force=True) or {}
    uid     = session["user_id"]
    dish_id = d.get("dish_id")
    qty     = int(d.get("qty", 1))
    notes   = d.get("notes", "")
    if not dish_id:
        return err("dish_id is required.")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO cart_items (user_id, dish_id, qty, notes)
               VALUES (%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE qty = qty + %s""",
            (uid, dish_id, qty, notes, qty),
        )
        conn.commit()
        return ok(message="Added to cart.")
    finally:
        cur.close(); conn.close()


@app.route("/api/cart/update", methods=["PUT"])
@login_required
def cart_update():
    d       = request.get_json(force=True) or {}
    uid     = session["user_id"]
    dish_id = d.get("dish_id")
    qty     = int(d.get("qty", 1))
    if not dish_id:
        return err("dish_id is required.")
    conn = get_db()
    cur  = conn.cursor()
    try:
        if qty <= 0:
            cur.execute("DELETE FROM cart_items WHERE user_id=%s AND dish_id=%s", (uid, dish_id))
        else:
            cur.execute(
                "UPDATE cart_items SET qty=%s WHERE user_id=%s AND dish_id=%s",
                (qty, uid, dish_id),
            )
        conn.commit()
        return ok(message="Cart updated.")
    finally:
        cur.close(); conn.close()


@app.route("/api/cart/remove/<int:dish_id>", methods=["DELETE"])
@login_required
def cart_remove(dish_id):
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("DELETE FROM cart_items WHERE user_id=%s AND dish_id=%s", (uid, dish_id))
        conn.commit()
        return ok(message="Removed from cart.")
    finally:
        cur.close(); conn.close()


@app.route("/api/cart/clear", methods=["DELETE"])
@login_required
def cart_clear():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("DELETE FROM cart_items WHERE user_id=%s", (uid,))
        conn.commit()
        return ok(message="Cart cleared.")
    finally:
        cur.close(); conn.close()


@app.route("/api/cart/sync", methods=["POST"])
@login_required
def cart_sync():
    d     = request.get_json(force=True) or {}
    uid   = session["user_id"]
    items = d.get("items", [])
    conn  = get_db()
    cur   = conn.cursor()
    try:
        cur.execute("DELETE FROM cart_items WHERE user_id=%s", (uid,))
        for item in items:
            cur.execute(
                "INSERT INTO cart_items (user_id, dish_id, qty, notes) VALUES (%s,%s,%s,%s)",
                (uid, item["dish_id"], item.get("qty", 1), item.get("notes", "")),
            )
        conn.commit()
        return ok(message="Cart synced.")
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# PROMO CODES  /api/promo/validate
# ─────────────────────────────────────────────
PROMO_CODES = {
    "GRANDVIP":  {"discount": 0.15, "free_service": False, "label": "15% VIP Discount"},
    "WELCOME10": {"discount": 0.10, "free_service": False, "label": "10% Welcome Discount"},
    "ROOM512":   {"discount": 0.20, "free_service": False, "label": "20% Room Service Discount"},
    "FREESHIP":  {"discount": 0.00, "free_service": True,  "label": "Free Service Fee"},
}


@app.route("/api/promo/validate", methods=["POST"])
@login_required
def validate_promo():
    code = (request.get_json(force=True) or {}).get("code", "").upper().strip()
    if code in PROMO_CODES:
        return ok({"code": code, **PROMO_CODES[code]})
    return err("Invalid promo code.", 404)


# ─────────────────────────────────────────────
# WISHLIST  /api/wishlist/*
# ─────────────────────────────────────────────
@app.route("/api/wishlist", methods=["GET"])
@login_required
def get_wishlist():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT dish_id FROM wishlist_items WHERE user_id=%s", (uid,))
        return ok([r[0] for r in cur.fetchall()])
    finally:
        cur.close(); conn.close()


@app.route("/api/wishlist/toggle", methods=["POST"])
@login_required
def wishlist_toggle():
    dish_id = (request.get_json(force=True) or {}).get("dish_id")
    if not dish_id:
        return err("dish_id is required.")
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM wishlist_items WHERE user_id=%s AND dish_id=%s", (uid, dish_id)
        )
        if cur.fetchone():
            cur.execute(
                "DELETE FROM wishlist_items WHERE user_id=%s AND dish_id=%s", (uid, dish_id)
            )
            added = False
        else:
            cur.execute(
                "INSERT INTO wishlist_items (user_id, dish_id) VALUES (%s,%s)", (uid, dish_id)
            )
            added = True
        conn.commit()
        return ok({"added": added})
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# ORDERS  /api/orders/*
# ─────────────────────────────────────────────
@app.route("/api/orders", methods=["GET"])
@login_required
def get_orders():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC", (uid,)
        )
        orders = cur.fetchall()
        for o in orders:
            if isinstance(o.get("created_at"), datetime):
                o["created_at"] = o["created_at"].isoformat()
            o["total"] = float(o["total"])
            cur.execute("SELECT * FROM order_items WHERE order_id=%s", (o["id"],))
            items = cur.fetchall()
            for item in items:
                item["price"] = float(item["price"])
            o["items"] = items
        return ok(orders)
    finally:
        cur.close(); conn.close()


@app.route("/api/orders/place", methods=["POST"])
@login_required
def place_order():
    d     = request.get_json(force=True) or {}
    uid   = session["user_id"]
    items = d.get("items", [])
    if not items:
        return err("Order must contain at least one item.")

    total          = float(d.get("total", 0))
    payment_method = d.get("payment_method", "card")
    notes          = d.get("notes", "")
    eta            = d.get("eta", "25–35 minutes")
    order_ref      = "GT-" + str(uuid.uuid4())[:8].upper()

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO orders (order_ref, user_id, total, status, payment_method, notes, eta)
               VALUES (%s,%s,%s,'placed',%s,%s,%s)""",
            (order_ref, uid, total, payment_method, notes, eta),
        )
        order_id = cur.lastrowid

        for item in items:
            cur.execute(
                """INSERT INTO order_items
                   (order_id, dish_id, dish_name, dish_icon, qty, price)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    order_id,
                    item["dish_id"],
                    item.get("name", ""),
                    item.get("icon", "🍽️"),
                    item["qty"],
                    float(item["price"]),
                ),
            )

        points_earned = int(total)
        cur.execute(
            "UPDATE users SET points = points + %s WHERE id=%s", (points_earned, uid)
        )
        cur.execute("SELECT points FROM users WHERE id=%s", (uid,))
        new_points = cur.fetchone()[0]
        new_tier   = tier_from_points(new_points)
        cur.execute("UPDATE users SET tier=%s WHERE id=%s", (new_tier, uid))
        cur.execute("DELETE FROM cart_items WHERE user_id=%s", (uid,))
        conn.commit()

        return ok(
            {
                "order_ref":     order_ref,
                "order_id":      order_id,
                "points_earned": points_earned,
                "new_points":    new_points,
                "new_tier":      new_tier,
            },
            message="Order placed successfully!",
        )
    finally:
        cur.close(); conn.close()


@app.route("/api/orders/<int:order_id>/status", methods=["GET"])
@login_required
def order_status_get(order_id):
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT status, eta FROM orders WHERE id=%s AND user_id=%s", (order_id, uid)
        )
        row = cur.fetchone()
        if not row:
            return err("Order not found.", 404)
        return ok({"status": row[0], "eta": row[1]})
    finally:
        cur.close(); conn.close()


@app.route("/api/orders/<int:order_id>/status", methods=["PUT"])
@login_required
def order_status_update(order_id):
    new_status = (request.get_json(force=True) or {}).get("status")
    valid = ["placed", "confirmed", "preparing", "on-the-way", "delivered", "cancelled"]
    if new_status not in valid:
        return err(f"Invalid status. Must be one of: {', '.join(valid)}")
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            "UPDATE orders SET status=%s WHERE id=%s AND user_id=%s",
            (new_status, order_id, uid),
        )
        conn.commit()
        if cur.rowcount == 0:
            return err("Order not found.", 404)
        return ok({"status": new_status})
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# ADDRESSES  /api/addresses/*
# ─────────────────────────────────────────────
@app.route("/api/addresses", methods=["GET"])
@login_required
def get_addresses():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM addresses WHERE user_id=%s ORDER BY is_primary DESC", (uid,))
        return ok(cur.fetchall())
    finally:
        cur.close(); conn.close()


@app.route("/api/addresses", methods=["POST"])
@login_required
def add_address():
    d       = request.get_json(force=True) or {}
    uid     = session["user_id"]
    label   = d.get("label", "").strip()
    address = d.get("address", "").strip()
    if not label or not address:
        return err("Label and address are required.")
    icon = d.get("icon", "📍")
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM addresses WHERE user_id=%s", (uid,))
        count   = cur.fetchone()[0]
        primary = 1 if count == 0 else 0
        cur.execute(
            "INSERT INTO addresses (user_id, label, address, icon, is_primary) VALUES (%s,%s,%s,%s,%s)",
            (uid, label, address, icon, primary),
        )
        conn.commit()
        return ok({"id": cur.lastrowid}, message="Address saved.")
    finally:
        cur.close(); conn.close()


@app.route("/api/addresses/<int:addr_id>", methods=["DELETE"])
@login_required
def delete_address(addr_id):
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("DELETE FROM addresses WHERE id=%s AND user_id=%s", (addr_id, uid))
        conn.commit()
        cur.execute(
            "SELECT id FROM addresses WHERE user_id=%s ORDER BY id LIMIT 1", (uid,)
        )
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE addresses SET is_primary=1 WHERE id=%s", (row[0],))
            conn.commit()
        return ok(message="Address removed.")
    finally:
        cur.close(); conn.close()


@app.route("/api/addresses/<int:addr_id>/primary", methods=["PUT"])
@login_required
def set_primary_address(addr_id):
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("UPDATE addresses SET is_primary=0 WHERE user_id=%s", (uid,))
        cur.execute(
            "UPDATE addresses SET is_primary=1 WHERE id=%s AND user_id=%s", (addr_id, uid)
        )
        conn.commit()
        return ok(message="Primary address updated.")
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# PREFERENCES  /api/preferences
# ─────────────────────────────────────────────
@app.route("/api/preferences", methods=["GET"])
@login_required
def get_preferences():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM preferences WHERE user_id=%s", (uid,))
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT IGNORE INTO preferences (user_id) VALUES (%s)", (uid,))
            conn.commit()
            cur.execute("SELECT * FROM preferences WHERE user_id=%s", (uid,))
            row = cur.fetchone()
        row.pop("user_id", None)
        return ok(row)
    finally:
        cur.close(); conn.close()


@app.route("/api/preferences", methods=["PUT"])
@login_required
def update_preferences():
    d    = request.get_json(force=True) or {}
    uid  = session["user_id"]
    allowed = ("winepairing", "dietary", "music", "chefs_table", "express")
    conn = get_db()
    cur  = conn.cursor()
    try:
        fields, vals = [], []
        for k in allowed:
            if k in d:
                fields.append(f"{k}=%s")
                vals.append(1 if d[k] else 0)
        if fields:
            vals.append(uid)
            cur.execute(f"UPDATE preferences SET {', '.join(fields)} WHERE user_id=%s", vals)
            conn.commit()
        return ok(message="Preferences updated.")
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# NOTIFICATIONS  /api/notifications
# ─────────────────────────────────────────────
@app.route("/api/notifications", methods=["GET"])
@login_required
def get_notifications():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT notif_key, enabled FROM notifications WHERE user_id=%s", (uid,)
        )
        rows = cur.fetchall()
        return ok({r["notif_key"]: bool(r["enabled"]) for r in rows})
    finally:
        cur.close(); conn.close()


@app.route("/api/notifications/<key>", methods=["PUT"])
@login_required
def update_notification(key):
    uid     = session["user_id"]
    enabled = (request.get_json(force=True) or {}).get("enabled", True)
    val     = 1 if enabled else 0
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO notifications (user_id, notif_key, enabled)
               VALUES (%s,%s,%s)
               ON DUPLICATE KEY UPDATE enabled=%s""",
            (uid, key, val, val),
        )
        conn.commit()
        return ok(message="Notification setting updated.")
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# LOYALTY  /api/loyalty
# ─────────────────────────────────────────────
@app.route("/api/loyalty", methods=["GET"])
@login_required
def get_loyalty():
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT points, tier FROM users WHERE id=%s", (uid,))
        row = cur.fetchone()
        if not row:
            return err("User not found.", 404)
        points, tier = row

        cur.execute(
            """SELECT order_ref, total, created_at FROM orders
               WHERE user_id=%s AND status='delivered'
               ORDER BY created_at DESC""",
            (uid,),
        )
        history = [
            {
                "ref":    r[0],
                "points": int(r[1]),
                "date":   r[2].isoformat() if isinstance(r[2], datetime) else str(r[2]),
            }
            for r in cur.fetchall()
        ]
        return ok({"points": points, "tier": tier, "history": history})
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# AI CONCIERGE  /api/ai/chat
# ─────────────────────────────────────────────
@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    if not ANTHROPIC_API_KEY:
        return err(
            "AI concierge not configured. Add ANTHROPIC_API_KEY to your .env file.", 503
        )

    d        = request.get_json(force=True) or {}
    messages = d.get("messages", [])
    system   = d.get("system", "You are a helpful concierge.")

    if not messages:
        return err("messages is required.")

    payload = json.dumps({
        "model":      "claude-sonnet-4-20250514",
        "max_tokens": 512,
        "system":     system,
        "messages":   messages,
    }).encode("utf-8")

    req = _urllib_req.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with _urllib_req.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            text   = result.get("content", [{}])[0].get("text", "")
            return ok({"reply": text})
    except _urllib_req.HTTPError as e:
        body = e.read().decode("utf-8")
        return err(f"Anthropic error: {body}", e.code)
    except Exception as e:
        return err(f"AI request failed: {str(e)}", 500)


# ─────────────────────────────────────────────
# UPI PAYMENTS  /api/payment/upi/*
# ─────────────────────────────────────────────

def _build_upi_uri(amount: float, order_ref: str) -> str:
    import urllib.parse
    params = urllib.parse.urlencode({
        "pa": UPI_ID,
        "pn": UPI_PAYEE_NAME,
        "am": f"{amount:.2f}",
        "cu": UPI_CURRENCY,
        "tn": f"Order {order_ref} - The Grand Table",
        "tr": order_ref,
    })
    return f"upi://pay?{params}"


def _generate_qr_png_b64(data: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@app.route("/api/payment/upi/qr", methods=["POST"])
@login_required
def upi_generate_qr():
    d         = request.get_json(force=True) or {}
    amount    = float(d.get("amount", 0))
    order_ref = d.get("order_ref", "").strip()
    uid       = session["user_id"]

    if amount <= 0:
        return err("amount must be greater than 0.")
    if not order_ref:
        return err("order_ref is required.")

    upi_uri = _build_upi_uri(amount, order_ref)
    qr_b64  = _generate_qr_png_b64(upi_uri)

    conn = get_db()
    cur  = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO upi_payments (order_ref, user_id, amount, status)
               VALUES (%s, %s, %s, 'pending')""",
            (order_ref, uid, amount),
        )
        conn.commit()
        payment_id = cur.lastrowid
    finally:
        cur.close(); conn.close()

    return ok({
        "qr_image_b64": qr_b64,
        "upi_uri":      upi_uri,
        "upi_id":       UPI_ID,
        "amount":       amount,
        "order_ref":    order_ref,
        "payment_id":   payment_id,
    })


@app.route("/api/payment/upi/confirm", methods=["POST"])
@login_required
def upi_confirm_payment():
    d          = request.get_json(force=True) or {}
    payment_id = d.get("payment_id")
    txn_ref    = d.get("upi_txn_ref", "").strip()
    uid        = session["user_id"]

    if not payment_id:
        return err("payment_id is required.")

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM upi_payments WHERE id=%s AND user_id=%s",
            (payment_id, uid),
        )
        row = cur.fetchone()
        if not row:
            return err("Payment record not found.", 404)
        if row["status"] in ("confirmed", "verified"):
            return ok({"status": row["status"]}, message="Payment already confirmed.")

        cur.execute(
            """UPDATE upi_payments
               SET status='confirmed', upi_txn_ref=%s, confirmed_at=NOW()
               WHERE id=%s""",
            (txn_ref or "", payment_id),
        )
        cur.execute(
            "UPDATE orders SET status='confirmed', payment_method='upi' WHERE order_ref=%s AND user_id=%s",
            (row["order_ref"], uid),
        )
        conn.commit()

        return ok({
            "status":      "confirmed",
            "order_ref":   row["order_ref"],
            "amount":      float(row["amount"]),
            "upi_txn_ref": txn_ref or None,
        }, message="Payment confirmed! Your order is being prepared.")
    finally:
        cur.close(); conn.close()


@app.route("/api/payment/upi/status/<int:payment_id>", methods=["GET"])
@login_required
def upi_payment_status(payment_id):
    uid  = session["user_id"]
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, order_ref, amount, status, upi_txn_ref, created_at, confirmed_at "
            "FROM upi_payments WHERE id=%s AND user_id=%s",
            (payment_id, uid),
        )
        row = cur.fetchone()
        if not row:
            return err("Payment not found.", 404)
        row["amount"] = float(row["amount"])
        for f in ("created_at", "confirmed_at"):
            if isinstance(row.get(f), datetime):
                row[f] = row[f].isoformat()
        return ok(row)
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# DEBUG — /api/debug/session  (remove after confirming it works)
# ─────────────────────────────────────────────
@app.route("/api/debug/session", methods=["GET"])
def debug_session():
    return jsonify({
        "session_keys": list(session.keys()),
        "user_id":      session.get("user_id"),
        "is_production": IS_PRODUCTION,
        "secure_cookie": app.config.get("SESSION_COOKIE_SECURE"),
        "samesite":     app.config.get("SESSION_COOKIE_SAMESITE"),
        "render_url":   RENDER_URL,
    })


# ─────────────────────────────────────────────
# HEALTH CHECK  /api/health
# ─────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    try:
        conn = get_db()
        conn.ping(reconnect=True)
        conn.close()
        return ok({"db": "connected", "status": "healthy"})
    except Exception as e:
        return jsonify({"success": False, "db": "error", "detail": str(e)}), 500


# ─────────────────────────────────────────────
# SERVE FRONTEND
# ─────────────────────────────────────────────
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    frontend_dir = os.path.abspath(frontend_dir)
    if path and os.path.exists(os.path.join(frontend_dir, path)):
        return send_from_directory(frontend_dir, path)
    return send_from_directory(frontend_dir, "index.html")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    print(f"🚀  Grand Table backend running on http://localhost:{port}")
    print(f"🍽️   Open http://localhost:{port} in your browser")
    print(f"🔒  Production mode: {IS_PRODUCTION}")
    app.run(debug=debug, host="0.0.0.0", port=port)