import os
import re
import sqlite3
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "bookbridge.db"))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,           # max upload size: 3 MB
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # https only on Render
    PERMANENT_SESSION_LIFETIME=7 * 24 * 3600,
)

CATEGORIES = ["Academic", "Fiction", "Non-Fiction", "Competitive Exams",
              "Comics", "Children", "Technology", "Self-Help", "Other"]
CONDITIONS = ["Like New", "Good", "Fair", "Old but readable"]
PER_PAGE = 12
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL UNIQUE,
    phone      TEXT,
    password   TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    author      TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       INTEGER NOT NULL,
    condition   TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'available',
    image       BLOB,
    image_type  TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    sender_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body        TEXT NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

CARD_COLS = """b.id, b.title, b.author, b.category, b.price, b.condition, b.status,
               (b.image IS NOT NULL) AS has_image, u.name AS seller"""


# ------------------------------------------------------------------ database
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


init_db()


# ------------------------------------------------------- user / template data
@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = get_db().execute(
            "SELECT id, name, email, phone FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:
            session.clear()


@app.context_processor
def inject_globals():
    unread = 0
    if g.user:
        unread = get_db().execute(
            "SELECT COUNT(*) FROM messages WHERE receiver_id = ? AND is_read = 0",
            (g.user["id"],)).fetchone()[0]
    return {"user": g.user, "unread": unread}


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def error_page(code, message):
    return render_template("error.html", code=code, message=message), code


@app.errorhandler(404)
def not_found(_e):
    return error_page(404, "Page not found.")


@app.errorhandler(403)
def forbidden(_e):
    return error_page(403, "You can only manage your own listings.")


@app.errorhandler(413)
def too_large(_e):
    return error_page(413, "File too large. Images must be under 2 MB.")


@app.errorhandler(500)
def server_error(_e):
    return error_page(500, "Something went wrong. Please try again.")


# --------------------------------------------------------------------- auth
@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("home"))
    form, error = {}, None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        form = {"name": name, "email": email, "phone": phone}
        db = get_db()

        if len(name) < 2:
            error = "Please enter your full name."
        elif not EMAIL_RE.match(email):
            error = "Please enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != request.form.get("confirm", ""):
            error = "Passwords do not match."
        elif db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            error = "An account with this email already exists."

        if not error:
            cur = db.execute(
                "INSERT INTO users (name, email, phone, password) VALUES (?, ?, ?, ?)",
                (name, email, phone, generate_password_hash(password)))
            db.commit()
            session.clear()
            session["user_id"] = cur.lastrowid
            session.permanent = True
            flash(f"Welcome to BookBridge, {name}!", "success")
            return redirect(url_for("home"))
    return render_template("register.html", form=form, error=error), (400 if error else 200)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    form, error = {}, None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        form = {"email": email}
        row = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None or not check_password_hash(row["password"], request.form.get("password", "")):
            error = "Invalid email or password."
        else:
            session.clear()
            session["user_id"] = row["id"]
            session.permanent = True
            flash(f"Welcome back, {row['name']}!", "success")
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):   # local redirects only
                return redirect(nxt)
            return redirect(url_for("home"))
    return render_template("login.html", form=form, error=error), (401 if error else 200)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


# ------------------------------------------------------------------ profile
@app.route("/profile")
@login_required
def profile():
    db = get_db()
    listed = db.execute("SELECT COUNT(*) FROM books WHERE user_id = ?", (g.user["id"],)).fetchone()[0]
    sold = db.execute("SELECT COUNT(*) FROM books WHERE user_id = ? AND status = 'sold'",
                      (g.user["id"],)).fetchone()[0]
    joined = db.execute("SELECT created_at FROM users WHERE id = ?", (g.user["id"],)).fetchone()[0]
    return render_template("profile.html", listed=listed, sold=sold, joined=joined)


@app.route("/profile", methods=["POST"])
@login_required
def profile_update():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    if len(name) < 2:
        flash("Name must be at least 2 characters.", "danger")
    else:
        db = get_db()
        db.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (name, phone, g.user["id"]))
        db.commit()
        flash("Profile updated.", "success")
    return redirect(url_for("profile"))


@app.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    db = get_db()
    row = db.execute("SELECT password FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    new, confirm = request.form.get("password", ""), request.form.get("confirm", "")
    if not check_password_hash(row["password"], request.form.get("current", "")):
        flash("Current password is incorrect.", "danger")
    elif len(new) < 6:
        flash("New password must be at least 6 characters.", "danger")
    elif new != confirm:
        flash("New passwords do not match.", "danger")
    else:
        db.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new), g.user["id"]))
        db.commit()
        flash("Password changed successfully.", "success")
    return redirect(url_for("profile"))


