"""Extra features for BookBridge: wishlist, seller reviews, admin panel, password recovery."""
import hashlib
import json
import os
import secrets
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

FEATURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS wishlist (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, book_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment     TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (seller_id, reviewer_id)
);
CREATE TABLE IF NOT EXISTS password_resets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL
);
"""

CARD_COLS = """b.id, b.title, b.author, b.category, b.price, b.condition, b.status,
               (b.image IS NOT NULL) AS has_image, u.name AS seller"""


def utc_now_str(delta=timedelta(0)):
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%d %H:%M:%S")


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def local_path_or(default):
    """Only allow redirects to paths on this site."""
    nxt = request.form.get("next", "")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else default


def init(app, get_db, login_required):
    # ---------- create tables / add the is_admin column (safe to run every start) ----------
    with app.app_context():
        db = get_db()
        db.executescript(FEATURE_SCHEMA)
        columns = [r["name"] for r in db.execute("PRAGMA table_info(users)")]
        if "is_admin" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        db.commit()

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if not g.user["is_admin"]:
                abort(403)
            return view(*args, **kwargs)
        return wrapper

    # ---------- helpers usable inside templates ----------
    def in_wishlist(book_id):
        if not g.user:
            return False
        return get_db().execute("SELECT 1 FROM wishlist WHERE user_id = ? AND book_id = ?",
                                (g.user["id"], book_id)).fetchone() is not None

    def seller_rating(seller_id):
        row = get_db().execute("SELECT AVG(rating) AS a, COUNT(*) AS c FROM reviews WHERE seller_id = ?",
                               (seller_id,)).fetchone()
        return {"avg": round(row["a"], 1) if row["a"] else 0, "count": row["c"]}

    app.jinja_env.globals.update(in_wishlist=in_wishlist, seller_rating=seller_rating)

    # =====================================================================  WISHLIST
    @app.route("/wishlist")
    @login_required
    def wishlist():
        books = get_db().execute(
            f"SELECT {CARD_COLS} FROM wishlist w JOIN books b ON b.id = w.book_id "
            "JOIN users u ON u.id = b.user_id WHERE w.user_id = ? ORDER BY w.created_at DESC",
            (g.user["id"],)).fetchall()
        return render_template("wishlist.html", books=books)

    @app.route("/wishlist/<int:book_id>/toggle", methods=["POST"])
    @login_required
    def wishlist_toggle(book_id):
        db = get_db()
        if db.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone() is None:
            abort(404)
        if in_wishlist(book_id):
            db.execute("DELETE FROM wishlist WHERE user_id = ? AND book_id = ?", (g.user["id"], book_id))
            flash("Removed from your wishlist.", "info")
        else:
            db.execute("INSERT INTO wishlist (user_id, book_id) VALUES (?, ?)", (g.user["id"], book_id))
            flash("Saved to your wishlist.", "success")
        db.commit()
        return redirect(local_path_or(url_for("book_detail", book_id=book_id)))

    # =====================================================================  REVIEWS
    def have_chatted(a, b):
        return get_db().execute(
            "SELECT 1 FROM messages WHERE (sender_id = ? AND receiver_id = ?) "
            "OR (sender_id = ? AND receiver_id = ?) LIMIT 1", (a, b, b, a)).fetchone() is not None

    @app.route("/seller/<int:seller_id>")
    def seller_page(seller_id):
        db = get_db()
        seller = db.execute("SELECT id, name, created_at FROM users WHERE id = ?", (seller_id,)).fetchone()
        if seller is None:
            abort(404)
        books = db.execute(
            f"SELECT {CARD_COLS} FROM books b JOIN users u ON u.id = b.user_id "
            "WHERE b.user_id = ? AND b.status = 'available' ORDER BY b.id DESC", (seller_id,)).fetchall()
        reviews = db.execute(
            "SELECT r.id, r.rating, r.comment, r.created_at, r.reviewer_id, u.name AS reviewer "
            "FROM reviews r JOIN users u ON u.id = r.reviewer_id WHERE r.seller_id = ? ORDER BY r.id DESC",
            (seller_id,)).fetchall()
        can_review, my_review = False, None
        if g.user and g.user["id"] != seller_id:
            can_review = have_chatted(g.user["id"], seller_id)
            my_review = db.execute("SELECT rating, comment FROM reviews WHERE seller_id = ? AND reviewer_id = ?",
                                   (seller_id, g.user["id"])).fetchone()
        return render_template("seller.html", seller=seller, books=books, reviews=reviews,
                               rating=seller_rating(seller_id), can_review=can_review, my_review=my_review)

    @app.route("/seller/<int:seller_id>/review", methods=["POST"])
    @login_required
    def review_submit(seller_id):
        db = get_db()
        back = redirect(url_for("seller_page", seller_id=seller_id))
        if db.execute("SELECT 1 FROM users WHERE id = ?", (seller_id,)).fetchone() is None:
            abort(404)
        if seller_id == g.user["id"]:
            flash("You cannot review yourself.", "danger")
            return back
        if not have_chatted(g.user["id"], seller_id):
            flash("You can review a seller only after chatting with them.", "warning")
            return back
        try:
            rating = int(request.form.get("rating", ""))
        except ValueError:
            rating = 0
        if not 1 <= rating <= 5:
            flash("Please choose a rating from 1 to 5.", "danger")
            return back
        comment = request.form.get("comment", "").strip()[:500]
        db.execute(
            "INSERT INTO reviews (seller_id, reviewer_id, rating, comment) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(seller_id, reviewer_id) DO UPDATE SET rating = excluded.rating, "
            "comment = excluded.comment, created_at = CURRENT_TIMESTAMP",
            (seller_id, g.user["id"], rating, comment))
        db.commit()
        flash("Thank you! Your review has been saved.", "success")
        return back

    @app.route("/reviews/<int:review_id>/delete", methods=["POST"])
    @login_required
    def review_delete(review_id):
        db = get_db()
        review = db.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
        if review is None:
            abort(404)
        if review["reviewer_id"] != g.user["id"] and not g.user["is_admin"]:
            abort(403)
        db.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        db.commit()
        flash("Review deleted.", "success")
        return redirect(url_for("seller_page", seller_id=review["seller_id"]))

    # =====================================================================  ADMIN PANEL
    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        db = get_db()
        count = lambda sql: db.execute(sql).fetchone()[0]
        stats = {
            "users": count("SELECT COUNT(*) FROM users"),
            "books": count("SELECT COUNT(*) FROM books"),
            "available": count("SELECT COUNT(*) FROM books WHERE status = 'available'"),
            "sold": count("SELECT COUNT(*) FROM books WHERE status = 'sold'"),
            "messages": count("SELECT COUNT(*) FROM messages"),
            "reviews": count("SELECT COUNT(*) FROM reviews"),
        }
        recent_users = db.execute("SELECT id, name, email, created_at FROM users ORDER BY id DESC LIMIT 5").fetchall()
        recent_books = db.execute(
            "SELECT b.id, b.title, b.price, u.name AS seller FROM books b JOIN users u ON u.id = b.user_id "
            "ORDER BY b.id DESC LIMIT 5").fetchall()
        return render_template("admin.html", stats=stats, recent_users=recent_users, recent_books=recent_books)

    @app.route("/admin/users")
    @admin_required
    def admin_users():
        q = request.args.get("q", "").strip()
        users = get_db().execute(
            "SELECT u.id, u.name, u.email, u.is_admin, u.created_at, "
            "(SELECT COUNT(*) FROM books b WHERE b.user_id = u.id) AS books "
            "FROM users u WHERE u.name LIKE ? OR u.email LIKE ? ORDER BY u.id DESC",
            (f"%{q}%", f"%{q}%")).fetchall()
        return render_template("admin_users.html", users=users, q=q)

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @admin_required
    def admin_user_delete(user_id):
        db = get_db()
        target = db.execute("SELECT id, name, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            abort(404)
        if target["id"] == g.user["id"] or target["is_admin"]:
            flash("Admin accounts cannot be deleted here.", "danger")
        else:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))   # their books/messages are removed too
            db.commit()
            flash(f"User '{target['name']}' and all their data were deleted.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/books")
    @admin_required
    def admin_books():
        q = request.args.get("q", "").strip()
        books = get_db().execute(
            "SELECT b.id, b.title, b.author, b.price, b.status, u.name AS seller "
            "FROM books b JOIN users u ON u.id = b.user_id "
            "WHERE b.title LIKE ? OR b.author LIKE ? OR u.name LIKE ? ORDER BY b.id DESC",
            (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
        return render_template("admin_books.html", books=books, q=q)

    @app.route("/admin/books/<int:book_id>/delete", methods=["POST"])
    @admin_required
    def admin_book_delete(book_id):
        db = get_db()
        if db.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone() is None:
            abort(404)
        db.execute("DELETE FROM books WHERE id = ?", (book_id,))
        db.commit()
        flash("Listing removed.", "success")
        return redirect(url_for("admin_books"))

    # =====================================================================  PASSWORD RECOVERY
    def send_email(to, subject, html):
        """Sends through Brevo's HTTPS API. Returns False if not configured or if it fails."""
        key, sender = os.environ.get("BREVO_API_KEY"), os.environ.get("MAIL_SENDER")
        if not (key and sender):
            return False
        payload = json.dumps({"sender": {"name": "BookBridge", "email": sender},
                              "to": [{"email": to}], "subject": subject, "htmlContent": html}).encode()
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email", data=payload, method="POST",
            headers={"api-key": key, "content-type": "application/json", "accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except Exception as exc:
            app.logger.error("Email sending failed: %s", exc)
            return False

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if g.user:
            return redirect(url_for("profile"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            db = get_db()
            row = db.execute("SELECT id, name FROM users WHERE email = ?", (email,)).fetchone()
            demo_link = None
            if row:
                token = secrets.token_urlsafe(32)
                db.execute("DELETE FROM password_resets WHERE user_id = ?", (row["id"],))
                db.execute("INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
                           (row["id"], hash_token(token), utc_now_str(timedelta(hours=1))))
                db.commit()
                link = url_for("reset_password", token=token, _external=True)
                html = (f"<p>Hi {row['name']},</p><p>Click the link below to choose a new BookBridge password. "
                        f"It is valid for 1 hour.</p><p><a href='{link}'>Reset my password</a></p>"
                        "<p>If you did not ask for this, you can ignore this email.</p>")
                if not send_email(email, "Reset your BookBridge password", html):
                    app.logger.warning("Password reset link for %s: %s", email, link)
                    if os.environ.get("SHOW_RESET_LINK") == "1" or app.debug:
                        demo_link = link          # demo mode only (no email service configured)
            if demo_link:
                return render_template("forgot_password.html", demo_link=demo_link)
            flash("If that email is registered, a password reset link has been sent.", "info")
            return redirect(url_for("login"))
        return render_template("forgot_password.html", demo_link=None)

    def valid_reset(token):
        return get_db().execute(
            "SELECT * FROM password_resets WHERE token_hash = ? AND expires_at > ?",
            (hash_token(token), utc_now_str())).fetchone()

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        row = valid_reset(token)
        if row is None:
            flash("This reset link is invalid or has expired. Please request a new one.", "danger")
            return redirect(url_for("forgot_password"))
        error = None
        if request.method == "POST":
            password = request.form.get("password", "")
            if len(password) < 6:
                error = "Password must be at least 6 characters."
            elif password != request.form.get("confirm", ""):
                error = "Passwords do not match."
            else:
                db = get_db()
                db.execute("UPDATE users SET password = ? WHERE id = ?",
                           (generate_password_hash(password), row["user_id"]))
                db.execute("DELETE FROM password_resets WHERE user_id = ?", (row["user_id"],))
                db.commit()
                flash("Password updated. You can log in now.", "success")
                return redirect(url_for("login"))
        return render_template("reset_password.html", error=error), (400 if error else 200)