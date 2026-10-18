"""Adds demo data. Safe to run many times (it only adds what is missing).
Demo logins:  asha@example.com / rohan@example.com  -> password123
Admin login:  admin@bookbridge.com -> password from ADMIN_PASSWORD (default: admin123)"""
import os

from werkzeug.security import generate_password_hash

from app import connect, init_db

init_db()
conn = connect()

if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
    pw = generate_password_hash("password123")
    add_user = "INSERT INTO users (name, email, phone, password) VALUES (?, ?, ?, ?)"
    asha = conn.execute(add_user, ("Asha Verma", "asha@example.com", "9876500001", pw)).lastrowid
    rohan = conn.execute(add_user, ("Rohan Mehta", "rohan@example.com", "9876500002", pw)).lastrowid

    books = [
        (asha, "Python Programming", "Reema Thareja", "Technology", 450, "Good", "Clean copy, no markings."),
        (asha, "Data Structures Using C", "Reema Thareja", "Academic", 380, "Like New", "Used for one semester only."),
        (asha, "The Alchemist", "Paulo Coelho", "Fiction", 150, "Good", "Popular novel, slight wear on cover."),
        (rohan, "Atomic Habits", "James Clear", "Self-Help", 260, "Like New", "Read once."),
        (rohan, "Wings of Fire", "A. P. J. Abdul Kalam", "Non-Fiction", 120, "Fair", "Some highlighting inside."),
        (rohan, "Quantitative Aptitude", "R. S. Aggarwal", "Competitive Exams", 300, "Good", "Latest edition, all pages intact."),
        (rohan, "Operating System Concepts", "Silberschatz", "Academic", 520, "Old but readable", "Older edition, still useful."),
        (asha, "Harry Potter and the Philosopher's Stone", "J. K. Rowling", "Children", 220, "Good", "Paperback."),
    ]
    conn.executemany(
        "INSERT INTO books (user_id, title, author, category, price, condition, description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", books)

    conn.executemany(
        "INSERT INTO reviews (seller_id, reviewer_id, rating, comment) VALUES (?, ?, ?, ?)",
        [(asha, rohan, 5, "Book was exactly as described. Smooth deal!"),
         (rohan, asha, 4, "Good condition and quick replies.")])
    conn.commit()
    print("Demo data added.  Login: asha@example.com / password123")
else:
    print("Demo data already exists - skipped.")

if conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] == 0:
    admin_pw = os.environ.get("ADMIN_PASSWORD", "admin123")
    conn.execute("INSERT INTO users (name, email, phone, password, is_admin) VALUES (?, ?, ?, ?, 1)",
                 ("Site Admin", "admin@bookbridge.com", "", generate_password_hash(admin_pw)))
    conn.commit()
    print("Admin created.  Login: admin@bookbridge.com")
conn.close()