# -------------------------------------------------------------------- books
def detect_image(data):
    """Return the MIME type by checking the file's first bytes (not just the filename)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_book_form():
    """Validate the add/edit form. Returns (book_dict, image_tuple_or_None, error)."""
    f = request.form
    try:
        price = int(f.get("price", ""))
    except ValueError:
        price = None
    book = {
        "title": f.get("title", "").strip(), "author": f.get("author", "").strip(),
        "category": f.get("category", ""), "condition": f.get("condition", ""),
        "price": price if price is not None else "",
        "description": f.get("description", "").strip()[:1000],
    }
    image, error = None, None
    upload = request.files.get("image")
    if upload and upload.filename:
        data = upload.read()
        mime = detect_image(data)
        if mime is None or len(data) > 2 * 1024 * 1024:
            error = "Image must be a JPG, PNG or WEBP file under 2 MB."
        else:
            image = (data, mime)

    if error:
        pass
    elif not book["title"] or not book["author"]:
        error = "Title and author are required."
    elif book["category"] not in CATEGORIES:
        error = "Please choose a category."
    elif book["condition"] not in CONDITIONS:
        error = "Please choose the book condition."
    elif price is None or price < 0 or price > 100000:
        error = "Please enter a valid price."
    return book, image, error


BLANK_BOOK = {"title": "", "author": "", "category": "", "price": "", "condition": "", "description": ""}


@app.route("/")
def home():
    db = get_db()
    latest = db.execute(
        f"SELECT {CARD_COLS} FROM books b JOIN users u ON u.id = b.user_id "
        "WHERE b.status = 'available' ORDER BY b.id DESC LIMIT 8").fetchall()
    total_books = db.execute("SELECT COUNT(*) FROM books WHERE status = 'available'").fetchone()[0]
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return render_template("index.html", latest=latest, total_books=total_books,
                           total_users=total_users, categories=CATEGORIES)


@app.route("/books")
def browse():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    sort = request.args.get("sort", "newest")
    page = max(request.args.get("page", 1, type=int), 1)
    pmin = request.args.get("min", type=int)
    pmax = request.args.get("max", type=int)

    where, params = ["b.status = 'available'"], []
    if q:
        where.append("(b.title LIKE ? OR b.author LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if category in CATEGORIES:
        where.append("b.category = ?")
        params.append(category)
    if pmin is not None:
        where.append("b.price >= ?")
        params.append(pmin)
    if pmax is not None:
        where.append("b.price <= ?")
        params.append(pmax)

    order = {"newest": "b.id DESC", "low": "b.price ASC",
             "high": "b.price DESC", "title": "b.title ASC"}.get(sort, "b.id DESC")
    where_sql = " WHERE " + " AND ".join(where)

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM books b{where_sql}", params).fetchone()[0]
    books = db.execute(
        f"SELECT {CARD_COLS} FROM books b JOIN users u ON u.id = b.user_id"
        f"{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()

    query = {"q": q, "category": category, "min": "" if pmin is None else pmin,
             "max": "" if pmax is None else pmax, "sort": sort}
    link_args = {k: v for k, v in query.items() if v != ""}
    pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
    return render_template("books.html", books=books, total=total, query=query,
                           link_args=link_args, page=page, pages=pages, categories=CATEGORIES)


@app.route("/books/new", methods=["GET", "POST"])
@login_required
def book_new():
    book, error = BLANK_BOOK, None
    if request.method == "POST":
        book, image, error = read_book_form()
        if not error:
            db = get_db()
            cur = db.execute(
                "INSERT INTO books (user_id, title, author, category, price, condition, description, image, image_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (g.user["id"], book["title"], book["author"], book["category"], book["price"],
                 book["condition"], book["description"],
                 image[0] if image else None, image[1] if image else None))
            db.commit()
            flash("Your book has been listed!", "success")
            return redirect(url_for("book_detail", book_id=cur.lastrowid))
    return render_template("book_form.html", book=book, error=error, categories=CATEGORIES,
                           conditions=CONDITIONS), (400 if error else 200)


@app.route("/my-books")
@login_required
def my_books():
    books = get_db().execute(
        f"SELECT {CARD_COLS} FROM books b JOIN users u ON u.id = b.user_id "
        "WHERE b.user_id = ? ORDER BY b.id DESC", (g.user["id"],)).fetchall()
    return render_template("my_books.html", books=books)


@app.route("/books/<int:book_id>/image")
def book_image(book_id):
    row = get_db().execute("SELECT image, image_type FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None or row["image"] is None:
        abort(404)
    return Response(row["image"], mimetype=row["image_type"],
                    headers={"Cache-Control": "public, max-age=300"})


@app.route("/books/<int:book_id>")
def book_detail(book_id):
    db = get_db()
    book = db.execute(
        "SELECT b.*, (b.image IS NOT NULL) AS has_image, u.name AS seller "
        "FROM books b JOIN users u ON u.id = b.user_id WHERE b.id = ?", (book_id,)).fetchone()
    if book is None:
        abort(404)
    similar = db.execute(
        f"SELECT {CARD_COLS} FROM books b JOIN users u ON u.id = b.user_id "
        "WHERE b.category = ? AND b.id != ? AND b.status = 'available' ORDER BY b.id DESC LIMIT 4",
        (book["category"], book_id)).fetchall()
    return render_template("book_detail.html", book=book, similar=similar)


def own_book(book_id):
    book = get_db().execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        abort(404)
    if book["user_id"] != g.user["id"]:
        abort(403)
    return book


@app.route("/books/<int:book_id>/edit", methods=["GET", "POST"])
@login_required
def book_edit(book_id):
    current = own_book(book_id)
    book, error = dict(current), None
    if request.method == "POST":
        book, image, error = read_book_form()
        book["id"] = book_id
        if not error:
            db = get_db()
            db.execute("UPDATE books SET title=?, author=?, category=?, price=?, condition=?, description=? WHERE id=?",
                       (book["title"], book["author"], book["category"], book["price"],
                        book["condition"], book["description"], book_id))
            if image:
                db.execute("UPDATE books SET image=?, image_type=? WHERE id=?", (image[0], image[1], book_id))
            db.commit()
            flash("Listing updated.", "success")
            return redirect(url_for("book_detail", book_id=book_id))
    return render_template("book_form.html", book=book, error=error, categories=CATEGORIES,
                           conditions=CONDITIONS), (400 if error else 200)


@app.route("/books/<int:book_id>/status", methods=["POST"])
@login_required
def book_status(book_id):
    book = own_book(book_id)
    new_status = "sold" if book["status"] == "available" else "available"
    db = get_db()
    db.execute("UPDATE books SET status = ? WHERE id = ?", (new_status, book_id))
    db.commit()
    flash(f"Marked as {new_status}.", "success")
    return redirect(url_for("my_books"))


@app.route("/books/<int:book_id>/delete", methods=["POST"])
@login_required
def book_delete(book_id):
    own_book(book_id)
    db = get_db()
    db.execute("DELETE FROM books WHERE id = ?", (book_id,))
    db.commit()
    flash("Listing deleted.", "success")
    return redirect(url_for("my_books"))


# --------------------------------------------------------------------- chat
@app.route("/chat")
@login_required
def chat_inbox():
    rows = get_db().execute("""
        SELECT m.book_id, m.body, m.created_at, m.sender_id, b.title,
               u.id AS other_id, u.name AS other_name,
               (SELECT COUNT(*) FROM messages x WHERE x.book_id = m.book_id
                  AND x.sender_id = u.id AND x.receiver_id = :me AND x.is_read = 0) AS unread
        FROM messages m
        JOIN books b ON b.id = m.book_id
        JOIN users u ON u.id = CASE WHEN m.sender_id = :me THEN m.receiver_id ELSE m.sender_id END
        WHERE m.id IN (
            SELECT MAX(id) FROM messages WHERE sender_id = :me OR receiver_id = :me
            GROUP BY book_id, CASE WHEN sender_id = :me THEN receiver_id ELSE sender_id END)
        ORDER BY m.id DESC""", {"me": g.user["id"]}).fetchall()
    return render_template("chat_inbox.html", conversations=rows)


def get_conversation(book_id, other_id):
    """A chat is only allowed between a user and the seller of that book."""
    db = get_db()
    book = db.execute("SELECT id, title, price, user_id FROM books WHERE id = ?", (book_id,)).fetchone()
    other = db.execute("SELECT id, name FROM users WHERE id = ?", (other_id,)).fetchone()
    if book is None or other is None or other["id"] == g.user["id"]:
        abort(404)
    if g.user["id"] != book["user_id"] and other["id"] != book["user_id"]:
        abort(404)
    return book, other


def fetch_messages(book_id, other_id, after=0):
    me = g.user["id"]
    db = get_db()
    rows = db.execute(
        "SELECT id, sender_id, body, created_at FROM messages WHERE book_id = ? AND id > ? "
        "AND ((sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)) ORDER BY id",
        (book_id, after, me, other_id, other_id, me)).fetchall()
    if rows:
        db.execute("UPDATE messages SET is_read = 1 WHERE book_id = ? AND sender_id = ? AND receiver_id = ?",
                   (book_id, other_id, me))
        db.commit()
    return rows


@app.route("/chat/<int:book_id>/<int:other_id>")
@login_required
def chat_room(book_id, other_id):
    book, other = get_conversation(book_id, other_id)
    messages = fetch_messages(book_id, other_id)
    return render_template("chat_room.html", book=book, other=other, messages=messages)


@app.route("/chat/<int:book_id>/<int:other_id>/messages")
@login_required
def chat_messages(book_id, other_id):
    get_conversation(book_id, other_id)
    after = request.args.get("after", 0, type=int)
    return jsonify([dict(r) for r in fetch_messages(book_id, other_id, after)])


@app.route("/chat/<int:book_id>/<int:other_id>/send", methods=["POST"])
@login_required
def chat_send(book_id, other_id):
    get_conversation(book_id, other_id)
    data = request.get_json(silent=True) or {}
    body = str(data.get("body", "")).strip()[:1000]
    if not body:
        return jsonify({"error": "Message is empty"}), 400
    db = get_db()
    cur = db.execute("INSERT INTO messages (book_id, sender_id, receiver_id, body) VALUES (?, ?, ?, ?)",
                     (book_id, g.user["id"], other_id, body))
    db.commit()
    row = db.execute("SELECT id, sender_id, body, created_at FROM messages WHERE id = ?",
                     (cur.lastrowid,)).fetchone()
    return jsonify(dict(row))


if __name__ == "__main__":
    app.run(debug=True)