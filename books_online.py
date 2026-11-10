"""My Books - online search with covers, reading dates + statistics, backups, CSV import.
Install:  pip install PySide6
Run:      python books_online.py

Tip: for the best Persian text, put Vazirmatn-Regular.ttf and Vazirmatn-Bold.ttf in a folder named
'fonts' next to this file (or install the free Vazirmatn font); the app uses it if present.
"""
import calendar
import csv
import difflib
import functools
import hashlib
import html
import io
import ipaddress
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QEvent, QIODevice, QObject, QPoint, QPointF,
                            QPropertyAnimation, QRectF, QRunnable, QSize, Qt,
                            QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontDatabase, QFontMetrics, QIcon, QKeySequence,
                           QLinearGradient, QPainter, QPainterPath, QPen,
                           QImage, QImageReader, QPixmap, QShortcut,
                           QTextLayout, QTextOption)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QStyle, QStyledItemDelegate, QTextEdit,
                               QVBoxLayout, QWidget)

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QGraphicsOpacityEffect, QMenu, QProgressBar, QScrollArea,
                               QSizePolicy)

APP_VERSION = "0.2.0"                # one place to change it for a release (also shown in Data & backup)
DB = os.environ.get("BOOKS_DB", "books.db")
COVERS_DIR = Path(DB).resolve().parent / "covers"
BACKUP_DIR = Path(DB).resolve().parent / "backups"
SETTINGS_FILE = Path(DB).resolve().parent / "settings.json"


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def user_agent(identify=False):
    # Open Library asks apps to identify themselves (name + contact email); identified clients get
    # higher rate limits. The e-mail is sent ONLY to Open Library (identify=True), to nobody else.
    email = load_settings().get("contact_email", "").strip() if identify else ""
    return f"MyBooksApp/1.0 ({email})" if email else "MyBooksApp/1.0"
STATUSES = ["Read", "Reading", "Want to read"]
WISH = "Want to buy"                # the wishlist: books to buy in the future (own table)
COVER_H = 180                        # height of the cover area on a card
CARD_W, CARD_H = 190, COVER_H + 150
# chip colours on the cards (white text on each; they sit on top of the cover, so they do not depend on the theme)
STATUS_COLORS = {"Read": "#23915f", "Reading": "#3b6fe0", "Want to read": "#c2710c", WISH: "#a855c7"}
SORTS = {
    "Newest first": "id DESC",
    "Oldest first": "id ASC",
    "Title A–Z": "title COLLATE NOCASE ASC",
    "Title Z–A": "title COLLATE NOCASE DESC",
    "Top rated": "COALESCE(rating,0) DESC, id DESC",
}

THEMES = {
    "dark": dict(bg="#14151b", side="#1a1b23", card="#20222c", border="#2e3140",
                 text="#ecedf3", muted="#8b8fa3", input="#262833",
                 accent="#7c8cff", star_off="#3a3d4d"),
    "light": dict(bg="#f4f5fa", side="#ffffff", card="#ffffff", border="#e1e3ee",
                  text="#1d2030", muted="#6b7088", input="#eceef6",
                  accent="#5468ff", star_off="#d5d8e6"),
}
T = dict(THEMES["dark"])


# ----------------------------------------------------------------- data
# ---------------------------------------------------------------- dates
# Dates are stored as ISO text (YYYY-MM-DD, Gregorian) and shown in the calendar chosen in Settings.
JMONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
           "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
CAL = {"mode": load_settings().get("calendar", "gregorian")}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_num(value):
    """Digits of a DATE as shown on screen: Persian digits when the Jalali calendar is chosen, plain
    digits otherwise. Only for dates: counts and ratings keep plain digits, and nothing that is
    stored or exported (CSV, database) ever goes through this."""
    text = str(value)
    return text.translate(_FA_DIGITS) if CAL["mode"] == "jalali" else text


def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            - 80 + gd + g_d_m[gm - 1])
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def j2g(jy, jm, jd):
    if jy > 979:
        gy, jy = 1600, jy - 979
    else:
        gy = 621
    days = (365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + 78 + jd
            + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186))
    gy += 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    sal = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal[gm]:
        gd -= sal[gm]
        gm += 1
    return gy, gm, gd


def jalali_month_days(jy, jm):
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if g2j(*j2g(jy, 12, 30)) == (jy, 12, 30) else 29


def month_names():
    if CAL["mode"] == "jalali":
        return JMONTHS
    return [calendar.month_abbr[i] for i in range(1, 13)]


# A stored date is ISO text. An exact date is plain "2026-11-04". When the person only remembers
# the month or the year, the MIDDLE of that period is stored plus a marker: "2019-07-02~y"
# (year only) or "2019-09-15~m" (month + year). No schema change; old databases work as they are.
def split_stored(stored):
    """'2019-07-02~y' -> ('2019-07-02', 'y');  '2026-11-04' -> ('2026-11-04', 'd')."""
    iso, _, mark = (stored or "").strip().partition("~")
    return iso, (mark if mark in ("m", "y") else "d")


def make_stored(iso, prec="d"):
    return f"{iso}~{prec}" if iso and prec in ("m", "y") else iso


def date_prec(stored):
    """'d' exact day, 'm' month + year only, 'y' year only."""
    return split_stored(stored)[1]


def split_date(stored):
    """'2026-11-04' -> (year, month, day) in the chosen calendar."""
    y, m, d = (int(x) for x in split_stored(stored)[0].split("-"))
    return g2j(y, m, d) if CAL["mode"] == "jalali" else (y, m, d)


def year_of(iso):
    return split_date(iso)[0]


def month_of(iso):
    return split_date(iso)[1]


def fmt_date(stored):
    if not stored:
        return ""
    try:
        y, m, d = split_date(stored)
    except ValueError:
        return stored
    prec = date_prec(stored)
    if prec == "y":
        text = f"≈ {y}"
    elif prec == "m":
        text = f"≈ {month_names()[m - 1]} {y}"
    else:
        text = f"{d} {month_names()[m - 1]} {y}"
    if CAL["mode"] == "jalali":
        # the isolate keeps the Persian date in its own right-to-left order inside an English sentence
        return "\u2068" + fa_num(text) + "\u2069"
    return text


def _iso(y, m, d):
    return f"{y:04d}-{m:02d}-{d:02d}"


def date_range(stored):
    """First and last possible day (ISO) of a stored date; a year-only date covers the whole
    year of the chosen calendar."""
    iso, prec = split_stored(stored)
    if prec == "d":
        return iso, iso
    y, m, _ = split_date(iso)
    jal = CAL["mode"] == "jalali"
    m1, m2 = (1, 12) if prec == "y" else (m, m)
    last = jalali_month_days(y, m2) if jal else calendar.monthrange(y, m2)[1]
    lo, hi = (y, m1, 1), (y, m2, last)
    if jal:
        lo, hi = j2g(*lo), j2g(*hi)
    return _iso(*lo), _iso(*hi)


def csv_date(stored):
    """Exact dates stay ISO; approximate ones are written as 2019-09 / 2019 (in the chosen
    calendar) so Excel shows something readable and import understands it again."""
    iso, prec = split_stored(stored)
    if prec == "d" or not iso:
        return iso
    y, m, _ = split_date(stored)
    return f"{y:04d}" if prec == "y" else f"{y:04d}-{m:02d}"


def parse_date(text):
    """Accepts 2026-11-04, 2026/11/04, 1405/08/13 (Jalali), Persian digits..., and also just
    month + year (2019-09, 1398/07) or just a year (2019, 1398) -> stored date or None."""
    text = (text or "").translate(_DIGITS)
    if "~" in text:                                   # our own stored form: 2019-07-02~y
        iso, prec = split_stored(text)
        try:
            date.fromisoformat(iso)
        except ValueError:
            return None
        return make_stored(iso, prec)
    parts = [int(x) for x in re.findall(r"\d+", text)]
    if len(parts) >= 3:
        a, b, c = parts[:3]
        y, m, d = (c, b, a) if a < 100 and c > 1000 else (a, b, c)       # also accept 13-08-1405
        prec = "d"
    elif len(parts) == 2:
        a, b = parts
        y, m = (a, b) if a >= 1000 else (b, a)
        d, prec = 15, "m"
    elif len(parts) == 1 and 1200 <= parts[0] < 2200:
        y, m, d, prec = parts[0], 7, 1, "y"
    else:
        return None
    jal = 1200 <= y < 1700
    if prec == "y" and not jal:
        d = 2                                         # middle of the Gregorian year: 2 July
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    try:
        if jal:
            y, m, d = j2g(y, m, d)
        return make_stored(date(y, m, d).isoformat(), prec)
    except ValueError:
        return None


SOFT_CHECKS = {"read_nodate"}        # hints only; they never block saving


def check_dates(status, started, finished, today=None):
    """Consistency of a book's status and dates -> [(code, message)].
    Pure function (no UI) so it can be tested; started/finished are stored dates or None."""
    today = today or date.today().isoformat()
    s0 = date_range(started) if started else None
    s1 = date_range(finished) if finished else None
    out = []
    if s0 and s1 and s0[0] > s1[1]:        # even the earliest start is after the latest finish
        out.append(("order", "The finish date is before the start date."))
    if (s0 and s0[0] > today) or (s1 and s1[0] > today):
        out.append(("future", "A reading date is in the future."))
    if status == "Want to read" and (started or finished):
        out.append(("want_dates", "A “Want to read” book can’t have reading dates."))
    if status == "Reading" and finished:
        out.append(("reading_finished",
                    "You are still reading this book, but it has a finish date."))
    if status == "Read" and not finished:
        out.append(("read_nodate",
                    "Marked Read without a finish date, so it won’t be in the statistics. "
                    "If you don’t remember exactly, tick “Finished” and choose “Year only”."))
    return out


class Store:
    def __init__(self, path=DB):
        self.con = sqlite3.connect(path)
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS books(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, author TEXT, status TEXT,
                rating INTEGER, notes TEXT)""")
        self.migrate()

    def migrate(self):
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(books)")]
        for name in ("cover", "started", "finished"):      # upgrade older databases
            if name not in cols:
                self.con.execute(f"ALTER TABLE books ADD COLUMN {name} TEXT")
        self.con.execute(                                   # wishlist: a separate table, books is untouched
            """CREATE TABLE IF NOT EXISTS wishlist(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, author TEXT, notes TEXT, cover TEXT)""")
        self.con.commit()

    # -- wishlist (books to buy)
    def wish_add(self, title, author, notes, cover=None):
        cover = Path(cover).name if cover else None
        cur = self.con.execute("INSERT INTO wishlist(title,author,notes,cover) VALUES(?,?,?,?)",
                               (title, author, notes, cover))
        self.con.commit()
        return cur.lastrowid

    def set_cover(self, id_, wish, cover):
        """Attaches a cover that arrived after the book was added. Only fills an EMPTY cover, so it
        can never replace one the person chose in the meantime."""
        table = "wishlist" if wish else "books"
        self.con.execute(f"UPDATE {table} SET cover=? WHERE id=? AND (cover IS NULL OR cover='')",
                         (Path(cover).name, id_))
        self.con.commit()

    def wish_update(self, id_, title, author, notes, cover=None):
        cover = Path(cover).name if cover else None
        self.con.execute("UPDATE wishlist SET title=?,author=?,notes=?,cover=? WHERE id=?",
                         (title, author, notes, cover, id_))
        self.con.commit()

    def restore_row(self, row, wish):
        """Puts a deleted card back (Undo) with its ORIGINAL id and every field.
        row: (id,title,author,status,rating,notes,cover,started,finished) as the card grid has it."""
        _id, t, a, st, r, n, c, d0, d1 = row
        with self.con:
            if wish:
                self.con.execute("INSERT INTO wishlist(id,title,author,notes,cover) VALUES(?,?,?,?,?)",
                                 (_id, t, a, n, c))
            else:
                self.con.execute(
                    "INSERT INTO books(id,title,author,status,rating,notes,cover,started,finished) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", (_id, t, a, st, r, n, c, d0, d1))

    def wish_delete(self, id_):
        self.con.execute("DELETE FROM wishlist WHERE id=?", (id_,))
        self.con.commit()

    def wish_count(self):
        return self.con.execute("SELECT COUNT(*) FROM wishlist").fetchone()[0]

    def cover_of(self, title):
        """File name of the cover a library book or wishlist entry with this title already has (or None)."""
        row = self.con.execute(
            "SELECT cover FROM books WHERE lower(title)=lower(?) AND cover IS NOT NULL AND cover!='' "
            "UNION SELECT cover FROM wishlist WHERE lower(title)=lower(?) AND cover IS NOT NULL AND cover!=''",
            (title.strip(), title.strip())).fetchone()
        return row[0] if row else None

    def wish_exists(self, title, exclude_id=None):
        return self.con.execute(
            "SELECT id FROM wishlist WHERE lower(title)=lower(?) AND id IS NOT ?",
            (title.strip(), exclude_id)).fetchone() is not None

    def wish_search(self, text="", sort="Newest first"):
        """Rows shaped like books rows (id,title,author,status,rating,notes,cover,started,finished),
        so the card grid can show them; status is WISH and there is no rating or dates."""
        order = "id DESC" if "rating" in SORTS[sort] else SORTS[sort]      # no ratings on a wishlist
        rows = self.con.execute(
            "SELECT id,title,author,notes,cover FROM wishlist "
            "WHERE (title LIKE ? OR author LIKE ?) ORDER BY " + order,
            (f"%{text}%", f"%{text}%")).fetchall()
        return [(i, t, a, WISH, None, n, c, None, None) for i, t, a, n, c in rows]

    def wish_bought(self, id_, title, author, notes, cover=None):
        """One transaction: the book enters the library as 'Want to read' and leaves the wishlist."""
        cover = Path(cover).name if cover else None
        with self.con:
            self.con.execute(
                "INSERT INTO books(title,author,status,rating,notes,cover,started,finished) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (title, author, "Want to read", None, notes, cover, None, None))
            self.con.execute("DELETE FROM wishlist WHERE id=?", (id_,))

    def add(self, title, author, status, rating, notes, cover=None, started=None, finished=None):
        cover = Path(cover).name if cover else None         # covers live in the covers/ folder
        cur = self.con.execute(
            "INSERT INTO books(title,author,status,rating,notes,cover,started,finished) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (title, author, status, rating, notes, cover, started, finished))
        self.con.commit()
        return cur.lastrowid

    def update(self, id_, title, author, status, rating, notes, cover=None,
               started=None, finished=None):
        cover = Path(cover).name if cover else None
        self.con.execute(
            "UPDATE books SET title=?,author=?,status=?,rating=?,notes=?,cover=?,"
            "started=?,finished=? WHERE id=?",
            (title, author, status, rating, notes, cover, started, finished, id_))
        self.con.commit()

    def count(self):
        return self.con.execute("SELECT COUNT(*) FROM books").fetchone()[0]

    def finished_rows(self):
        return self.con.execute(
            "SELECT title,author,rating,started,finished FROM books "
            "WHERE finished IS NOT NULL AND finished!=''").fetchall()

    def read_without_date(self):
        return self.con.execute(
            "SELECT COUNT(*) FROM books WHERE status='Read' "
            "AND (finished IS NULL OR finished='')").fetchone()[0]

    def delete(self, id_):
        self.con.execute("DELETE FROM books WHERE id=?", (id_,))
        self.con.commit()

    def exists(self, title, exclude_id=None):
        row = self.con.execute(
            "SELECT id FROM books WHERE lower(title)=lower(?) AND id IS NOT ?",
            (title.strip(), exclude_id)).fetchone()
        return row is not None

    def average_rating(self):
        return self.con.execute("SELECT AVG(rating) FROM books").fetchone()[0]

    def search(self, text="", status="All", sort="Newest first"):
        q = ("SELECT id,title,author,status,rating,notes,cover,started,finished FROM books "
             "WHERE (title LIKE ? OR author LIKE ?)")
        args = [f"%{text}%", f"%{text}%"]
        if status != "All":
            q += " AND status=?"
            args.append(status)
        return self.con.execute(q + " ORDER BY " + SORTS[sort], args).fetchall()

    def counts(self):
        c = {"All": 0, **{s: 0 for s in STATUSES}}
        for status, n in self.con.execute("SELECT status,COUNT(*) FROM books GROUP BY status"):
            c[status] = n
            c["All"] += n
        return c


# ------------------------------------------------------------- online search
# ------------------------------------------------------- backup / restore / CSV
def make_backup(store, force=False, tag=""):
    """Zip of the database + covers in backups/. Automatic: once a day; the newest 10 are kept."""
    if not force and store.count() == 0 and store.wish_count() == 0:
        return None
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S") if force else date.today().isoformat()
    dest = BACKUP_DIR / f"books-{stamp}{tag}.zip"
    if dest.exists():
        return None
    tmp = BACKUP_DIR / "_tmp.db"
    dst = sqlite3.connect(tmp)
    try:
        store.con.backup(dst)                       # consistent copy even while the app is open
    finally:
        dst.close()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "books.db")
        if COVERS_DIR.exists():
            for f in COVERS_DIR.iterdir():
                if f.is_file():
                    z.write(f, f"covers/{f.name}")
    tmp.unlink()
    keep = int(load_settings().get("backup_keep", 10))
    for old in sorted(BACKUP_DIR.glob("books-*.zip"))[:-keep]:
        old.unlink()
    return dest


def restore_from_zip(store, zip_path):
    BACKUP_DIR.mkdir(exist_ok=True)
    tmp = BACKUP_DIR / "_restore.db"
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if "books.db" not in names:
            raise ValueError("This is not a backup made by this app.")
        tmp.write_bytes(z.read("books.db"))
        COVERS_DIR.mkdir(exist_ok=True)
        for n in names:
            if n.startswith("covers/") and not n.endswith("/"):
                target = COVERS_DIR / Path(n).name
                if not target.exists():
                    target.write_bytes(z.read(n))
    src = sqlite3.connect(tmp)
    try:
        src.backup(store.con)                       # replaces everything in the open database
    finally:
        src.close()
        tmp.unlink()
    store.migrate()                                 # older backups lack the newer columns


COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".part"}


def sweep_unused_covers(store):
    """Deletes the files in covers/ that no book and no wishlist entry uses: pictures that online
    search downloaded for results you never added, covers you replaced or removed, covers of deleted
    books, a cover found but then cancelled. A cover that is in the database is NEVER touched.
    Only call it while no dialog is open and no download is running. Returns the number removed."""
    if not COVERS_DIR.is_dir():
        return 0
    try:
        used = {Path(r[0]).name.lower() for r in store.con.execute(
            "SELECT cover FROM books UNION SELECT cover FROM wishlist") if r[0]}
    except sqlite3.Error:
        return 0                                    # cannot tell what is in use: delete nothing
    removed = 0
    for f in COVERS_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in COVER_EXTS and f.name.lower() not in used:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass                                # in use or read-only: leave it, try next time
    return removed


STATUS_MAP = {"read": "Read", "finished": "Read", "reading": "Reading",
              "currently-reading": "Reading", "currently reading": "Reading",
              "want to read": "Want to read", "to-read": "Want to read", "to read": "Want to read",
              "خوانده شده": "Read", "در حال خواندن": "Reading", "می‌خواهم بخوانم": "Want to read"}
ALIASES = dict(
    title=("title", "name", "book", "عنوان", "نام کتاب"),
    author=("author", "authors", "نویسنده"),
    status=("status", "exclusive shelf", "shelf", "وضعیت"),
    rating=("rating", "my rating", "امتیاز"),
    notes=("notes", "note", "my review", "review", "یادداشت"),
    started=("started", "date started", "start", "شروع"),
    finished=("finished", "date finished", "date read", "end", "پایان"))


def read_csv_rows(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Persian Excel files (Windows-1256 only has the Arabic ي and ك: make them Persian ی and ک)
        text = raw.decode("cp1256", errors="replace").replace("ي", "ی").replace("ك", "ک")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text, newline=""), dialect=dialect))


def import_csv_file(store, path):
    """Understands this app's own export, simple Excel lists and Goodreads exports.
    Returns (added, already_in_library, rows_without_title)."""
    def pick(row, field):
        for k, v in row.items():
            if k and k.strip().lower() in ALIASES[field]:
                return (v or "").strip()
        return ""
    added = skipped = bad = 0
    for row in read_csv_rows(path):
        title = pick(row, "title")
        if not title:
            bad += 1
            continue
        if store.exists(title):
            skipped += 1
            continue
        started, finished = parse_date(pick(row, "started")), parse_date(pick(row, "finished"))
        status = STATUS_MAP.get(pick(row, "status").lower()) or ("Read" if finished else "Want to read")
        try:
            rating = int(float(pick(row, "rating").translate(_DIGITS)))
        except ValueError:
            rating = None
        if rating is not None and not 1 <= rating <= 5:
            rating = None
        store.add(title, pick(row, "author"), status, rating, pick(row, "notes"),
                  None, started, finished)
        added += 1
    return added, skipped, bad


class ApiError(Exception):
    pass


MAX_JSON = 5 * 1024 * 1024          # search / book-info answers
MAX_IMAGE = 10 * 1024 * 1024        # cover pictures
MAX_PIXELS = 25_000_000             # a cover bigger than this (e.g. 5000 x 5000) is refused
MAX_SECONDS = 60                    # a download that drips slowly is stopped after this long


def _check_url(url):
    """Only plain HTTPS addresses with a real host name: no http, no user:pass@, no odd ports,
    no IP addresses and no localhost. (No DNS lookup on purpose: VPN/proxy tools that hand out
    fake private addresses would break every request.)"""
    try:
        u = urllib.parse.urlparse(url)
        host, port = (u.hostname or "").lower(), u.port
    except ValueError:
        raise ApiError("blocked address")
    if u.scheme != "https" or u.username or u.password or port not in (None, 443) or not host \
            or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ApiError("blocked address")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    raise ApiError("blocked address")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(urllib.parse.urljoin(req.full_url, newurl))   # a redirect gets the same checks
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _read_limited(r, limit):
    """Reads in pieces and stops when the REAL amount of data passes the limit
    (Content-Length is only a hint: a server can lie)."""
    declared = r.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError("response too large")
    buf, deadline = bytearray(), time.monotonic() + MAX_SECONDS
    while True:
        chunk = r.read(65536)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > limit:
            raise ApiError("response too large")
        if time.monotonic() > deadline:
            raise ApiError("the server answered too slowly")


def http_get(url, timeout=15, retries=1, headers=None, max_bytes=MAX_JSON, identify=False):
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(identify), **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                return _read_limited(r, max_bytes)
        except urllib.error.HTTPError as e:
            if e.code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5)
                continue
            if e.code == 429:
                raise ApiError("too many requests (rate limit)")
            raise ApiError(f"server error {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < retries:
                time.sleep(1.5)
                continue
            raise ApiError("no connection, or the server timed out")


# ---- text helpers: matching "The Stranger" == "the stranger", Persian ی/ي, accents...
ARABIC = re.compile(r"[\u0600-\u06FF]")


def norm(text):
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))     # drop accents / diacritics
    t = t.lower().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    t = " ".join(re.sub(r"[^\w\s]", " ", t).split())
    return re.sub(r"^(the|a|an) ", "", t)


def surname(name):
    name = (name or "").strip()
    if "," in name:
        return norm(name.split(",")[0])
    parts = norm(name).split()
    return parts[-1] if parts else ""


def split_query(q):
    """'the stranger by camus' / 'بیگانه - کامو'  ->  (title, author hint)"""
    parts = re.split(r"\s+by\s+|\s+[-–—]\s+", q.strip(), maxsplit=1, flags=re.I)
    if len(parts) == 2 and all(p.strip() for p in parts):
        return parts[0].strip(), parts[1].strip()
    return q.strip(), ""


# ---- sources
def _ol(params):
    data = json.loads(http_get("https://openlibrary.org/search.json?"
                               + urllib.parse.urlencode(params), identify=True))
    out = []
    for d in data.get("docs", []):
        cid = d.get("cover_i")
        out.append(dict(
            title=d.get("title", ""),
            author=", ".join(d.get("author_name", [])[:3]),
            year=d.get("first_publish_year"), pop=d.get("edition_count") or 0,
            cover_url=f"https://covers.openlibrary.org/b/id/{cid}-L.jpg" if cid else None))
    return out


def search_openlibrary(query, mode, hint="", popular=True):
    base = {"limit": 25, "fields": "title,author_name,first_publish_year,cover_i,edition_count"}
    if mode == "title":
        base["title"] = query
        if hint:
            base["author"] = hint
    else:
        base["author"] = query
    out = _ol(base)                              # best text matches
    if popular:
        try:
            out += _ol(dict(base, sort="editions"))  # plus the most popular matches
        except ApiError:
            pass
    return out


def search_apple(query, mode, hint=""):
    """Apple Books store search: free, no key, fast, good covers."""
    params = {"media": "ebook", "entity": "ebook", "limit": 30}
    if mode == "title" and hint:
        params["term"] = f"{query} {hint}"
    else:
        params["term"] = query
        params["attribute"] = "titleTerm" if mode == "title" else "authorTerm"
    data = json.loads(http_get("https://itunes.apple.com/search?"
                               + urllib.parse.urlencode(params)))
    out = []
    for d in data.get("results", []):
        art = d.get("artworkUrl100")
        if art:
            art = art.replace("100x100bb", "400x400bb")   # larger artwork
        year = (d.get("releaseDate") or "")[:4]
        out.append(dict(title=d.get("trackName", ""), author=d.get("artistName", ""),
                        year=int(year) if year.isdigit() else None, pop=0, cover_url=art))
    return out


def search_google(query, mode, hint=""):
    if mode == "title":
        q = f'intitle:"{query}"' + (f' inauthor:"{hint}"' if hint else "")
    else:
        q = f'inauthor:"{query}"'
    params = {"q": q, "maxResults": 30, "printType": "books"}
    key = load_settings().get("google_api_key", "").strip()
    if key:
        params["key"] = key
    try:
        data = json.loads(http_get("https://www.googleapis.com/books/v1/volumes?"
                                   + urllib.parse.urlencode(params), retries=0))
    except ApiError as e:
        if not key:
            raise ApiError(f"{e}. Google limits searches without an API key; "
                           "add a free one in Settings")
        raise
    out = []
    for it in data.get("items", []):
        v = it.get("volumeInfo", {})
        img = (v.get("imageLinks") or {}).get("thumbnail")
        if img:
            img = img.replace("http://", "https://").replace("&edge=curl", "")
        year = (v.get("publishedDate") or "")[:4]
        out.append(dict(title=v.get("title", ""),
                        author=", ".join((v.get("authors") or [])[:2]),
                        year=int(year) if year.isdigit() else None, pop=0, cover_url=img))
    return out


# ---- Wikidata: knows famous books by their real title/author, in Persian too
NOT_BOOKS = {"Q13442814", "Q191067", "Q18918145"}      # scholarly article, article, journal article


def wd_get(**params):
    params["format"] = "json"
    return json.loads(http_get("https://www.wikidata.org/w/api.php?"
                               + urllib.parse.urlencode(params)))


def _claim_ids(claims, prop):
    out = []
    for c in claims.get(prop, []):
        dv = c.get("mainsnak", {}).get("datavalue")
        if dv and isinstance(dv.get("value"), dict) and "id" in dv["value"]:
            out.append(dv["value"]["id"])
    return out


def _label(ent, *langs):
    for lang in langs:
        v = ent.get("labels", {}).get(lang, {}).get("value")
        if v:
            return v
    return ""


def _year(claims):
    for c in claims.get("P577", []):
        try:
            t = c["mainsnak"]["datavalue"]["value"]["time"]
            if t.startswith("+"):
                return int(t[1:5])
        except (KeyError, ValueError, TypeError):
            pass
    return None


@functools.lru_cache(maxsize=32)
def _wd_entities(query, lang):
    hits = wd_get(action="wbsearchentities", search=query, language=lang,
                  uselang=lang, type="item", limit=10).get("search", [])
    ids = [h["id"] for h in hits]
    if not ids:
        return []
    ents = wd_get(action="wbgetentities", ids="|".join(ids), props="labels|claims",
                  languages=f"{lang}|en").get("entities", {})
    return [ents[i] for i in ids if i in ents]


def _is_book(e):
    claims = e.get("claims", {})
    return bool(_claim_ids(claims, "P50")) and not set(_claim_ids(claims, "P31")) & NOT_BOOKS


def _wd_works_by_author(query, lang):
    """Find the PERSON first, then the works whose author (P50) is that person.
    (Searching works by text would return books that merely have the name in their title.)"""
    people = [e for e in _wd_entities(query, lang) if "Q5" in _claim_ids(e.get("claims", {}), "P31")]
    for person in people[:3]:
        hits = wd_get(action="query", list="search", srnamespace=0, srlimit=50,
                      srsearch=f"haswbstatement:P50={person['id']}").get("query", {}).get("search", [])
        ids = [h["title"] for h in hits]
        if not ids:
            continue
        ents = wd_get(action="wbgetentities", ids="|".join(ids), props="labels|claims",
                      languages=f"{lang}|en").get("entities", {})
        works = [ents[i] for i in ids if i in ents and _is_book(ents[i])]
        if works:
            return works
    return []


def search_wikidata(query, mode, hint=""):
    lang = "fa" if ARABIC.search(query) else "en"
    if mode == "author":
        if lang == "en":
            return []                # Open Library / Apple handle English authors well
        works = _wd_works_by_author(query, lang)
    else:
        works = [e for e in _wd_entities(query, lang) if _is_book(e)]
    if not works:
        return []
    author_ids = list(dict.fromkeys(a for e in works for a in _claim_ids(e["claims"], "P50")[:2]))
    people = wd_get(action="wbgetentities", ids="|".join(author_ids), props="labels",
                    languages=f"{lang}|en").get("entities", {})
    out = []
    for i, e in enumerate(works):
        authors = [people[a] for a in _claim_ids(e["claims"], "P50")[:2] if a in people]
        title_en = _label(e, "en") or _label(e, lang)
        author_en = _label(authors[0], "en") if authors else ""
        out.append(dict(
            title=_label(e, lang, "en"),
            author=", ".join(_label(a, lang, "en") for a in authors),
            year=_year(e["claims"]), pop=1_000_000 - i, cover_url=None,
            alt_core=core_title(title_en) if title_en else "",      # to merge with "The Stranger"
            alt_sn=surnames(author_en),
            cover_query=(title_en, author_en)))      # cover is looked up later
    return out


def wikidata_english_author(query):
    """Persian author name -> English name (so the other sources can find his books)."""
    for e in _wd_entities(query, "fa"):
        if "Q5" in _claim_ids(e.get("claims", {}), "P31"):      # Q5 = human
            return _label(e, "en") or None
    return None


# ---- Taaghche (taaghche.com): Iranian ebook store, ~220k Persian + translated titles, so it has the
# covers of the IRANIAN editions (the Persian publisher's cover, not the English original's).
# It has no documented public API: this uses the same JSON endpoint the website itself uses
# (/v3/search/filters; fields checked against the open-source taaghche-mcp client and its recorded
# responses, NOT against the live server from here).
TG_SEARCH = "https://get.taaghche.com/v3/search/filters"
TG_TRACKING = 110160240          # constant the site's own web app sends with every search
TG_HEADERS = {"Accept": "application/json", "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.5"}


def _taaghche_items(term, pages):
    """Up to `pages` pages (12 books each) of ebook results for `term`."""
    out, offset = [], "null"                         # first page: offset=null, then nextOffset
    for _ in range(pages):
        params = {"term": term, "offset": offset, "trackingData": TG_TRACKING, "type": 1}  # 1 = ebooks
        data = json.loads(http_get(TG_SEARCH + "?" + urllib.parse.urlencode(params),
                                   headers=TG_HEADERS))
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise ApiError("unexpected reply from Taaghche (the site may have changed)")
        tab = data.get("tabItems")
        tab = {} if tab is None else tab
        items = tab.get("items") if isinstance(tab, dict) else None
        items = [] if items is None else items
        if not isinstance(tab, dict) or not isinstance(items, list):
            raise ApiError("unexpected reply from Taaghche (the site may have changed)")
        out += [b for b in items if isinstance(b, dict)]
        offset = str(tab.get("nextOffset") or "")
        if not tab.get("hasMore") or not offset.isdigit():
            break
    return out


def _tg_text(v):
    return v.strip() if isinstance(v, str) else ""


def _tg_cover(u):
    """A Taaghche cover must be an https address on taaghche.com itself."""
    if not isinstance(u, str):
        return ""
    try:
        p = urllib.parse.urlparse(u)
        host = (p.hostname or "").lower()
    except ValueError:
        return ""
    return u if p.scheme == "https" and (host == "taaghche.com" or host.endswith(".taaghche.com")) else ""


def search_taaghche(query, mode, hint="", pages=None):
    out = []
    for i, b in enumerate(_taaghche_items(query, pages or (3 if mode == "author" else 2))):
        cover = _tg_cover(b.get("coverUri"))
        if "/audioCover/" in cover:                  # audiobooks have square covers: skip them
            continue
        people = [a for a in b.get("authors") or [] if isinstance(a, dict)] \
            if isinstance(b.get("authors"), list) else []
        names = [" ".join(x for x in (_tg_text(a.get("firstName")), _tg_text(a.get("lastName"))) if x)
                 for a in people if a.get("type") == 1]                      # type 1 = author
        names = [n for n in dict.fromkeys(names) if n]                       # (2 = translator)
        out.append(dict(title=_tg_text(b.get("title")), author=", ".join(names[:3]),
                        year=None, pop=500_000 - i, cover_url=cover or None,
                        publisher=_tg_text(b.get("publisher"))))
    return out


PUB_GENERIC = {"انتشارات", "نشر", "موسسه", "مجموعه"}       # "Nashr-e Negah" == "Negah"


def publisher_matches(wanted, name):
    """Every word of the wanted publisher occurs in the edition's publisher
    ('نگاه' matches 'انتشارات نگاه'; 'کوله پشتی' matches 'کتاب کوله پشتی')."""
    ww = [w for w in norm(wanted).split() if w not in PUB_GENERIC]
    nw = {w for w in norm(name).split() if w not in PUB_GENERIC}
    return bool(ww) and all(w in nw for w in ww)


PROVIDERS = {"Open Library": search_openlibrary, "Apple Books": search_apple,
             "Wikidata": search_wikidata, "Taaghche": search_taaghche,
             "Google Books": search_google}
SOURCES = ["All sources"] + list(PROVIDERS)


def core_title(t):
    """'1984 (adaptation)' / '1984: A Novel' -> '1984' (so editions of one book match)."""
    return norm(re.split(r"\s*[:\[(–—]\s*|\s+-\s+", t or "", maxsplit=1)[0])


def surnames(author):
    return {surname(a) for a in (author or "").split(",") if a.strip()} - {""}


def _word_eq(a, b):
    """Same word, or a close spelling (Dostoevsky / Dostoyevsky)."""
    return a == b or (min(len(a), len(b)) >= 4
                      and difflib.SequenceMatcher(None, a, b).ratio() >= 0.82)


def author_matches(query, author):
    """True when every word of the searched name occurs in one of the book's authors."""
    qw = norm(query).split()
    if not qw:
        return True
    for a in (author or "").split(","):
        aw = norm(a).split()
        if aw and all(any(_word_eq(w, x) for x in aw) for w in qw):
            return True
    return False


NOISE_TITLE = ("study guide", "summary", "summaries", "analysis", "sparknotes", "cliffsnotes",
               "cliffs notes", "coles notes", "notes on", "companion to", "reader s companion",
               "workbook", "teaching", "teacher s", "lesson plan", "critical essays", "bloom s",
               "adaptation", "with connections", "reading guide", "reader s guide", "literature guide",
               "trivia", "quiz", "book report", "in a nutshell", "conversation starters",
               "key takeaways", "unofficial")
NOISE_AUTHOR = ("editorial board", "publishing company", "sparknotes", "cliffsnotes", "bookrags",
                "shmoop", "gradesaver", "supersummary", "bright summaries", "instaread",
                "readtrepreneur", "quickread")


def is_noise(title, author):
    """Study guides, summaries, adaptations ... (hidden by default, one click to show)."""
    t, a = norm(title), norm(author)
    if any(k in t for k in NOISE_TITLE) or any(k in a for k in NOISE_AUTHOR):
        return True
    m = re.search(r" by ([^\W\d_]+ [^\W\d_]+)$", t)         # "1984 by George Orwell" written by someone else
    return bool(m and m.group(1).split()[-1] not in surnames(author))


COVER_PREF = {"Taaghche": -1, "Apple Books": 0, "Google Books": 1,       # nicest covers first;
              "Open Library": 2}                                         # Taaghche = Iranian edition
# Which source to trust for the year: Wikidata / Open Library give the FIRST publication year;
# Google and Apple give the date of one particular (often recent) edition or ebook.
YEAR_PREF = ["Wikidata", "Open Library", "Google Books", "Apple Books"]


def search_books(query, mode, source, publisher=""):
    """Returns (results, notes, info). 'All sources' queries every source at the same
    time and merges the answers; a source that fails only adds a note.
    publisher (optional, Persian searches): use the cover of that publisher's edition."""
    publisher = (publisher or "").strip()
    notes, info = [], ""
    persian = bool(ARABIC.search(query))
    fa_query = query.strip() if mode == "author" and persian else None
    if fa_query:
        try:
            name = wikidata_english_author(query)
        except ApiError as e:
            notes.append(f"Wikidata: {e}")
            name = None
        if name:
            info, query = f"Searched as: {name}", name
    title_q, hint = split_query(query) if mode == "title" else (query.strip(), "")

    names = list(PROVIDERS) if source == "All sources" else [source]
    if source == "All sources" and not load_settings().get("google_api_key"):
        names.remove("Google Books")        # without a key it is almost always rate-limited
    if source == "All sources" and not persian:
        names.remove("Taaghche")            # Persian store: only useful for Persian searches
    if publisher and "Taaghche" not in names:
        info = (info + "\n" if info else "") + \
            "The publisher filter only works for Persian searches (it uses Taaghche)."
    answers, failed = {}, 0
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        def provider(n):                    # a wanted publisher may be on a later page: look deeper
            return functools.partial(PROVIDERS[n], pages=4) if (n == "Taaghche" and publisher) \
                else PROVIDERS[n]
        # Wikidata gets the ORIGINAL Persian name: it knows the Persian titles of the books
        futures = {n: pool.submit(provider(n), fa_query if (n in ("Wikidata", "Taaghche") and fa_query) else title_q,
                                  mode, hint) for n in names}
        for n in names:
            try:
                answers[n] = futures[n].result()
            except Exception as e:
                failed += 1
                notes.append(f"{n}: {e}")
    if failed == len(names):
        raise ApiError("; ".join(notes))

    # one entry per book: same core title and at least one shared author surname
    groups = []
    for n in names:
        for d in answers.get(n, []):
            if not d["title"].strip():
                continue
            cores = {core_title(d["title"])} | ({d["alt_core"]} if d.get("alt_core") else set())
            sn = surnames(d["author"]) | set(d.get("alt_sn") or ())
            for g in groups:
                same_title = bool(g["cores"] & cores) and (not g["sn"] or not sn or g["sn"] & sn)
                # "1984" and "Nineteen Eighty-Four": same author, same first year, both famous
                same_work = (d.get("year") and d["year"] in g["years"].values() and g["sn"] & sn
                             and min(g["pop"], d.get("pop", 0)) >= 50)
                if same_title or same_work:
                    break
            else:
                g = dict(title=d["title"], author=d["author"], editions=[],
                         source=n, cores=set(), sn=set(), covers={}, years={}, pop=0)
                groups.append(g)
            g["cores"] |= cores
            g["sn"] |= sn
            g["pop"] = max(g["pop"], d.get("pop", 0))
            if d.get("year"):
                g["years"].setdefault(n, d["year"])
            if fa_query and bool(ARABIC.search(d["title"])) != bool(ARABIC.search(g["title"])):
                better = bool(ARABIC.search(d["title"]))       # Persian title wins in a Persian search
            else:
                better = len(d["title"]) < len(g["title"])     # "1984" beats "1984 (adaptation)"
            if better:
                g.update(title=d["title"], author=d["author"] or g["author"])
            if d.get("cover_url"):
                g["covers"].setdefault(n, d["cover_url"])
                if d.get("publisher") and (d["publisher"], d["cover_url"]) not in g["editions"]:
                    g["editions"].append((d["publisher"], d["cover_url"]))   # Iranian editions
            if d.get("cover_query") and "cover_query" not in g:
                g["cover_query"] = d["cover_query"]

    items = []
    for g in groups:
        urls = [u for _, u in sorted(g["covers"].items(), key=lambda kv: COVER_PREF.get(kv[0], 3))]
        cover_pub, pub_hit = None, False
        if g["editions"]:
            cover_pub = g["editions"][0][0]                  # the Taaghche cover comes first
            mine = [(pb, u) for pb, u in g["editions"] if publisher and publisher_matches(publisher, pb)]
            if mine:                                         # the wanted publisher's edition only
                urls, cover_pub, pub_hit = list(dict.fromkeys(u for _, u in mine))[:2], mine[0][0], True
        first_author = (g["author"] or "").split(",")[0].strip()
        query_for_cover = g.get("cover_query") or ((g["title"], first_author) if first_author else None)
        year_src = next((n for n in YEAR_PREF if n in g["years"]), None)
        items.append(dict(title=g["title"], author=g["author"],
                          year=g["years"].get(year_src),
                          year_is_edition=year_src in ("Google Books", "Apple Books"),
                          pop=g["pop"],
                          source=g["source"], cover_urls=urls,
                          publishers=list(dict.fromkeys(pb for pb, _ in g["editions"])),
                          cover_publisher=cover_pub, pub_hit=pub_hit,
                          cover_query=None if urls else query_for_cover,
                          noise=is_noise(g["title"], g["author"])))

    nq, ha = norm(title_q), surname(hint)
    words = nq.split()

    if mode == "author":
        # the AUTHOR is the criterion: books that only have the name in their title are dropped
        kept = [d for d in items if author_matches(title_q, d["author"])
                or (fa_query and author_matches(fa_query, d["author"]))]
        if len(kept) < len(items):
            extra = f"{len(items) - len(kept)} results not written by this author were hidden"
            info = f"{info}\n{extra}" if info else extra
        items = kept

        def author_rank(d):
            exact = any(norm(a) in (nq, norm(fa_query or "")) for a in d["author"].split(","))
            return (not exact, -d.get("pop", 0), norm(d["title"]))
        return sorted(items, key=author_rank)[:50], notes, info

    def rank(d):                      # best matches first
        t = norm(d["title"])
        return (bool(ha) and ha not in norm(d["author"]),   # author hint matches
                t != nq,                                   # exact title
                not t.startswith(nq),
                not all(w in t.split() for w in words),    # contains every query word
                -d.get("pop", 0))                          # then the most popular edition count
    return sorted(items, key=rank)[:50], notes, info


def looks_like_image(data):
    """JPEG, PNG, WebP or GIF by the first bytes of the file (the file name proves nothing)."""
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP") or data[:4] == b"GIF8")


def fetch_cover(url):
    COVERS_DIR.mkdir(exist_ok=True)
    path = COVERS_DIR / (hashlib.md5(url.encode()).hexdigest() + ".jpg")
    if not path.exists():
        try:
            data = http_get(url, max_bytes=MAX_IMAGE)
        except ApiError:
            if "400x400bb" not in url:
                raise
            data = http_get(url.replace("400x400bb", "100x100bb"), max_bytes=MAX_IMAGE)
        if len(data) < 500:
            raise ValueError("no cover")
        if not looks_like_image(data):                 # an HTML error page saved as .jpg, etc.
            raise ValueError("no usable cover")
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.part")
        try:
            tmp.write_bytes(data)
            size = QImageReader(str(tmp)).size()       # reads the header only, nothing is decoded
            if not size.isValid() or size.width() * size.height() > MAX_PIXELS:
                raise ValueError("no usable cover")
            tmp.replace(path)                          # a half-written file never enters covers/
        finally:
            tmp.unlink(missing_ok=True)
    return str(path)


def fetch_best_cover(urls):
    """Try the candidates in order; take the first sharp one, else the biggest."""
    best = None
    for url in urls:
        try:
            path = fetch_cover(url)
        except Exception:
            continue
        img = QImage(path)
        if img.isNull():
            continue
        if img.height() >= 300 and img.width() >= 180:
            return path
        if best is None or img.width() * img.height() > best[0]:
            best = (img.width() * img.height(), path)
    if best:
        return best[1]
    raise ValueError("no usable cover")


def fetch_cover_checked(url):
    """One given cover address (no fallbacks): network errors stay visible as ApiError, and a
    file that is not a picture is thrown away."""
    path = fetch_cover(url)
    if QImage(path).isNull():
        Path(path).unlink(missing_ok=True)
        raise ValueError("no usable cover")
    return path


# ---- result previews: small pictures that live in memory only (never written to covers/). One download
# per row; the picture is cut down while it is decoded, and the newest PREVIEW_MAX stay in a small cache.
PREVIEW_W, PREVIEW_H = 93, 138        # 1.5 x the 62 x 92 box of a result row
PREVIEW_MAX = 120                     # about 6 MB of pictures at most
MAX_PREVIEW_BYTES = 1536 * 1024
_PREVIEWS, _PREVIEW_LOCK = {}, threading.Lock()


def preview_get(key):
    """QImage, False (looked up before, nothing found) or None (not looked up yet)."""
    with _PREVIEW_LOCK:
        return _PREVIEWS.get(key)


def preview_put(key, value):
    with _PREVIEW_LOCK:
        _PREVIEWS.pop(key, None)
        _PREVIEWS[key] = value
        while len(_PREVIEWS) > PREVIEW_MAX:
            del _PREVIEWS[next(iter(_PREVIEWS))]


def preview_url(url):
    """A smaller version of the same picture where the source offers sizes (Open Library S/M/L,
    Apple's size in the address); any other address is used as it is."""
    if url.startswith("https://covers.openlibrary.org/") and url.endswith("-L.jpg"):
        return url[:-6] + "-M.jpg"
    if "400x400bb" in url:
        return url.replace("400x400bb", "200x200bb")
    return url


def decode_preview(data):
    """Bytes of a picture -> QImage no bigger than PREVIEW_W x PREVIEW_H. A big picture is cut down
    while it is read, so it is never decoded at full size. Raises ValueError for anything unusable."""
    if not looks_like_image(data):
        raise ValueError("no usable cover")
    buf = QBuffer()
    buf.setData(QByteArray(data))
    buf.open(QIODevice.ReadOnly)
    reader = QImageReader(buf)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > MAX_PIXELS:
        raise ValueError("no usable cover")
    k = min(PREVIEW_W / size.width(), PREVIEW_H / size.height(), 1.0)
    if k < 1.0:
        reader.setScaledSize(QSize(max(1, round(size.width() * k)), max(1, round(size.height() * k))))
    img = reader.read()
    if img.isNull():
        raise ValueError("no usable cover")
    return img


def fetch_preview(url):
    """The preview of one cover address: one download (the small variant when there is one)."""
    hit = preview_get(url)
    if hit:
        return hit
    small = preview_url(url)
    try:
        data = http_get(small, max_bytes=MAX_PREVIEW_BYTES)
    except ApiError:
        if small == url:
            raise
        data = http_get(url, max_bytes=MAX_PREVIEW_BYTES)     # that size was not offered: the normal one
    img = decode_preview(data)
    preview_put(url, img)
    return img


def find_preview(title, author):
    """Preview for a book that came without a cover address (Persian / Wikidata results): look it up
    by title + author, then download ONE picture."""
    key = ("find", norm(title), norm(author))
    hit = preview_get(key)
    if hit:
        return hit
    if hit is False:
        raise ValueError("no cover found")
    want, url, errors = surname(author), None, []
    for search in (search_apple, functools.partial(search_openlibrary, popular=False)):
        try:
            for d in search(title, "title", author):
                if d["cover_url"] and (not want or want in norm(d["author"])):
                    url = d["cover_url"]
                    break
        except ApiError as e:
            errors.append(e)
            continue
        if url:
            break
    if not url:
        if len(errors) == 2:                  # both sources failed: it is the network, not the book
            raise errors[0]
        preview_put(key, False)
        raise ValueError("no cover found")
    img = fetch_preview(url)
    preview_put(key, img)
    return img


def find_cover(title, author):
    """For books that came without a cover: look one up by title + author."""
    want = surname(author)
    urls, errors = [], []
    for search in (search_apple, search_openlibrary):
        try:
            for d in search(title, "title", author):
                if d["cover_url"] and (not want or want in norm(d["author"])) \
                        and d["cover_url"] not in urls:
                    urls.append(d["cover_url"])
        except ApiError as e:
            errors.append(e)
            continue
        if urls:
            break
    if not urls:
        if len(errors) == 2:                  # both sources failed: it is the network, not the book
            raise errors[0]
        raise ValueError("no cover found")
    return fetch_best_cover(urls[:4])


def _cover_via_wikidata(title, author):
    """Persian title -> Wikidata knows its English title + author -> look the cover up with those."""
    nt = norm(title)
    cands = [d for d in search_wikidata(title, "title")
             if d.get("cover_query") and d["cover_query"][0] and d["cover_query"][1]
             and (not author or author_matches(author, d["author"])
                  or author_matches(author, d["cover_query"][1]))]
    if not cands:
        raise ValueError("no cover found")
    cands.sort(key=lambda d: norm(d["title"]) != nt)           # exact title first
    return find_cover(*cands[0]["cover_query"])


def find_cover_taaghche(title, author):
    """Cover of the Iranian edition: search Taaghche for the Persian title; when the author is Persian
    too it must match. Only an exact title (or the same title before a ':' / '(' subtitle) is accepted,
    so a different book is never picked."""
    cands = [d for d in search_taaghche(title, "title") if d["cover_url"]]
    if author and ARABIC.search(author):
        cands = [d for d in cands if author_matches(author, d["author"])]
    exact = [d for d in cands if norm(d["title"]) == norm(title)]
    cands = exact or [d for d in cands if core_title(d["title"]) == core_title(title)]
    if not cands:
        raise ValueError("no cover found")
    return fetch_best_cover(list(dict.fromkeys(d["cover_url"] for d in cands))[:4])


def taaghche_cover_url(text):
    """A Taaghche book link (taaghche.com/book/134219/...) or the link of its cover image ->
    the cover image URL. Works for books the search does not list (e.g. marked "unavailable"),
    because the cover address only depends on the book id. Raises ValueError with a message."""
    text = (text or "").strip().translate(_DIGITS)
    if not text:
        raise ValueError("Paste a Taaghche book link first.")
    try:
        u = urllib.parse.urlparse(text if "//" in text else "https://" + text)
        host, path, port = (u.hostname or "").lower(), urllib.parse.unquote(u.path), u.port
    except ValueError:
        u, host, path, port = urllib.parse.urlparse(""), "", "", None
    m = None
    if u.scheme not in ("http", "https") or u.username or u.password or port not in (None, 80, 443):
        host = ""                                    # odd link: treated as "not a Taaghche link"
    if host in ("taaghche.com", "www.taaghche.com"):
        if path.startswith("/audiobook/"):
            raise ValueError("That is an audiobook page (its cover is square). Open the book’s "
                             "ebook page on Taaghche and paste that link.")
        m = re.match(r"/book/(\d{1,10})(?:/|$)", path)
    elif host == "img.taaghche.com":
        m = re.match(r"/frontCover/(\d{1,10})\.\w+$", path)
    if not m:
        raise ValueError("That doesn’t look like a Taaghche book link. It should look like "
                         "taaghche.com/book/123456/…")
    return f"https://img.taaghche.com/frontCover/{m.group(1)}.jpg"


def find_cover_for(title, author):
    """The 'Find cover' button: try title + author as typed; for Persian text also try the
    English author name and the English title that Wikidata knows."""
    def translated_author():
        name = wikidata_english_author(author)
        if not name:
            raise ValueError("no cover found")
        return find_cover(title, name)

    steps = []
    if ARABIC.search(title):
        steps.append(lambda: find_cover_taaghche(title, author))   # the Iranian edition first
    steps.append(lambda: find_cover(title, author))
    if ARABIC.search(author or ""):
        steps.append(translated_author)
    if ARABIC.search(title):
        steps.append(lambda: _cover_via_wikidata(title, author))
    errors = []
    for step in steps:
        try:
            return step()
        except (ValueError, ApiError) as e:
            errors.append(e)
    if all(isinstance(e, ApiError) for e in errors):
        raise errors[0]                                         # e.g. "no connection"
    raise ValueError("no cover found")


COVER_POOL = QThreadPool()          # few threads: be polite to the cover servers
COVER_POOL.setMaxThreadCount(3)
LOOKUP_POOL = QThreadPool()         # previews that need a search first: one at a time
LOOKUP_POOL.setMaxThreadCount(1)
COVER_ATTACH = {"n": 0}             # covers still being fetched for books that were just added


class JobSignals(QObject):
    done = Signal(object, object)
    failed = Signal(object, str)


class Job(QRunnable):
    """Runs fn(*args) on a worker thread so the window never freezes."""
    def __init__(self, tag, fn, *args):
        super().__init__()
        self.tag, self.fn, self.args = tag, fn, args
        self.signals = JobSignals()

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as e:  # network errors, bad JSON, ...
            self.signals.failed.emit(self.tag, str(e))
        else:
            self.signals.done.emit(self.tag, result)


# ------------------------------------------------------------- drawing
_PIX = {}


def get_pixmap(path):
    if not path:
        return None
    if not os.path.isabs(path):
        path = str(COVERS_DIR / path)
    if path not in _PIX:
        pm = QPixmap(path)
        _PIX[path] = None if pm.isNull() else pm
    return _PIX[path]


def draw_book_cover(p, area, pm, vpad=22, hpad=30):
    h = area.height() - vpad
    w = h * pm.width() / pm.height()
    if w > area.width() - hpad:
        w = area.width() - hpad
        h = w * pm.height() / pm.width()
    rect = QRectF(area.center().x() - w / 2, area.center().y() - h / 2 + 2, w, h)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    shadow = QPainterPath()
    shadow.addRoundedRect(rect.translated(2, 3), 5, 5)
    p.fillPath(shadow, QColor(0, 0, 0, 90))
    clip = QPainterPath()
    clip.addRoundedRect(rect, 5, 5)
    p.save()
    p.setClipPath(clip)
    p.drawPixmap(rect, pm, QRectF(pm.rect()))
    p.restore()

_BLUR = {}


def blurred_backdrop(pm):
    """Tiny smooth copy of the cover; drawn stretched it looks like a soft blur of the same picture.
    Cached, so each cover is shrunk only once."""
    key = pm.cacheKey()
    if key not in _BLUR:
        _BLUR[key] = pm.scaled(10, 15, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return _BLUR[key]


def clear_icon():
    """Round 'x' for the search box, in the current theme's colours (the stock Qt clear button is
    nearly invisible on the dark theme)."""
    pm = QPixmap(32, 32)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T["muted"]))
    p.drawEllipse(QRectF(1, 1, 14, 14))
    p.setPen(QPen(QColor(T["input"]), 1.6, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(5.3, 5.3), QPointF(10.7, 10.7))
    p.drawLine(QPointF(10.7, 5.3), QPointF(5.3, 10.7))
    p.end()
    return QIcon(pm)


def mix(c1, c2, t):
    """Colour between c1 (t=0) and c2 (t=1)."""
    return QColor(*(round(a + (b - a) * t) for a, b in
                    ((c1.red(), c2.red()), (c1.green(), c2.green()), (c1.blue(), c2.blue()))))


def star_path(cx, cy, r):
    path = QPainterPath()
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.45
        pt = QPointF(cx + rad * math.cos(ang), cy + rad * math.sin(ang))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path


def draw_stars(p, x, y, r, rating, gap=4):
    p.setPen(Qt.NoPen)
    for i in range(5):
        p.setBrush(QColor("#ffc94a") if i < (rating or 0) else QColor(T["star_off"]))
        p.drawPath(star_path(x + r + i * (2 * r + gap), y + r, r))


def cover_colors(title):
    h = int(hashlib.md5(title.encode("utf-8")).hexdigest()[:4], 16) % 360
    return QColor.fromHsl(h, 140, 105), QColor.fromHsl((h + 45) % 360, 170, 72)


def is_rtl(text):
    for ch in text:
        if ch.isalpha():
            return "\u0590" <= ch <= "\u08ff"
    return False


def align_for(text):
    return (Qt.AlignRight if is_rtl(text) else Qt.AlignLeft) | Qt.AlignVCenter


def wrap_lines(text, font, width, max_lines=2):
    """Breaks text into at most max_lines lines that fit `width`; the last line ends with … when the
    text is longer. Long words without spaces are broken too (Persian titles, URLs...)."""
    text = " ".join((text or "").split())
    if not text:
        return []
    lay = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    lay.setTextOption(opt)
    spans = []
    lay.beginLayout()
    while True:
        line = lay.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        spans.append((line.textStart(), line.textLength()))
        if len(spans) > max_lines:                    # one more than allowed: the text overflows
            break
    lay.endLayout()
    if len(spans) <= max_lines:
        return [text[a:a + n].strip() for a, n in spans]
    out = [text[a:a + n].strip() for a, n in spans[:max_lines - 1]]
    out.append(QFontMetrics(font).elidedText(text[spans[max_lines - 1][0]:], Qt.ElideRight, int(width) - 3))
    return out


class BookDelegate(QStyledItemDelegate):
    """Draws the cards. The hovered card rises smoothly (about 0.1 s) instead of jumping."""
    STEP = 0.16                                   # progress per tick (16 ms): ~100 ms in total

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.level = {}                           # book id -> hover progress 0..1
        self.hovered = None                       # id of the card under the mouse
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.recheck)

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.MouseMove:
            idx = self.view.indexAt(e.position().toPoint())
            row = idx.data(Qt.UserRole) if idx.isValid() else None
            self.set_hovered(row[0] if row else None)
        elif t == QEvent.Leave:
            self.set_hovered(None)
        return False                              # never swallow an event

    def recheck(self, *_):
        """The mouse did not move but the cards did (wheel / scroll bar): find the card under it again."""
        vp = self.view.viewport()
        pos = QCursor.pos()
        if QApplication.widgetAt(pos) is not vp:
            self.set_hovered(None)
            return
        idx = self.view.indexAt(vp.mapFromGlobal(pos))
        row = idx.data(Qt.UserRole) if idx.isValid() else None
        self.set_hovered(row[0] if row else None)

    def set_hovered(self, book_id):
        if book_id == self.hovered:
            return
        self.hovered = book_id
        if book_id is not None:
            self.level.setdefault(book_id, 0.0)
        if not self.timer.isActive() and self.level:
            self.timer.start()

    def reset(self):
        """Called when the list is rebuilt (card ids are no longer the same cards)."""
        self.level.clear()
        self.hovered = None
        self.timer.stop()

    def _tick(self):
        for k in list(self.level):
            goal = 1.0 if k == self.hovered else 0.0
            v = self.level[k]
            v = min(goal, v + self.STEP) if goal > v else max(goal, v - self.STEP)
            if v == 0.0 and goal == 0.0:
                del self.level[k]
            else:
                self.level[k] = v
        if not self.level or all(v == (1.0 if k == self.hovered else 0.0) for k, v in self.level.items()):
            self.timer.stop()
        self.view.viewport().update()

    def sizeHint(self, option, index):
        return QSize(CARD_W, CARD_H)

    def paint(self, p, option, index):
        _id, title, author, status, rating, _notes, cover_path, started, finished = index.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        r = QRectF(option.rect).adjusted(8, 8, -8, -8)
        selected = bool(option.state & QStyle.State_Selected)
        lvl = self.level.get(_id, 0.0)
        lvl = lvl * lvl * (3 - 2 * lvl)                       # ease in-out
        r.translate(0, -4 * lvl)

        # soft shadow
        for i, a in enumerate((10, 16, 22)):
            sh = QPainterPath()
            sh.addRoundedRect(r.adjusted(-3 + i, 2 + i, 3 - i, 6 - i), 18, 18)
            p.fillPath(sh, QColor(0, 0, 0, a if T["bg"] == "#14151b" else a // 2))

        card = QPainterPath()
        card.addRoundedRect(r, 16, 16)
        p.fillPath(card, QColor(T["card"]))
        p.setPen(QPen(QColor(T["accent"]) if selected
                      else mix(QColor(T["border"]), QColor(T["accent"]), lvl),
                      2 if selected else 1))
        p.drawPath(card)

        # cover
        cover = QRectF(r.left(), r.top(), r.width(), COVER_H)
        cover_clip = QPainterPath()
        cover_clip.addRect(cover)
        p.setClipPath(card.intersected(cover_clip))
        pm = get_pixmap(cover_path)
        if pm:                                                # the cover's own soft blur behind it
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(cover, blurred_backdrop(pm), QRectF(blurred_backdrop(pm).rect()))
            p.fillRect(cover, QColor(0, 0, 0, 70))
            draw_book_cover(p, cover, pm, vpad=22, hpad=26)
        else:
            c1, c2 = cover_colors(title)
            grad = QLinearGradient(cover.topLeft(), cover.bottomRight())
            grad.setColorAt(0, c1)
            grad.setColorAt(1, c2)
            p.fillRect(cover, grad)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawEllipse(QPointF(r.right() - 10, r.top() + 15), 62, 62)
            p.setBrush(QColor(0, 0, 0, 28))
            p.drawEllipse(QPointF(r.left() + 10, cover.bottom() - 5), 48, 48)
            f = QFont(option.font)
            f.setPointSize(13)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 240))
            short = title if len(title) <= 48 else title[:47] + "…"
            p.drawText(cover.adjusted(16, 28, -16, -14), Qt.AlignCenter | Qt.TextWordWrap, short)
        p.setClipping(False)

        # status chip
        f = QFont(option.font)
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        chip_w = fm.horizontalAdvance(status or "") + 18
        chip = QRectF(r.left() + 10, r.top() + 10, chip_w, 22)
        p.setPen(Qt.NoPen)
        chip_color = QColor(STATUS_COLORS.get(status, "#000000"))
        chip_color.setAlpha(235 if status in STATUS_COLORS else 110)
        p.setBrush(chip_color)
        p.drawRoundedRect(chip, 11, 11)
        p.setPen(QColor("white"))
        p.drawText(chip, Qt.AlignCenter, status or "")

        # title / author / stars
        tx, tw = r.left() + 14, r.width() - 28
        f = QFont(option.font)
        f.setPointSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T["text"]))
        lh = min(QFontMetrics(f).lineSpacing(), 20)
        lines = wrap_lines(title, f, tw, 2) or [""]
        for i, line in enumerate(lines):
            p.drawText(QRectF(tx, r.top() + COVER_H + 8 + i * lh, tw, lh), align_for(title), line)
        author_top = r.top() + COVER_H + 10 + len(lines) * lh

        f = QFont(option.font)
        f.setPointSize(9)
        p.setFont(f)
        p.setPen(QColor(T["muted"]))
        p.drawText(QRectF(tx, author_top, tw, 20), align_for(author or ""),
                   QFontMetrics(f).elidedText(author or "Unknown author", Qt.ElideRight, int(tw)))

        if status == WISH:                      # wishlist card: first note line (price, shop...)
            line = next(iter((_notes or "").strip().splitlines()), "")
            p.drawText(QRectF(tx, r.top() + COVER_H + 74, tw, 22), align_for(line),
                       QFontMetrics(f).elidedText(line, Qt.ElideRight, int(tw)))
        else:
            draw_stars(p, tx, r.top() + COVER_H + 76, 9, rating)
        note = (f"Finished {fmt_date(finished)}" if finished
                else f"Started {fmt_date(started)}" if started and status == "Reading" else "")
        if note:
            f = QFont(option.font)
            f.setPointSize(8)
            p.setFont(f)
            p.setPen(QColor(T["muted"]))
            p.drawText(QRectF(tx, r.top() + COVER_H + 102, tw, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f).elidedText(note, Qt.ElideRight, int(tw)))
        p.restore()


class StarRating(QWidget):
    """Click a star to rate, click the same star again to clear. Hover previews the rating,
    and the keyboard works too (Left/Right or 0-5 once the widget has focus)."""
    changed = Signal(int)

    def __init__(self, value=0):
        super().__init__()
        self.value = value
        self._hover = 0
        self.setFixedSize(5 * 30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("Click a star to rate. Click the same star again to clear.")

    def setValue(self, v):
        v = max(0, min(5, int(v or 0)))
        if v != self.value:
            self.value = v
            self.update()
            self.changed.emit(v)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        draw_stars(p, 0, 1, 12, self._hover or self.value, gap=6)
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T["accent"]), 1.5))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)

    def _star_at(self, x):
        return max(1, min(5, int(x // 30) + 1))

    def mouseMoveEvent(self, e):
        n = self._star_at(e.position().x())
        if n != self._hover:
            self._hover = n
            self.update()

    def leaveEvent(self, _):
        self._hover = 0
        self.update()

    def mousePressEvent(self, e):
        n = self._star_at(e.position().x())
        self._hover = 0
        self.setValue(0 if n == self.value else n)
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Right, Qt.Key_Up):
            self.setValue(self.value + 1)
        elif k in (Qt.Key_Left, Qt.Key_Down):
            self.setValue(self.value - 1)
        elif Qt.Key_0 <= k <= Qt.Key_5:
            self.setValue(k - Qt.Key_0)
        else:
            super().keyPressEvent(e)


class CoverPreview(QWidget):
    """Book cover shown at a real 2:3 size in the dialog. Without a cover it shows the same
    gradient + title as the library card, so the dialog already previews how the card will look."""
    COVER_W, COVER_H, PAD = 150, 225, 8

    def __init__(self):
        super().__init__()
        self.pm, self.title = None, ""
        self.setFixedSize(self.COVER_W + 2 * self.PAD, self.COVER_H + 2 * self.PAD)

    def set_cover(self, pm, title=None):
        self.pm = pm
        if title is not None:
            self.title = title
        self.update()

    def set_title(self, title):
        self.title = title
        if not self.pm:
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        rect = QRectF(self.PAD, self.PAD, self.COVER_W, self.COVER_H)
        if self.pm:
            draw_book_cover(p, rect.adjusted(-15, -11, 15, 11), self.pm)
            return
        for i, a in enumerate((12, 18, 26)):                      # soft shadow, like the cards
            sh = QPainterPath()
            sh.addRoundedRect(rect.adjusted(-2 + i, 2 + i, 2 - i, 5 - i), 12, 12)
            p.fillPath(sh, QColor(0, 0, 0, a if T["bg"] == "#14151b" else a // 2))
        card = QPainterPath()
        card.addRoundedRect(rect, 10, 10)
        c1, c2 = cover_colors(self.title or "book")
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.save()
        p.setClipPath(card)
        p.fillRect(rect, grad)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawEllipse(QPointF(rect.right() - 6, rect.top() + 10), 70, 70)
        p.setBrush(QColor(0, 0, 0, 28))
        p.drawEllipse(QPointF(rect.left() + 8, rect.bottom() - 4), 54, 54)
        p.restore()
        title = self.title.strip()
        if title:
            f = QFont(self.font())
            f.setPointSize(13)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 240))
            short = title if len(title) <= 60 else title[:59] + "…"
            p.drawText(rect.adjusted(16, 24, -16, -40), Qt.AlignCenter | Qt.TextWordWrap, short)
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 185))
        p.drawText(rect.adjusted(0, 0, 0, -12), Qt.AlignHCenter | Qt.AlignBottom, "No cover yet")


class SegmentedChoice(QFrame):
    """A few mutually exclusive options side by side (Apple HIG: 2-5 options, short labels, the
    current choice always visible). Same small API as QComboBox: currentText / setCurrentText /
    currentTextChanged."""
    currentTextChanged = Signal(str)

    def __init__(self, options):
        super().__init__()
        self.setObjectName("segtrack")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.btns = {}
        for text in options:
            b = QPushButton(text, objectName="segopt")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, t=text: self._pick(t))
            self.group.addButton(b)
            self.btns[text] = b
            lay.addWidget(b, 1)
        self._cur = options[0]
        self.btns[self._cur].setChecked(True)

    def _pick(self, text):
        if text != self._cur:
            self._cur = text
            self.currentTextChanged.emit(text)

    def currentText(self):
        return self._cur

    def setCurrentText(self, text):
        if text in self.btns:
            self.btns[text].setChecked(True)
            self._pick(text)


class CheckBox(QCheckBox):
    """Check box drawn by hand so it follows the dark/light theme (the default Fusion one is a
    bright white square on the dark theme). Same API as QCheckBox."""
    BOX = 18

    def __init__(self, text=""):
        super().__init__(text)
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        fm = self.fontMetrics()
        w = self.BOX + 2 + (9 + fm.horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(w, max(self.BOX, fm.height()) + 8)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(1, (self.height() - self.BOX) / 2, self.BOX, self.BOX)
        on, live = self.isChecked(), self.isEnabled()
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T["accent"]))
        else:
            p.setPen(QPen(QColor(T["accent"] if (self.underMouse() and live) else T["border"]), 1.5))
            p.setBrush(QColor(T["input"]))
        p.drawRoundedRect(box, 5, 5)
        if on:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("white"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath()
            path.moveTo(box.left() + box.width() * 0.26, box.top() + box.height() * 0.52)
            path.lineTo(box.left() + box.width() * 0.43, box.top() + box.height() * 0.69)
            path.lineTo(box.left() + box.width() * 0.75, box.top() + box.height() * 0.32)
            p.drawPath(path)
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T["accent"]), 1.5))
            p.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 7, 7)
        if self.text():
            p.setPen(QColor(T["text"] if live else T["muted"]))
            p.drawText(QRectF(self.BOX + 10, 0, self.width() - self.BOX - 10, self.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, self.text())


class LinkPopover(QFrame):
    """Small floating panel under the cover buttons for pasting a Taaghche link (closes when you
    click anywhere else). Keeps the form itself clean."""

    def __init__(self, parent):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="popover")
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        v.addWidget(QLabel("Cover from a Taaghche link", objectName="flabel"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("taaghche.com/book/134219/…")
        self.go = QPushButton("Get cover", objectName="primary")
        row.addWidget(self.edit, 1)
        row.addWidget(self.go)
        v.addLayout(row)
        self.err = QLabel("", objectName="ferror")
        self.err.setWordWrap(True)
        self.err.setVisible(False)
        v.addWidget(self.err)
        hint = QLabel("Open the book’s page on Taaghche, copy the address from the browser "
                      "and paste it here.", objectName="muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.setFixedWidth(420)

    def show_error(self, message):
        self.err.setText(message)
        self.err.setVisible(bool(message))
        self.adjustSize()

    def show_below(self, widget):
        self.show_error("")
        self.adjustSize()
        self.move(widget.mapToGlobal(QPoint(0, widget.height() + 6)))
        self.show()
        self.edit.setFocus()
        self.edit.selectAll()


# ---- small helpers for the book form
def set_invalid(widget, bad):
    """Red outline on an input (the QSS rule QLineEdit[invalid="true"] reads this property)."""
    widget.setProperty("invalid", bool(bad))
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def labeled(text, widget, note=""):
    """A field = its label ABOVE the input (+ a small muted note such as 'optional').
    NN/g, web.dev and Baymard all recommend top-aligned labels; placeholders only show examples."""
    box = QWidget()
    box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)   # never stretch: free space goes below
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(QLabel(text, objectName="flabel"))
    if note:
        row.addWidget(QLabel(note, objectName="fnote"))
    row.addStretch()
    v.addLayout(row)
    v.addWidget(widget)
    return box


def divider():
    line = QFrame(objectName="hline")
    line.setFixedHeight(1)
    return line


# -------------------------------------------------------------- dialog
class NumberEdit(QLineEdit):
    """Whole-number input that accepts Persian digits and clamps to [lo, hi]."""
    valueChanged = Signal(int)

    def __init__(self, lo, hi, fa=False):
        super().__init__()
        self.lo, self.hi = lo, hi
        self.fa = fa                              # show Persian digits (typing either kind works)
        self.setAlignment(Qt.AlignCenter)
        self.editingFinished.connect(self._clamp)

    def value(self):
        m = re.search(r"\d+", self.text().translate(_DIGITS))
        v = int(m.group()) if m else self.lo
        return max(self.lo, min(self.hi, v))

    def setValue(self, v):
        text = str(max(self.lo, min(self.hi, v)))
        self.setText(text.translate(_FA_DIGITS) if self.fa else text)

    def setRange(self, lo, hi):
        self.lo, self.hi = lo, hi

    def setMaximum(self, hi):
        self.hi = hi
        if self.text():
            self.setValue(self.value())

    def _clamp(self):
        self.setValue(self.value())
        self.valueChanged.emit(self.value())


class DateField(QWidget):
    """[ ] precision day month year - empty until ticked. Gregorian or Jalali, as chosen in
    Settings. Precision: exact date / month + year / year only (for books read long ago).
    iso() / set_iso() work with STORED dates (see split_stored)."""
    changed = Signal()
    PRECS = ("d", "m", "y")
    LABELS = ("Exact date", "Month & year", "Year only")

    def __init__(self, label=""):
        super().__init__()
        self._filled = False
        self.jalali = CAL["mode"] == "jalali"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.on = CheckBox(label)                   # the label doubles as the on/off switch
        self.off_lbl = QLabel("Not set", objectName="muted")
        if label:
            self.on.setMinimumWidth(88)
        self.on.toggled.connect(self._toggled)
        self.prec = QComboBox()
        self.prec.addItems(self.LABELS)
        self.d = NumberEdit(1, 31, fa=self.jalali)
        self.m = QComboBox()
        self.m.addItems(month_names())
        self.y = NumberEdit(1300, 1500, fa=True) if self.jalali else NumberEdit(1900, 2100)
        self.d.setFixedWidth(54)
        self.y.setFixedWidth(78)
        lay.addWidget(self.on)
        lay.addWidget(self.off_lbl)
        lay.addWidget(self.prec)
        lay.addWidget(self.d)
        lay.addWidget(self.m, 1)
        lay.addWidget(self.y)
        lay.addStretch(0)                           # takes the free space when the month is hidden
        self._lay = lay
        self.prec.currentIndexChanged.connect(self._prec_changed)
        self.m.currentIndexChanged.connect(self._part_changed)
        self.y.valueChanged.connect(self._part_changed)
        self.d.valueChanged.connect(lambda *_: self.changed.emit())
        self.d.setValue(1)
        self.y.setValue(self.y.lo)
        self._apply_prec()
        self._enable(False)

    def focus_chain(self):
        return [self.on, self.prec, self.d, self.m, self.y]

    def _enable(self, on):
        for w in (self.prec, self.d, self.m, self.y):
            w.setEnabled(on)
        self.off_lbl.setVisible(not on)
        self._apply_prec()

    def _apply_prec(self):
        on = self.on.isChecked()                   # unticked: only "Not set" is shown
        p = self.PRECS[self.prec.currentIndex()]
        self.prec.setVisible(on)
        self.d.setVisible(on and p == "d")         # year only: no day, no month
        self.m.setVisible(on and p in ("d", "m"))
        self.y.setVisible(on)
        wide = on and p != "y"
        self._lay.setStretch(4, 1 if wide else 0)  # 4 = month box, 6 = trailing stretch
        self._lay.setStretch(6, 0 if wide else 1)

    def _prec_changed(self, *_):
        self._apply_prec()
        self.changed.emit()

    def _part_changed(self, *_):
        self._fix_day()
        self.changed.emit()

    def _fix_day(self, *_):
        y, m = self.y.value(), self.m.currentIndex() + 1
        self.d.setMaximum(jalali_month_days(y, m) if self.jalali else calendar.monthrange(y, m)[1])

    def _toggled(self, on):
        self._enable(on)
        if on and not self._filled:
            self.set_iso(date.today().isoformat())
            self._enable(True)
        self.changed.emit()

    def set_iso(self, stored):
        iso, prec = split_stored(stored)
        self.on.blockSignals(True)
        self.on.setChecked(bool(iso))
        self.on.blockSignals(False)
        self._enable(bool(iso))
        if iso:
            self._filled = True
            self.prec.blockSignals(True)
            self.prec.setCurrentIndex(self.PRECS.index(prec))
            self.prec.blockSignals(False)
            self._apply_prec()
            y, m, d = split_date(iso)
            self.y.setValue(y)
            self.m.setCurrentIndex(m - 1)
            self._fix_day()
            self.d.setValue(d)

    def iso(self):
        if not self.on.isChecked():
            return None
        prec = self.PRECS[self.prec.currentIndex()]
        y, m, d = self.y.value(), self.m.currentIndex() + 1, self.d.value()
        if prec == "m":
            d = 15                                  # middle of the month
        elif prec == "y":
            m, d = (7, 1) if self.jalali else (7, 2)    # middle of the year
        if self.jalali:
            y, m, d = j2g(y, m, d)
        return make_stored(f"{y:04d}-{m:02d}-{d:02d}", prec)


class BookDialog(QDialog):
    """Add / edit a book (or a wishlist book).
    Layout: header | scrollable body (cover column + one-column form) | sticky footer with the actions.
    Form rules (NN/g, web.dev, Material, Apple HIG): labels above inputs, related fields grouped,
    status as a segmented control, only the date fields that make sense for the status are shown,
    errors appear next to the field, the main action is the filled button on the right."""

    def __init__(self, parent, store, book=None, wish=False):
        super().__init__(parent)
        self.store = store
        self.wish = wish                    # wishlist mode: only title, author, cover, notes
        self.bought = False
        self.book_id = book[0] if book else None
        self.book = book                    # the stored row (what a delete would remove)
        self.deleted = False
        self._auto = {}                     # dates filled in by a status change (not typed by the person)
        self.cover_path = None
        self.cover_gen = 0                  # bumped to ignore a lookup that is no longer wanted
        self.setWindowTitle(("Edit wishlist book" if book else "Add to wishlist") if wish
                            else "Edit book" if book else "Add a book")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(780)
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen else 800
        self.resize(800, max(520, min(820, avail - 90)))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- header
        head_w = QWidget()
        hl = QVBoxLayout(head_w)
        hl.setContentsMargins(28, 22, 28, 16)
        hl.setSpacing(2)
        hl.addWidget(QLabel(("Edit wishlist book" if book else "New wishlist book") if wish
                            else "Edit book" if book else "New book", objectName="h1"))
        hl.addWidget(QLabel("Keep track of a book you want to buy later." if wish
                            else "Update its details, cover or reading dates." if book
                            else "Only the title is required; you can fill in the rest later.",
                            objectName="muted"))
        root.addWidget(head_w)
        root.addWidget(divider())

        # ---- scrollable body
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="formbody")
        self.scroll.setWidget(body)
        body.setAutoFillBackground(False)
        self.scroll.viewport().setAutoFillBackground(False)
        bl = QHBoxLayout(body)
        bl.setContentsMargins(28, 18, 28, 18)
        bl.setSpacing(28)

        # left column: the cover
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        self.cover_prev = CoverPreview()
        left.addWidget(self.cover_prev, alignment=Qt.AlignHCenter)
        self.find_btn = QPushButton("Find cover", objectName="ghost")
        self.find_btn.clicked.connect(self.find_cover_online)
        self.more_btn = QPushButton("More  ▾", objectName="ghost")
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)        # lets the QSS rounded corners show
        menu.setMinimumWidth(172)
        menu.addAction("Choose image file…", lambda *_: self.choose_cover())
        menu.addAction("From Taaghche link…", lambda *_: self.show_link_popover())
        menu.addSeparator()
        self.act_remove = menu.addAction("Remove cover", lambda *_: self.remove_cover())
        self.more_btn.setMenu(menu)
        self.cover_msg = QLabel("", objectName="muted")
        self.cover_msg.setTextFormat(Qt.PlainText)
        self.cover_msg.setWordWrap(True)
        left.addWidget(self.find_btn)
        left.addWidget(self.more_btn)
        left.addWidget(self.cover_msg)
        left.addStretch()
        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setFixedWidth(172)
        bl.addWidget(left_w, 0, Qt.AlignTop)

        # right column: the form (one column)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)

        self.link_pop = LinkPopover(self)               # opened by More > From Taaghche link…
        self.link_e, self.link_go = self.link_pop.edit, self.link_pop.go
        self.link_go.clicked.connect(self.cover_from_link)
        self.link_e.returnPressed.connect(self.cover_from_link)

        self.title_e = QLineEdit()
        self.title_e.setPlaceholderText("e.g. The Stranger   /   بیگانه")
        self.title_err = QLabel("", objectName="ferror")
        self.title_err.setWordWrap(True)
        self.title_err.setVisible(False)
        title_box = labeled("Title", self.title_e, "required")
        title_box.layout().addWidget(self.title_err)
        right.addWidget(title_box)
        self.author_e = QLineEdit()
        self.author_e.setPlaceholderText("e.g. Albert Camus   /   آلبر کامو")
        right.addWidget(labeled("Author", self.author_e, "optional"))
        self.status_c = SegmentedChoice(STATUSES)
        self.status_box = labeled("Status", self.status_c)
        right.addWidget(self.status_box)

        # reading group: rating + dates (not used for wishlist books)
        self.reading_box = QWidget()
        rb = QVBoxLayout(self.reading_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(16)
        rb.addWidget(divider())
        self.stars = StarRating()
        self.rating_lbl = QLabel("Not rated", objectName="muted")
        srow_w = QWidget()
        srow = QHBoxLayout(srow_w)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(12)
        srow.addWidget(self.stars)
        srow.addWidget(self.rating_lbl)
        srow.addStretch()
        rb.addWidget(labeled("Rating", srow_w))
        self.started = DateField("Started")
        self.finished = DateField("Finished")
        self.date_hint = QLabel("")
        self.date_hint.setWordWrap(True)
        dates_w = QWidget()
        dv = QVBoxLayout(dates_w)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(8)
        dv.addWidget(self.started)
        dv.addWidget(self.finished)
        dv.addWidget(self.date_hint)
        self.dates_box = labeled("Reading dates", dates_w)
        rb.addWidget(self.dates_box)
        right.addWidget(self.reading_box)

        right.addWidget(divider())
        self.notes = QTextEdit()
        self.notes.setFixedHeight(92)
        self.notes.setPlaceholderText("Where to buy, price, edition..." if wish
                                      else "Thoughts, quotes, why you liked it...")
        right.addWidget(labeled("Notes", self.notes, "optional"))
        right.addStretch()
        bl.addLayout(right, 1)
        root.addWidget(self.scroll, 1)

        # ---- sticky footer: destructive action on the left, Cancel + main action on the right
        root.addWidget(divider())
        foot_w = QWidget()
        row = QHBoxLayout(foot_w)
        row.setContentsMargins(28, 14, 28, 16)
        row.setSpacing(10)
        self.delete_btn = self.bought_btn = None
        if book:
            self.delete_btn = QPushButton("Delete", objectName="danger")
            self.delete_btn.clicked.connect(self._delete)
            row.addWidget(self.delete_btn)
            if wish:
                self.bought_btn = QPushButton("Bought — move to library", objectName="ghost")
                self.bought_btn.clicked.connect(self._bought)
                row.addWidget(self.bought_btn)
        row.addStretch()
        self.cancel_btn = QPushButton("Cancel", objectName="ghost")
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Add to wishlist" if wish and not book
                                    else "Add book" if not book else "Save changes",
                                    objectName="primary")
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setToolTip("Ctrl+Enter")
        row.addWidget(self.cancel_btn)
        row.addWidget(self.save_btn)
        root.addWidget(foot_w)

        # Enter saves (but never deletes or cancels by accident); Ctrl+Enter also works in Notes
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        self.save_btn.setAutoDefault(True)
        self.save_btn.setDefault(True)
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, self._save)

        # ---- load the book being edited
        if book:
            _, t, a, s, r, n, self.cover_path, started, finished = book
            self.title_e.setText(t)
            self.author_e.setText(a or "")
            self.status_c.setCurrentText(s)
            self.stars.setValue(r or 0)
            self.notes.setPlainText(n or "")
            self.started.set_iso(started)
            self.finished.set_iso(finished)

        # ---- behaviour
        self.stars.changed.connect(self.update_rating_label)
        self.status_c.currentTextChanged.connect(self.status_changed)
        self.status_c.currentTextChanged.connect(self.refresh_hint)   # after status_changed
        self.status_c.currentTextChanged.connect(self.update_dates_visibility)
        for d in (self.started, self.finished):
            d.changed.connect(self.refresh_hint)
            d.changed.connect(self.update_dates_visibility)
        self.title_e.textChanged.connect(self.on_title_changed)
        if wish:                                    # a wishlist book has no status, rating or dates
            self.status_box.hide()
            self.reading_box.hide()
        self.update_rating_label()
        self.refresh_hint()
        self.update_dates_visibility()
        self.show_cover()
        self.check_title()

        # keyboard order follows the visual order
        chain = [self.title_e, self.author_e]
        if not wish:
            chain += list(self.status_c.btns.values()) + [self.stars]
            chain += self.started.focus_chain() + self.finished.focus_chain()
        chain += [self.notes, self.find_btn, self.more_btn]
        chain += [b for b in (self.delete_btn, self.bought_btn) if b]
        chain += [self.cancel_btn, self.save_btn]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)
        self.title_e.setFocus()
        self.mark_clean()                           # what the form looks like before the person touches it

    # -- behaviour
    def status_changed(self, status):
        """Convenience: 'Reading' fills today's start date, 'Read' today's finish date (if empty).
        Dates filled this way are removed again when the status changes, unless the person edited them."""
        for key, field in (("started", self.started), ("finished", self.finished)):
            if self._auto.get(key) and field.iso() == self._auto[key]:
                field.set_iso(None)                     # still the value we filled in: take it back
            self._auto[key] = None
        if status == "Reading" and not self.started.iso():
            self.started.set_iso(date.today().isoformat())
            self._auto["started"] = self.started.iso()
        elif status == "Read" and not self.finished.iso():
            self.finished.set_iso(date.today().isoformat())
            self._auto["finished"] = self.finished.iso()

    def update_dates_visibility(self, *_):
        """Show only the dates that make sense for the status. A date that already has a value is
        never hidden, so nothing is ever silently kept out of sight."""
        if self.wish:
            return
        status = self.status_c.currentText()
        show_s = status in ("Reading", "Read") or bool(self.started.iso())
        show_f = status == "Read" or bool(self.finished.iso())
        self.started.setVisible(show_s)
        self.finished.setVisible(show_f)
        self.dates_box.setVisible(show_s or show_f)

    def update_rating_label(self, *_):
        v = self.stars.value
        self.rating_lbl.setText(f"{v} of 5" if v else "Not rated")

    def refresh_hint(self, *_):
        """Live consistency check between status and dates (see check_dates)."""
        if self.wish:
            self.date_hint.hide()
            return
        found = check_dates(self.status_c.currentText(), self.started.iso(), self.finished.iso())
        hard = [m for c, m in found if c not in SOFT_CHECKS]
        soft = [m for c, m in found if c in SOFT_CHECKS]
        self.date_hint.setText("\n".join(hard + soft))
        self.date_hint.setStyleSheet(
            "color:#e0526a; background:rgba(224,82,106,0.12); border-radius:8px; padding:8px 10px;"
            if hard else f"color:{T['muted']};")
        self.date_hint.setVisible(bool(found))

    def on_title_changed(self, *_):
        if not self.cover_path:
            self.cover_prev.set_title(self.title_e.text())
        self.check_title()

    def set_title_error(self, message):
        self.title_err.setText(message)
        self.title_err.setVisible(bool(message))
        set_invalid(self.title_e, bool(message))

    def check_title(self, required=False):
        """Inline check next to the field (no pop-up). True when the title can be saved."""
        t = self.title_e.text().strip()
        msg = ""
        if not t:
            msg = "Enter the book’s title." if required else ""
        elif self.wish and self.store.wish_exists(t, self.book_id):
            msg = "This book is already on your wishlist."
        elif not self.wish and self.store.exists(t, self.book_id):
            msg = "A book with this title is already in your library."
        self.set_title_error(msg)
        return bool(t) and not msg

    def show_cover(self):
        self.cover_prev.set_cover(get_pixmap(self.cover_path), self.title_e.text())
        self.act_remove.setEnabled(bool(self.cover_path))

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a cover image", "",
                                              "Images (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        COVERS_DIR.mkdir(exist_ok=True)
        data = Path(path).read_bytes()
        dest = COVERS_DIR / (hashlib.md5(data).hexdigest() + Path(path).suffix.lower())
        shutil.copyfile(path, dest)
        self.cover_path = str(dest)
        self._cover_idle()
        self.show_cover()

    def remove_cover(self):
        self.cover_path = None
        self._cover_idle()
        self.show_cover()

    def _cover_idle(self, message=""):
        self.cover_gen += 1                       # a lookup still running is now stale
        self.find_btn.setEnabled(True)
        self.find_btn.setText("Find cover")
        self.link_go.setEnabled(True)
        self.link_go.setText("Get cover")
        self.cover_msg.setText(message)

    def find_cover_online(self):
        title = self.title_e.text().strip()
        if not title:
            self.title_e.setFocus()
            self.cover_msg.setText("Type the title first.")
            return
        self._cover_idle()
        self.find_btn.setEnabled(False)
        self.find_btn.setText("Searching…")
        job = Job(("findcover", self.cover_gen), find_cover_for, title, self.author_e.text().strip())
        job.signals.done.connect(self._cover_found)
        job.signals.failed.connect(self._cover_failed)
        QThreadPool.globalInstance().start(job)

    def show_link_popover(self):
        clip = QApplication.clipboard().text()          # a link copied just before: use it
        if not self.link_e.text() and clip:
            try:
                taaghche_cover_url(clip)
                self.link_e.setText(clip.strip())
            except ValueError:
                pass
        self.link_pop.show_below(self.more_btn)

    def cover_from_link(self):
        try:
            url = taaghche_cover_url(self.link_e.text())
        except ValueError as e:
            self.link_pop.show_error(str(e))            # the message stays in the panel, next to the box
            return
        self.link_pop.hide()
        self._cover_idle("Loading the cover…")
        self.link_go.setEnabled(False)
        self.link_go.setText("Loading…")
        job = Job(("linkcover", self.cover_gen), fetch_cover_checked, url)
        job.signals.done.connect(self._cover_found)
        job.signals.failed.connect(self._cover_failed)
        QThreadPool.globalInstance().start(job)

    def _cover_found(self, tag, path):
        if tag[1] != self.cover_gen:
            return
        self._cover_idle("Found. Press Save to keep it, or Cancel to discard.")
        self.cover_path = path
        self.show_cover()

    def _cover_failed(self, tag, message):
        if tag[1] != self.cover_gen:
            return
        if tag[0] == "linkcover":
            self._cover_idle("That link has no usable cover image." if message in ("no usable cover", "no cover")
                             else "There is no cover at that address — check the link (server error 404)."
                             if "404" in message else f"Couldn’t download the cover — {message}.")
            return
        if message in ("no cover found", "no usable cover"):
            self._cover_idle("No cover found online. Check the title and author, "
                             "or choose an image file.")
        else:
            self._cover_idle(f"Couldn’t search — {message}.")

    # -- unsaved changes: Cancel, Esc and the window's close button all end up in reject()
    def _state(self):
        return self.wish_values() if self.wish else self.values()

    def mark_clean(self):
        """Remembers the current form as 'nothing changed yet'. The window calls it again after it
        pre-selects a status, so a Cancel right after that does not ask anything."""
        self._clean = self._state()

    def is_dirty(self):
        return self._state() != self._clean

    def _ask_discard(self):
        """'save', 'discard' or 'keep'. Enter and Esc both mean 'keep editing' (the safe choice)."""
        adding = self.book is None
        box = QMessageBox(QMessageBox.Question,
                          "Discard this new book?" if adding else "Discard your changes?",
                          "This book hasn’t been added yet." if adding
                          else "You have unsaved changes to this book.",
                          QMessageBox.NoButton, self)
        save = box.addButton(self.save_btn.text(), QMessageBox.AcceptRole)
        drop = box.addButton("Discard", QMessageBox.DestructiveRole)
        keep = box.addButton("Keep editing", QMessageBox.RejectRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(keep)
        box.exec()
        clicked = box.clickedButton()
        return "save" if clicked is save else "discard" if clicked is drop else "keep"

    def reject(self):
        if self.is_dirty():
            choice = self._ask_discard()
            if choice == "keep":
                return
            if choice == "save":
                self._save()                        # checks the form; closes only when it is fine
                return
        super().reject()

    def done(self, result):
        self.cover_gen += 1                       # ignore a lookup that finishes after closing
        super().done(result)

    def _bought(self):
        self.bought = True
        self._save()
        if self.result() != QDialog.Accepted:       # validation failed: it is not a purchase after all
            self.bought = False

    def _save(self):
        title = self.title_e.text().strip()
        if not self.check_title(required=True):     # the message is already shown under the field
            self.title_e.setFocus()
            self.scroll.ensureWidgetVisible(self.title_e)
            return
        if self.wish:
            if self.bought and self.store.exists(title):
                self.set_title_error("A book with this title is already in your library.")
                self.title_e.setFocus()
                return
            self.accept()
            return
        found = dict(check_dates(self.status_c.currentText(),
                                 self.started.iso(), self.finished.iso()))
        for code in ("order", "future"):                    # real mistakes: fix them first
            if code in found:                               # the red note under the dates says what
                self.scroll.ensureWidgetVisible(self.date_hint)
                return
        if "want_dates" in found:
            if QMessageBox.question(self, "Dates on a book you haven’t read",
                                    found["want_dates"] + "\n\nClear the dates and save?"
                                    ) != QMessageBox.Yes:
                return
            self.started.set_iso(None)
            self.finished.set_iso(None)
        elif "reading_finished" in found:
            box = QMessageBox(QMessageBox.Question, "Finish date on a book you are reading",
                              found["reading_finished"] + "\n\nWhat should the app do?",
                              QMessageBox.NoButton, self)
            mark = box.addButton("Mark as Read", QMessageBox.AcceptRole)
            clear = box.addButton("Clear the finish date", QMessageBox.DestructiveRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is mark:
                self.status_c.setCurrentText("Read")
            elif box.clickedButton() is clear:
                self.finished.set_iso(None)
            else:
                return
        self.accept()

    def _delete(self):
        if self.book:                               # no confirmation: the window offers Undo
            self.deleted = True
            self.accept()

    def wish_values(self):
        return (self.title_e.text().strip(), self.author_e.text().strip(),
                self.notes.toPlainText().strip(), self.cover_path)

    def values(self):
        return (self.title_e.text().strip(), self.author_e.text().strip(),
                self.status_c.currentText(), self.stars.value or None,
                self.notes.toPlainText().strip(), self.cover_path,
                self.started.iso(), self.finished.iso())


# ------------------------------------------------------- online search UI
class ResultRow(QFrame):
    add = Signal(object)

    def __init__(self, data, in_library):
        super().__init__(objectName="row")
        self.data = data
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 14, 10)
        lay.setSpacing(14)

        self.cover = QLabel(data["title"] if len(data["title"]) <= 40 else data["title"][:39] + "…")
        self.cover.setTextFormat(Qt.PlainText)
        self.cover.setWordWrap(True)
        self.cover.setFixedSize(62, 92)
        self.cover.setAlignment(Qt.AlignCenter)
        c1, c2 = cover_colors(data["title"])
        self.cover.setStyleSheet(
            "border-radius:6px; color:white; font-size:10px; font-weight:700; padding:4px;"
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {c1.name()},stop:1 {c2.name()});")
        lay.addWidget(self.cover)

        col = QVBoxLayout()
        col.setSpacing(3)
        title = QLabel(data["title"], objectName="rowtitle")
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        year = str(data["year"] or "")
        if year and data.get("year_is_edition"):
            year = f"ed. {year}"            # edition / ebook date, not the original publication
        meta = " · ".join(x for x in (data["author"], year, data.get("cover_publisher")) if x)
        sub = QLabel(meta or "Unknown author", objectName="muted")
        sub.setTextFormat(Qt.PlainText)
        sub.setWordWrap(True)
        col.addStretch()
        col.addWidget(title)
        col.addWidget(sub)
        col.addStretch()
        lay.addLayout(col, 1)

        self.btn = QPushButton("In library" if in_library else "Add", objectName="primary")
        self.btn.setEnabled(not in_library)
        self.btn.setFixedWidth(96)
        self.btn.clicked.connect(lambda: self.add.emit(self))
        lay.addWidget(self.btn)

    def set_preview(self, source):
        """source: a QImage (downloaded preview) or a file path (the library's own cover). It is only
        shown here: the book's real cover is fetched when the book is added."""
        pm = QPixmap.fromImage(source) if isinstance(source, QImage) else QPixmap(source)
        if pm.isNull():
            return
        self.cover.setStyleSheet("background:transparent;")
        self.cover.setPixmap(pm.scaled(62, 92, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def mark_added(self, text="Added"):
        self.btn.setText(text)
        self.btn.setEnabled(False)


class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        cfg = load_settings()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        head = QLabel("Settings")
        head.setObjectName("h1")
        lay.addWidget(head)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.cal = QComboBox()
        self.cal.addItems(["Gregorian", "Jalali (شمسی)"])
        self.cal.setCurrentIndex(1 if cfg.get("calendar") == "jalali" else 0)
        self.email = QLineEdit(cfg.get("contact_email", ""))
        self.email.setPlaceholderText("you@example.com")
        self.gkey = QLineEdit(cfg.get("google_api_key", ""))
        self.gkey.setPlaceholderText("optional")
        form.addRow("Calendar", self.cal)
        form.addRow("Your email", self.email)
        form.addRow("Google Books key", self.gkey)
        lay.addLayout(form)
        hint = QLabel("Calendar: how dates are shown and entered (they are stored the same way "
                      "either way). The email and the Google key are only for online search (the email is sent to "
                      "Open Library only): Open Library asks apps to identify themselves with a contact email, which "
                      "gives you faster and more reliable searches. Google Books needs a free API "
                      "key, otherwise it quickly answers “too many requests”. Both are optional.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel", objectName="ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save", objectName="primary")
        save.clicked.connect(self.save)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)

    def save(self):
        cfg = load_settings()
        cfg.update(calendar="jalali" if self.cal.currentIndex() == 1 else "gregorian",
                   contact_email=self.email.text().strip(),
                   google_api_key=self.gkey.text().strip())
        save_settings(cfg)
        CAL["mode"] = cfg["calendar"]
        self.accept()


class BarChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(210)
        self.values, self.labels = [0] * 12, [""] * 12

    def set_data(self, values, labels):
        self.values, self.labels = values, labels
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        top, bottom = 24, 28
        slot = w / len(self.values)
        bar_w = min(slot * 0.58, 34)
        vmax = max(max(self.values), 1)
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)
        fm = QFontMetrics(f)
        p.setPen(QPen(QColor(T["border"]), 1))
        p.drawLine(0, h - bottom, w, h - bottom)
        for i, v in enumerate(self.values):
            x = i * slot + (slot - bar_w) / 2
            bh = (h - top - bottom) * v / vmax
            p.setPen(Qt.NoPen)
            if v:
                rect = QRectF(x, h - bottom - bh, bar_w, bh)
                p.setBrush(QColor(T["accent"]))
                p.drawRoundedRect(rect, 6, 6)
                p.setPen(QColor(T["text"]))
                p.drawText(QRectF(i * slot, rect.top() - 18, slot, 16), Qt.AlignCenter, str(v))
            else:
                p.setBrush(QColor(T["border"]))
                p.drawRoundedRect(QRectF(x, h - bottom - 3, bar_w, 3), 1.5, 1.5)
            p.setPen(QColor(T["muted"]))
            p.drawText(QRectF(i * slot, h - bottom + 5, slot, 18), Qt.AlignCenter,
                       fm.elidedText(self.labels[i], Qt.ElideRight, int(slot) - 2))


class StatsDialog(QDialog):
    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Reading statistics")
        self.resize(660, 780)
        self.rows = store.finished_rows()          # title, author, rating, started, finished
        this_year = year_of(date.today().isoformat())
        years = sorted({year_of(r[4]) for r in self.rows} | {this_year}, reverse=True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)
        top = QHBoxLayout()
        head = QLabel("Reading statistics")
        head.setObjectName("h1")
        top.addWidget(head)
        top.addStretch()
        self.year_c = QComboBox()
        self.year_c.addItems([fa_num(y) for y in years])
        top.addWidget(self.year_c)
        lay.addLayout(top)

        goal_row = QHBoxLayout()
        goal_row.addWidget(QLabel("Yearly goal"))
        self.goal = NumberEdit(0, 999)
        self.goal.setFixedWidth(80)
        self.goal.setValue(int(load_settings().get("goal", 24)))
        goal_row.addWidget(self.goal)
        goal_row.addWidget(QLabel("books"))
        goal_row.addStretch()
        self.progress_lbl = QLabel("")
        goal_row.addWidget(self.progress_lbl)
        lay.addLayout(goal_row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        lay.addWidget(self.bar)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card = {}
        for key, caption in (("n", "Books finished"), ("avg", "Average rating"),
                             ("days", "Days per book")):
            box = QFrame(objectName="row")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(16, 12, 16, 12)
            big = QLabel("0", objectName="bignum")
            bl.addWidget(big)
            bl.addWidget(QLabel(caption, objectName="muted"))
            self.card[key] = big
            cards.addWidget(box)
        lay.addLayout(cards)

        lay.addWidget(QLabel("Books finished per month", objectName="muted"))
        self.chart = BarChart()
        lay.addWidget(self.chart)
        self.chart_note = QLabel("", objectName="muted")
        self.chart_note.setWordWrap(True)
        lay.addWidget(self.chart_note)
        lay.addWidget(QLabel("Most-read authors", objectName="muted"))
        self.authors = QLabel("")
        self.authors.setTextFormat(Qt.RichText)
        self.authors.setWordWrap(True)
        lay.addWidget(self.authors)
        self.note = QLabel("", objectName="muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        lay.addStretch()
        done = QPushButton("Done", objectName="ghost")
        done.clicked.connect(self.accept)
        lay.addWidget(done, alignment=Qt.AlignRight)

        self.year_c.currentIndexChanged.connect(self.refresh)
        self.goal.valueChanged.connect(self.goal_changed)
        self.refresh()

    def goal_changed(self, value):
        cfg = load_settings()
        cfg["goal"] = value
        save_settings(cfg)
        self.refresh()

    def refresh(self, *_):
        year = int(self.year_c.currentText().translate(_DIGITS))
        mine = [r for r in self.rows if year_of(r[4]) == year]
        n, goal = len(mine), self.goal.value()
        self.progress_lbl.setText(f"{n} of {goal} books · {round(100 * n / goal)}%" if goal
                                  else f"{n} books finished")
        self.bar.setVisible(bool(goal))
        self.bar.setRange(0, max(goal, 1))
        self.bar.setValue(min(n, max(goal, 1)))
        ratings = [r[2] for r in mine if r[2]]
        exact = lambda s: bool(s) and date_prec(s) == "d"
        days = [(date.fromisoformat(r[4]) - date.fromisoformat(r[3])).days       # exact dates only
                for r in mine if exact(r[3]) and exact(r[4]) and r[3] <= r[4]]
        self.card["n"].setText(str(n))
        self.card["avg"].setText(f"{sum(ratings) / len(ratings):.1f}" if ratings else "–")
        self.card["days"].setText(str(round(sum(days) / len(days))) if days else "–")
        months = [0] * 12
        year_only = 0
        for r in mine:
            if date_prec(r[4]) == "y":              # only the year is known: yearly, not monthly
                year_only += 1
            else:
                months[month_of(r[4]) - 1] += 1
        self.chart.set_data(months, month_names())
        self.chart_note.setText(
            f"+ {year_only} book{'s' if year_only != 1 else ''} with only the year known "
            f"(≈ {fa_num(year)}): counted in the yearly total and the goal, but not in this chart."
            if year_only else "")
        top = Counter((r[1] or "Unknown").split(",")[0].strip() for r in mine).most_common(5)
        self.authors.setText("<br>".join(f"{i}. \u2068{html.escape(a)}\u2069 — {c}" for i, (a, c) in enumerate(top, 1))
                             or "Nothing yet for this year.")
        missing = self.store.read_without_date()
        lines = []
        if year_only:
            lines.append("“Days per book” uses only books with exact start and finish dates.")
        if missing:
            lines.append(f"{missing} book{'s' if missing != 1 else ''} marked Read "
                         f"{'have' if missing != 1 else 'has'} no finish date, so "
                         f"{'they are' if missing != 1 else 'it is'} not counted. "
                         "Open the book, tick “Finished” and pick the date; if you only "
                         "remember the year, choose “Year only”.")
        self.note.setText("\n".join(lines))


class DataDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Data & backup")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        head = QLabel("Data & backup")
        head.setObjectName("h1")
        lay.addWidget(head)
        info = QLabel("A backup of your library (database + covers) is made automatically "
                      "once a day when the app starts; the newest 10 are kept in the "
                      f"“backups” folder next to the app.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.last = QLabel("", objectName="muted")
        lay.addWidget(self.last)
        for text, fn in (("Back up now", self.backup), ("Restore from a backup…", self.restore),
                         ("Open backup folder", self.open_folder),
                         ("Import from CSV…", lambda: win.import_csv(self)),
                         ("Export to CSV…", lambda: win.export_csv(self)),
                         ("Settings…", self.settings)):
            b = QPushButton(text, objectName="ghost")
            b.clicked.connect(lambda _=False, f=fn: f())
            lay.addWidget(b)
        lay.addWidget(QLabel(f"My Books · version {APP_VERSION}", objectName="muted"))
        done = QPushButton("Done", objectName="primary")
        done.clicked.connect(self.accept)
        lay.addWidget(done, alignment=Qt.AlignRight)
        self.refresh_last()

    def refresh_last(self):
        files = sorted(BACKUP_DIR.glob("books-*.zip")) if BACKUP_DIR.exists() else []
        self.last.setText(f"Latest backup: {files[-1].name}" if files else "No backup yet.")

    def backup(self):
        self.win.backup_now(self)
        self.refresh_last()

    def restore(self):
        self.win.restore_backup(self)
        self.refresh_last()

    def open_folder(self):
        BACKUP_DIR.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(BACKUP_DIR)))

    def settings(self):
        SettingsDialog(self).exec()
        self.win.refresh()


class DiscoverDialog(QDialog):
    changed = Signal()

    def __init__(self, parent, store, query=""):
        super().__init__(parent)
        self.store = store
        self.gen = 0          # bumped on every search so stale results are ignored
        self.cover_specs = []                 # per result row: (function, args) that fetches its cover, or None
        self.cover_started = set()            # rows whose cover download has been started
        self.cover_timer = QTimer(self)       # waits until scrolling stops before downloading
        self.cover_timer.setSingleShot(True)
        self.cover_timer.setInterval(200)
        self.cover_timer.timeout.connect(self.load_visible_covers)
        self.rows = []
        self.items, self.notes, self.info = [], (), ""
        self.mode = "title"
        self.setWindowTitle("Find books online")
        self.resize(660, 720)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)
        head = QLabel("Find books online")
        head.setObjectName("h1")
        lay.addWidget(head)

        modes = QHBoxLayout()
        group = QButtonGroup(self)
        self.mode_btns = {}
        for key, label in (("title", "Book title"), ("author", "Author")):
            b = QPushButton(label, objectName="seg")
            b.setCheckable(True)
            b.setChecked(key == "title")
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            group.addButton(b)
            modes.addWidget(b)
        modes.addStretch()
        self.provider = QComboBox()
        self.provider.addItems(SOURCES)
        modes.addWidget(self.provider)
        gear = QPushButton("Settings", objectName="ghost")
        gear.clicked.connect(lambda: SettingsDialog(self).exec())
        modes.addWidget(gear)
        lay.addLayout(modes)

        bar = QHBoxLayout()
        self.q = QLineEdit(query)
        self.q.returnPressed.connect(self.search)
        go = QPushButton("Search", objectName="primary")
        go.clicked.connect(self.search)
        bar.addWidget(self.q, 1)
        bar.addWidget(go)
        lay.addLayout(bar)
        pubrow = QHBoxLayout()
        pubrow.addWidget(QLabel("Cover from publisher"))
        self.pub = QComboBox()
        self.pub.setEditable(True)                      # type a name, or pick one seen in the results
        self.pub.setInsertPolicy(QComboBox.NoInsert)
        self.pub.lineEdit().setPlaceholderText("any publisher (Persian searches), e.g. نگاه")
        self.pub.lineEdit().setStyleSheet("background:transparent; border:none; padding:0;")
        self.pub.lineEdit().returnPressed.connect(self.search)
        self.pub.activated.connect(lambda *_: self.search())
        pubrow.addWidget(self.pub, 1)
        lay.addLayout(pubrow)
        self.publisher = ""                             # the one the current results were made for
        self.set_mode("title")

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.hide_noise = CheckBox("Hide study guides, summaries and adaptations")
        self.hide_noise.setChecked(True)
        self.hide_noise.toggled.connect(self.render)
        lay.addWidget(self.hide_noise)
        self.show_covers = CheckBox("Show covers in the results (a small picture for each visible result)")
        self.show_covers.setChecked(bool(load_settings().get("show_covers", False)))
        self.show_covers.toggled.connect(self.covers_toggled)
        lay.addWidget(self.show_covers)

        self.results = QListWidget()
        self.results.setSelectionMode(QAbstractItemView.NoSelection)
        self.results.setSpacing(5)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.results.verticalScrollBar().valueChanged.connect(lambda *_: self.cover_timer.start())
        lay.addWidget(self.results, 1)

        foot = QHBoxLayout()
        foot.addWidget(QLabel("Add new books as"))
        self.status_c = QComboBox()
        self.status_c.addItems(STATUSES + [WISH])
        self.status_c.setCurrentText("Want to read")
        foot.addWidget(self.status_c)
        foot.addStretch()
        close = QPushButton("Done", objectName="ghost")
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)

        if query:
            self.search()

    def set_mode(self, key):
        self.mode = key
        self.q.setPlaceholderText("e.g. The Stranger by Camus   /   بیگانه" if key == "title"
                                  else "e.g. James Clear   /   آلبر کامو")

    def search(self):
        text = self.q.text().strip()
        if not text:
            return
        self.gen += 1
        self.results.clear()
        self.rows = []
        self.status.setText("Searching…")
        self.publisher = self.pub.currentText().strip()
        job = Job(("search", self.gen), search_books, text, self.mode,
                  self.provider.currentText(), self.publisher)
        job.signals.done.connect(self.job_done)
        job.signals.failed.connect(self.job_failed)
        QThreadPool.globalInstance().start(job)

    def job_done(self, tag, result):
        if tag[1] != self.gen:
            return
        if tag[0] == "search":
            self.show_results(*result)
        elif tag[0] == "cover" and tag[2] < len(self.rows):
            self.rows[tag[2]].set_preview(result)

    def job_failed(self, tag, message):
        if tag[0] == "search" and tag[1] == self.gen:
            self.status.setText(f"Couldn’t get results — {message}.\n"
                                "Try “All sources” or another source.")

    def show_results(self, items, notes=(), info=""):
        self.items, self.notes, self.info = items, notes, info
        seen = Counter(pb for d in items for pb in d.get("publishers", []))
        typed = self.pub.currentText()
        self.pub.blockSignals(True)                     # suggestions: publishers in these results
        self.pub.clear()
        self.pub.addItems([pb for pb, _ in seen.most_common(40)])
        self.pub.setCurrentText(typed)
        self.pub.blockSignals(False)
        self.render()

    def render(self, *_):
        shown = [d for d in self.items if not (self.hide_noise.isChecked() and d.get("noise"))]
        hidden = len(self.items) - len(shown)
        self.gen += 1                      # cover jobs of the previous list are now stale
        self.results.clear()
        self.rows = []
        self.cover_specs, self.cover_started = [], set()
        extra = ("\n" + self.info if self.info else "") + \
            "".join(f"\nUnavailable — {n}" for n in self.notes)
        if not shown:
            self.status.setText(("No results. Try another spelling, or another source."
                                 if not self.items else f"{hidden} hidden (study guides etc.)")
                                + extra)
            return
        what = "books by this author" if self.mode == "author" else "matching books"
        sources = ", ".join(dict.fromkeys(d["source"] for d in shown))
        hid = f" ({hidden} study guides/adaptations hidden)" if hidden else ""
        if self.publisher and any(d.get("publishers") for d in shown):
            hits = sum(1 for d in shown if d.get("pub_hit"))
            extra += (f"\nCover from “\u2068{self.publisher}\u2069”: {hits} of {len(shown)} have an edition "
                      "from this publisher; the others keep the default cover.")
        self.status.setText(f"{len(shown)} {what} · {sources}{hid}{extra}")
        for i, d in enumerate(shown):
            in_library = self.store.exists(d["title"])
            row = ResultRow(d, in_library)
            row.add.connect(self.add_book)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 116))
            self.results.addItem(item)
            self.results.setItemWidget(item, row)
            self.rows.append(row)
            spec = None
            if self.show_covers.isChecked():
                own = self.store.cover_of(d["title"]) if in_library else None
                if own and get_pixmap(own):
                    row.set_preview(str(COVERS_DIR / own))       # already in the library: no download
                else:
                    spec = self.preview_spec(d)
            self.cover_specs.append(spec)
        QTimer.singleShot(0, self.load_visible_covers)      # once the list has been laid out

    @staticmethod
    def cover_spec(d):
        """(function, args) that downloads this result's cover, or None when it has no cover source."""
        if d.get("cover_urls"):
            return fetch_best_cover, (d["cover_urls"],)
        if d.get("cover_query") and d["cover_query"][0]:
            return find_cover, tuple(d["cover_query"])
        return None

    def covers_toggled(self, on):
        cfg = load_settings()
        cfg["show_covers"] = bool(on)
        save_settings(cfg)
        if self.items:
            self.render()

    @staticmethod
    def preview_spec(d):
        """(pool, function, args) that makes this result's small preview, or None without a cover source."""
        if d.get("cover_urls"):
            return COVER_POOL, fetch_preview, (d["cover_urls"][0],)
        if d.get("cover_query") and d["cover_query"][0]:
            return LOOKUP_POOL, find_preview, tuple(d["cover_query"])      # needs a search: one at a time
        return None

    def load_visible_covers(self):
        """Downloads the covers only for the rows on screen (plus a little ahead), so a search with
        50 results does not fetch 50 pictures nobody looks at. Called after a search, and 0.2 s after
        scrolling stops, so scrolling quickly past rows downloads nothing."""
        view = self.results.viewport().rect().adjusted(0, -116, 0, 2 * 116)
        for i, spec in enumerate(self.cover_specs):
            if spec is None or i in self.cover_started or i >= self.results.count():
                continue
            if not view.intersects(self.results.visualItemRect(self.results.item(i))):
                continue
            self.cover_started.add(i)
            pool, fn, args = spec
            job = Job(("cover", self.gen, i), self._cover_job, self.gen, fn, *args)
            job.signals.done.connect(self.job_done)
            pool.start(job)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.cover_timer.start()                            # a taller window shows more rows

    def _cover_job(self, gen, fn, *args):
        """Runs on a worker thread. A job that has not started yet when the list changed or the
        dialog closed does nothing, so no picture is downloaded for results nobody sees any more."""
        if self.gen != gen:
            raise ValueError("cancelled")
        return fn(*args)

    def done(self, result):
        self.gen += 1                               # cover jobs still waiting are cancelled
        self.cover_timer.stop()
        self.cover_specs = []
        super().done(result)

    def add_book(self, row):
        d = row.data
        if self.status_c.currentText() == WISH:
            if self.store.wish_exists(d["title"]):
                row.mark_added("In wishlist")
                return
            new_id = self.store.wish_add(d["title"], d["author"], "", None)
            row.mark_added("Wishlist")
            wish = True
        else:
            if self.store.exists(d["title"]):
                row.mark_added("In library")
                return
            new_id = self.store.add(d["title"], d["author"], self.status_c.currentText(),
                                    None, "")
            row.mark_added()
            wish = False
        self.changed.emit()
        spec = self.cover_spec(d)
        if spec:                                    # the real cover is fetched now, only for this book
            COVER_ATTACH["n"] += 1
            job = Job(("attach", new_id, wish), *spec[:1], *spec[1])
            job.signals.done.connect(self._cover_attached)
            job.signals.failed.connect(self._cover_attach_failed)
            COVER_POOL.start(job)

    def _cover_attached(self, tag, path):
        COVER_ATTACH["n"] -= 1
        _, new_id, wish = tag
        try:
            self.store.set_cover(new_id, wish, path)
        except Exception:
            return
        self.changed.emit()

    def _cover_attach_failed(self, tag, message):
        COVER_ATTACH["n"] -= 1                      # no cover found: the book stays as it is


class ShelfArt(QWidget):
    """Little shelf with a few books, drawn with QPainter in the status colours of the chips.
    Uses the theme colours only for the backdrop and the shelf, so it fits dark and light."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(200, 130)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        base = 108
        accent = QColor(T["accent"])
        accent.setAlpha(28)
        p.setBrush(accent)
        p.drawEllipse(QPointF(100, 62), 62, 62)
        shelf = QColor(T["muted"])
        shelf.setAlpha(120)
        p.setBrush(shelf)
        p.drawRoundedRect(QRectF(28, base, 144, 5), 2.5, 2.5)

        def book(rect, key):
            p.setBrush(QColor(STATUS_COLORS[key]))
            p.drawRoundedRect(rect, 3, 3)
            p.setBrush(QColor(255, 255, 255, 70))                      # two bands on the spine
            p.drawRect(QRectF(rect.left(), rect.top() + 10, rect.width(), 3))
            p.drawRect(QRectF(rect.left(), rect.bottom() - 13, rect.width(), 3))

        for x, w, h, key in ((44, 20, 66, "Read"), (66, 16, 54, "Reading"),
                             (84, 22, 72, "Want to read"), (108, 17, 58, WISH)):
            book(QRectF(x, base - h, w, h), key)
        p.save()                                                         # one book leans on the others
        p.translate(160, base)
        p.rotate(-14)
        book(QRectF(-18, -58, 18, 58), "Reading")
        p.restore()


class EmptyState(QWidget):
    """What the book area shows when there is nothing to list: picture, a short title and sentence,
    and up to three buttons (the first one is the main action). Window.show_empty fills it in."""
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addStretch(3)
        lay.addWidget(ShelfArt(), alignment=Qt.AlignHCenter)
        lay.addSpacing(6)
        self.title = QLabel("", objectName="h1")
        self.title.setTextFormat(Qt.PlainText)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        lay.addWidget(self.title, alignment=Qt.AlignHCenter)
        self.sub = QLabel("", objectName="muted")
        self.sub.setTextFormat(Qt.PlainText)
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub, alignment=Qt.AlignHCenter)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()
        self.btns, self._cbs = [], [None, None, None]
        for i, name in enumerate(("primary", "ghost", "ghost")):
            b = QPushButton(objectName=name)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self._cbs[i] and self._cbs[i]())
            row.addWidget(b)
            self.btns.append(b)
        row.addStretch()
        lay.addLayout(row)
        lay.addStretch(4)

    def set_state(self, title, sub, actions):
        """actions: [(button text, callback)], at most three."""
        self.title.setText(title)
        self.sub.setText(sub)
        for label, width in ((self.title, 460), (self.sub, 400)):   # wrapped labels need their height set
            label.setFixedWidth(width)
            label.setFixedHeight(label.heightForWidth(width))
        for i, b in enumerate(self.btns):
            b.setVisible(i < len(actions))
            self._cbs[i] = actions[i][1] if i < len(actions) else None
            if i < len(actions):
                b.setText(actions[i][0])


class Toast(QFrame):
    """Small message that appears at the bottom of its host window and fades away by itself.
    Replaces the pop-up boxes for success messages (nothing to click away) and carries the Undo
    button after a delete. One Toast per host; a new message replaces the old one."""
    closed = Signal()

    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("toast")
        self.host = host
        self._cb = None                       # action button callback
        self._hold = False                    # True while the action is an Undo for deleted data
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 10, 10, 10)
        lay.setSpacing(10)
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)          # long messages (import result) wrap in a narrow dialog
        self.btn = QPushButton(objectName="toastbtn")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._act)
        lay.addWidget(self.label)
        lay.addWidget(self.btn)
        self.fx = QGraphicsOpacityEffect(self)
        self.fx.setOpacity(0.0)
        self.setGraphicsEffect(self.fx)
        self.anim = QPropertyAnimation(self.fx, b"opacity", self)
        self.anim.setDuration(160)
        self.anim.finished.connect(self._anim_done)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        host.installEventFilter(self)
        self.hide()

    def has_pending(self):
        """True while an Undo is on offer (deleted data may still come back)."""
        return self._hold and self.isVisible()

    def popup(self, text, action=None, callback=None, ms=4500, pending=False):
        had = self._hold
        self._cb, self._hold = (callback if action else None), bool(action and pending)
        self.btn.setText(action or "")
        self.btn.setVisible(bool(action))
        avail = max(160, min(self.host.width() - 120, 560))
        fm = self.label.fontMetrics()
        w = min(fm.horizontalAdvance(text) + 4, avail)
        h = fm.boundingRect(0, 0, w, 10000, Qt.TextWordWrap, text).height()
        self.label.setText(text)
        self.label.setFixedSize(w, h)
        self.adjustSize()
        self.place()
        self.raise_()
        self.show()
        self._fade(1.0)
        self.timer.start(ms)
        if had and not self._hold:
            self.closed.emit()                # an Undo offer was replaced by a plain message

    def place(self):
        self.move(max(8, (self.host.width() - self.width()) // 2), self.host.height() - self.height() - 24)

    def eventFilter(self, obj, e):
        if obj is self.host and e.type() == QEvent.Resize and self.isVisible():
            self.adjustSize()
            self.place()
        return False

    def _act(self):
        cb, self._cb = self._cb, None
        self.dismiss()
        if cb:
            cb()

    def dismiss(self):
        self.timer.stop()
        self._cb = None
        self._fade(0.0)

    def _fade(self, to):
        self.anim.stop()
        self.anim.setStartValue(self.fx.opacity())
        self.anim.setEndValue(to)
        self.anim.start()

    def _anim_done(self):
        if self.fx.opacity() == 0.0 and self.isVisible():
            self.hide()
            self._hold = False
            self.closed.emit()


def toast(host, text, action=None, callback=None, ms=4500, pending=False):
    """Shows a Toast on `host` (a window or dialog); the Toast is created on first use."""
    t = getattr(host, "_toast", None)
    if t is None:
        t = host._toast = Toast(host)
    t.popup(text, action, callback, ms, pending)
    return t


def card_tooltip(row):
    """Full title, author and the start of the notes: the card itself cuts long text."""
    _id, title, author, status, rating, notes, _c, started, finished = row
    def block(text, bold=False):
        t = html.escape(text).replace("\n", "<br>")
        t = f"<b>{t}</b>" if bold else t
        return f"<div dir='{'rtl' if is_rtl(text) else 'ltr'}'>{t}</div>"
    parts = [block(title, True)]
    if author:
        parts.append(block(author))
    note = (notes or "").strip()
    if note:
        parts.append(block(note if len(note) <= 240 else note[:239] + "…"))
    return "".join(parts)


# -------------------------------------------------------------- window
def TOAST_ACCENT():
    """The toast is the inverse of the theme, so its button uses the OTHER theme's accent colour."""
    return THEMES["light" if T["bg"] == THEMES["dark"]["bg"] else "dark"]["accent"]


def make_qss():
    return f"""
* {{ color:{T['text']}; }}
QMainWindow, QDialog, #root {{ background:{T['bg']}; }}
QDialog {{ background:{T['card']}; }}
#sidebar {{ background:{T['side']}; border-right:1px solid {T['border']}; }}
#logo {{ font-size:20px; font-weight:800; }}
#h1 {{ font-size:20px; font-weight:700; }}
#muted {{ color:{T['muted']}; }}
QLineEdit, QTextEdit, QComboBox {{
    background:{T['input']}; border:1px solid transparent; border-radius:10px; padding:9px 12px;
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border:1px solid {T['accent']}; }}
QComboBox::drop-down {{ border:none; width:24px; }}
QComboBox QAbstractItemView {{ background:{T['card']}; border:1px solid {T['border']};
    selection-background-color:{T['accent']}; selection-color:white; outline:none; }}
QPushButton {{ border:none; border-radius:10px; padding:10px 18px; font-weight:600; }}
QPushButton#primary {{ background:{T['accent']}; color:white; }}
QPushButton#primary:hover {{ background:{QColor(T['accent']).lighter(114).name()}; }}
QPushButton#primary:pressed {{ background:{QColor(T['accent']).darker(112).name()}; }}
QPushButton#ghost {{ background:{T['input']}; }}
QPushButton#ghost:hover {{ border:1px solid {T['accent']}; }}
QPushButton#ghost:focus {{ border:1px solid {T['accent']}; }}
QPushButton#dangerfill {{ background:#e0526a; color:white; }}
QPushButton#dangerfill:hover {{ background:#e96a80; }}
QPushButton#dangerfill:pressed {{ background:#c9435a; }}
QPushButton#danger {{ background:transparent; color:#e0526a; }}
QPushButton#danger:hover {{ background:rgba(224,82,106,0.15); }}
QPushButton#nav {{ background:transparent; text-align:left; padding:11px 14px; color:{T['muted']}; }}
QPushButton#nav:hover {{ background:{T['input']}; }}
QPushButton#nav:checked {{ background:{T['accent']}; color:white; }}
QListWidget {{ background:transparent; border:none; outline:none; }}
QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{T['border']}; border-radius:4px; min-height:30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QMessageBox {{ background:{T['card']}; }}
QMessageBox QPushButton {{ background:{T['input']}; min-width:92px; padding:8px 16px; border:1px solid transparent; }}
QMessageBox QPushButton:hover {{ border:1px solid {T['accent']}; }}
QMessageBox QPushButton:default {{ background:{T['accent']}; color:white; }}
QMessageBox QPushButton:default:hover {{ background:{QColor(T['accent']).lighter(114).name()}; }}
QFrame#row {{ background:{T['bg']}; border:1px solid {T['border']}; border-radius:14px; }}
QFrame#row QLabel {{ background:transparent; }}
#rowtitle {{ font-size:15px; font-weight:700; }}
QPushButton#seg {{ background:{T['input']}; padding:9px 18px; }}
QPushButton#seg:checked {{ background:{T['accent']}; color:white; }}
QPushButton:disabled {{ background:{T['input']}; color:{T['muted']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color:{T['muted']}; }}
QProgressBar {{ background:{T['input']}; border:none; border-radius:7px; }}
QProgressBar::chunk {{ background:{T['accent']}; border-radius:7px; }}
#flabel {{ font-size:12px; font-weight:600; }}
#fnote {{ color:{T['muted']}; font-size:11px; }}
#ferror {{ color:#e0526a; font-size:11px; }}
QFrame#hline {{ background:{T['border']}; border:none; }}
QFrame#segtrack {{ background:{T['input']}; border-radius:12px; }}
QPushButton#segopt {{ background:transparent; color:{T['muted']}; padding:8px 12px; border-radius:9px; }}
QPushButton#segopt:hover:!checked {{ color:{T['text']}; }}
QPushButton#segopt:checked {{ background:{T['accent']}; color:white; }}
QPushButton#segopt:focus {{ border:1px solid {T['accent']}; }}
QLineEdit[invalid="true"], QLineEdit[invalid="true"]:focus {{ border:1px solid #e0526a; }}
QScrollArea {{ background:transparent; border:none; }}
#formbody {{ background:transparent; }}
QMenu {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:12px; padding:6px; }}
QMenu::item {{ padding:9px 16px; border-radius:8px; margin:1px 0; }}
QMenu::item:selected {{ background:{T['accent']}; color:white; }}
QMenu::item:disabled {{ color:{T['muted']}; }}
QMenu::separator {{ height:1px; background:{T['border']}; margin:5px 8px; }}
QPushButton::menu-indicator {{ image:none; width:0; }}
QFrame#popover {{ background:{T['card']}; border:1px solid {T['border']}; border-radius:12px; }}
#bignum {{ font-size:26px; font-weight:800; }}
QFrame#toast {{ background:{T['text']}; border-radius:12px; }}
QFrame#toast QLabel {{ color:{T['bg']}; background:transparent; }}
QPushButton#toastbtn {{ background:transparent; color:{TOAST_ACCENT()}; padding:6px 12px; border-radius:8px; font-weight:700; }}
QPushButton#toastbtn:hover {{ background:rgba(128,128,128,0.22); }}
QToolTip {{ background:{T['card']}; color:{T['text']}; border:1px solid {T['border']}; padding:6px 8px; }}
"""


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("My Books")
        self.store = Store()
        self.status = "All"
        cfg = load_settings()
        self.restore_size(cfg)
        self.theme = cfg.get("theme") if cfg.get("theme") in THEMES else "dark"   # remembered between runs
        self._tidy_pending = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # sidebar
        side = QFrame(objectName="sidebar")
        side.setFixedWidth(230)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 22, 16, 16)
        sl.setSpacing(6)
        logo = QLabel("My Books")
        logo.setObjectName("logo")
        sl.addWidget(logo)
        sl.addSpacing(16)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.nav = {}
        for name in ["All"] + STATUSES + [WISH]:
            if name == WISH:
                sl.addSpacing(10)
            b = QPushButton(objectName="nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.set_status(n))
            self.group.addButton(b)
            self.nav[name] = b
            sl.addWidget(b)
        self.nav["All"].setChecked(True)
        sl.addSpacing(14)
        self.stats = QLabel("")
        self.stats.setObjectName("muted")
        self.stats.setWordWrap(True)
        sl.addWidget(self.stats)
        sl.addSpacing(8)
        stats_btn = QPushButton("Statistics", objectName="ghost")
        stats_btn.clicked.connect(self.open_stats)
        sl.addWidget(stats_btn)
        sl.addStretch()
        data_btn = QPushButton("Data && backup", objectName="ghost")
        data_btn.clicked.connect(self.open_data)
        sl.addWidget(data_btn)
        self.theme_btn = QPushButton("Light mode", objectName="ghost")
        self.theme_btn.clicked.connect(self.toggle_theme)
        sl.addWidget(self.theme_btn)
        outer.addWidget(side)

        # main area
        main = QVBoxLayout()
        main.setContentsMargins(28, 24, 28, 12)
        main.setSpacing(16)
        top = QHBoxLayout()
        self.heading = QLabel("All books")
        self.heading.setObjectName("h1")
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        hb = QVBoxLayout()
        hb.setSpacing(2)
        hb.addWidget(self.heading)
        hb.addWidget(self.sub)
        top.addLayout(hb)
        top.addStretch()
        self.search = QLineEdit(placeholderText="Search title or author...")
        self.search.setFixedWidth(240)
        self.search_clear = self.search.addAction(clear_icon(), QLineEdit.TrailingPosition)
        self.search_clear.setToolTip("Clear")
        self.search_clear.setVisible(False)
        self.search_clear.triggered.connect(self.search.clear)
        self.search.textChanged.connect(lambda t: self.search_clear.setVisible(bool(t)))
        self.search.textChanged.connect(self.refresh)
        add = QPushButton("+  Add book", objectName="primary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self.add_book)
        self.sort_c = QComboBox()
        self.sort_c.addItems(list(SORTS))
        self.sort_c.setFixedWidth(150)
        if cfg.get("sort") in SORTS:
            self.sort_c.setCurrentText(cfg["sort"])           # before connecting: no refresh yet
        self.sort_c.currentIndexChanged.connect(self.sort_changed)
        top.addWidget(self.sort_c)
        top.addWidget(self.search)
        online = QPushButton("Find online", objectName="ghost")
        online.setCursor(Qt.PointingHandCursor)
        online.clicked.connect(lambda: self.open_discover(""))
        top.addWidget(online)
        top.addWidget(add)
        main.addLayout(top)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(4)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.delegate = BookDelegate(self.list)
        self.list.setItemDelegate(self.delegate)
        self.list.itemDoubleClicked.connect(self.edit_book)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.card_menu)
        QShortcut(QKeySequence("Ctrl+N"), self, self.add_book)
        QShortcut(QKeySequence("Ctrl+F"), self, self.focus_search)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo_shortcut)
        del_key = QShortcut(QKeySequence(Qt.Key_Delete), self.list)
        del_key.setContext(Qt.WidgetShortcut)
        del_key.activated.connect(self.delete_selected)
        main.addWidget(self.list, 1)

        self.empty_box = EmptyState()
        main.addWidget(self.empty_box, 1)
        outer.addLayout(main, 1)

        self._toast = Toast(self)
        self._toast.closed.connect(self.tidy_covers)   # an Undo offer ended: covers can be swept
        self.apply_theme()
        self.refresh()
        self.auto_backup()
        self.tidy_covers()

    # -- window size
    def restore_size(self, cfg):
        """Size (and maximised state) of the last run; a nonsense or too-big value falls back."""
        w, h = 1120, 680
        saved = cfg.get("window")
        if isinstance(saved, list) and len(saved) == 2 and all(isinstance(x, int) for x in saved):
            screen = QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            w, h = max(760, saved[0]), max(480, saved[1])
            if avail:
                w, h = min(w, avail.width()), min(h, avail.height())
        self.resize(w, h)
        self.start_maximized = bool(cfg.get("maximized"))

    def closeEvent(self, e):
        geo = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.remember(window=[geo.width(), geo.height()], maximized=self.isMaximized())
        super().closeEvent(e)

    # -- behaviour
    def remember(self, **values):
        """Saves small UI choices (theme, sort order) in settings.json; a failure never matters."""
        try:
            cfg = load_settings()
            cfg.update(values)
            save_settings(cfg)
        except OSError:
            pass

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def sort_changed(self, *_):
        self.remember(sort=self.sort_c.currentText())
        self.refresh()

    def set_status(self, name):
        self.status = name
        self.refresh()

    def refresh(self):
        if self.status == WISH:
            rows = self.store.wish_search(self.search.text(), self.sort_c.currentText())
        else:
            rows = self.store.search(self.search.text(), self.status, self.sort_c.currentText())
        self.delegate.reset()
        self.list.clear()
        for row in rows:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, row)
            it.setSizeHint(QSize(CARD_W, CARD_H))
            it.setToolTip(card_tooltip(row))
            self.list.addItem(it)
        self.list.setVisible(bool(rows))
        self.empty_box.setVisible(not rows)
        if not rows:
            self.show_empty(self.search.text().strip())
        counts = self.store.counts()
        counts[WISH] = self.store.wish_count()
        for name, b in self.nav.items():
            label = "All books" if name == "All" else name
            b.setText(f"{label}     {counts[name]}")
        self.heading.setText("All books" if self.status == "All" else self.status)
        self.sub.setText(f"{len(rows)} book{'s' if len(rows) != 1 else ''}")
        avg = self.store.average_rating()
        self.stats.setText(f"Total books: {counts['All']}\n"
                           + (f"Average rating: {avg:.1f} / 5" if avg else "No ratings yet"))

    def show_empty(self, text):
        """Fills the empty state for what is on screen: a search without hits, an empty wishlist, an
        empty library, or a status that has no books yet."""
        where = "wishlist" if self.status == WISH else "library"
        find = ("Find online", lambda: self.open_discover(self.search.text().strip()))
        add = ("Add to wishlist" if self.status == WISH else "Add book", self.add_book)
        hints = {"Read": "Books you have finished will show up here.",
                 "Reading": "The books you are reading right now will show up here.",
                 "Want to read": "Books you plan to read will show up here."}
        if text:
            short = text if len(text) <= 40 else text[:39] + "…"
            self.empty_box.set_state(
                f"No match for “\u2068{short}\u2069”",
                f"Nothing in your {where} matches this search. Try another spelling, or look for it online.",
                [("Search online instead", lambda: self.open_discover(self.search.text().strip())),
                 ("Clear search", self.search.clear)])
        elif self.status == WISH:
            self.empty_box.set_state("Your wishlist is empty",
                                     "Keep track of the books you want to buy later.", [add, find])
        elif self.status == "All":
            self.empty_box.set_state("Your shelf is empty",
                                     "Add your first book, find one online, or import a list you already have.",
                                     [add, find, ("Import from CSV…", self.import_csv)])
        else:
            self.empty_box.set_state(f"Nothing in “{self.status}” yet", hints[self.status], [add, find])

    def tidy_covers(self, tries=0):
        """Removes cover files that no book uses (see sweep_unused_covers). It waits until no dialog
        is open and no cover download is running, so it can never delete a picture that is about to
        be saved."""
        if tries == 0 and self._tidy_pending:
            return                                  # a check is already waiting
        busy = (QApplication.activeModalWidget() is not None
                or COVER_POOL.activeThreadCount() > 0 or COVER_ATTACH["n"] > 0
                or QThreadPool.globalInstance().activeThreadCount() > 0
                or self._toast.has_pending())
        if busy:
            if tries < 120:                         # look again in a second (for up to 2 minutes)
                self._tidy_pending = True
                QTimer.singleShot(1000, lambda: self.tidy_covers(tries + 1))
            else:
                self._tidy_pending = False          # the next dialog that closes tries again
            return
        self._tidy_pending = False
        try:
            sweep_unused_covers(self.store)
        except Exception:
            pass                                    # tidying must never stop the app

    def add_book(self):
        if self.status == WISH:
            d = BookDialog(self, self.store, wish=True)
            if d.exec():
                self.store.wish_add(*d.wish_values())
                self.refresh()
            self.tidy_covers()
            return
        d = BookDialog(self, self.store)
        if self.status in STATUSES:                 # adding from the 'Reading' view starts as Reading
            d.status_c.setCurrentText(self.status)
            d.mark_clean()
        if d.exec():
            self.store.add(*d.values())
            self.refresh()
        self.tidy_covers()

    def edit_wish(self, book):
        d = BookDialog(self, self.store, book, wish=True)
        if d.exec():
            t, a, n, c = d.wish_values()
            if d.deleted:
                self.remove_book(book, True)
            elif d.bought:
                self.store.wish_bought(book[0], t, a, n, c)
            else:
                self.store.wish_update(book[0], t, a, n, c)
            self.refresh()
        self.tidy_covers()

    def edit_book(self, item):
        book = item.data(Qt.UserRole)
        if self.status == WISH:
            self.edit_wish(book)
            return
        self.edit_library_book(book)

    def edit_library_book(self, book, preset_status=None):
        d = BookDialog(self, self.store, book)
        if preset_status:                           # same rules as picking the status in the dialog
            d.status_c.setCurrentText(preset_status)
            d.mark_clean()
        if d.exec():
            if d.deleted:
                self.remove_book(book, False)
            else:
                self.store.update(book[0], *d.values())
            self.refresh()
        self.tidy_covers()

    def quick_status(self, book, target):
        """Right-click > change status. Fills today's start/finish date like the dialog does. When the
        dates and the new status do not fit together (e.g. Read -> Reading with a finish date), the
        edit dialog opens with the new status already chosen, so nothing is lost or decided silently."""
        bid, title, author, status, rating, notes, cover, started, finished = book
        if target == status:
            return
        today = date.today().isoformat()
        if target == "Reading" and not started:
            started = today
        if target == "Read" and not finished:
            finished = today
        if [c for c, _ in check_dates(target, started, finished) if c not in SOFT_CHECKS]:
            self.edit_library_book(book, preset_status=target)
            return
        self.store.update(bid, title, author, target, rating, notes, cover, started, finished)
        self.refresh()

    def wish_to_library(self, book):
        bid, title, author, _s, _r, notes, cover, _a, _b = book
        if self.store.exists(title):
            QMessageBox.information(self, "Already in your library",
                                    "A book with this title is already in your library.")
            return
        self.store.wish_bought(bid, title, author, notes, cover)
        self.refresh()

    def card_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        book = item.data(Qt.UserRole)
        wish = self.status == WISH
        menu = QMenu(self.list)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)        # lets the QSS rounded corners show
        menu.setMinimumWidth(200)
        menu.addAction("Edit…").setData("edit")
        if wish:
            menu.addAction("Bought — move to library").setData("bought")
        else:
            labels = {"Read": "Mark as Read", "Reading": "Start reading",
                      "Want to read": "Move to Want to read"}
            for st in STATUSES:
                if st != book[3]:
                    menu.addAction(labels[st]).setData(st)
        menu.addSeparator()
        menu.addAction("Delete…").setData("delete")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        key = chosen.data() if chosen else None            # run it after the menu has closed
        if key == "edit":
            self.edit_book(item)
        elif key == "bought":
            self.wish_to_library(book)
        elif key == "delete":
            self.delete_item(item)
        elif key in STATUSES:
            self.quick_status(book, key)

    def open_discover(self, query):
        d = DiscoverDialog(self, self.store, query)
        if self.status == WISH:                     # opened from the wishlist: add there by default
            d.status_c.setCurrentText(WISH)
        d.changed.connect(self.refresh)
        d.exec()
        self.refresh()
        self.tidy_covers()

    def delete_selected(self):
        self.delete_item(self.list.currentItem())

    def delete_item(self, item):
        if item:
            self.remove_book(item.data(Qt.UserRole), self.status == WISH)
            self.refresh()
            self.tidy_covers()

    def remove_book(self, book, wish):
        """Deletes at once and offers Undo for a few seconds (instead of asking first). The row is
        kept as it was; Undo puts it back with the same id, cover, rating, notes and dates. The cover
        file stays on disk while the offer is open (tidy_covers waits for it)."""
        snapshot = tuple(book)
        (self.store.wish_delete if wish else self.store.delete)(book[0])
        name = snapshot[1] if len(snapshot[1]) <= 40 else snapshot[1][:39] + "…"
        where = "wishlist" if wish else "library"
        self.last_deleted = (snapshot, wish)
        toast(self, f"“\u2068{name}\u2069” removed from your {where}", "Undo",
              lambda: self.undo_delete(snapshot, wish), ms=7000, pending=True)

    def undo_delete(self, snapshot=None, wish=None):
        if snapshot is None:
            snapshot, wish = getattr(self, "last_deleted", (None, None))
        if snapshot is None:
            return
        self.last_deleted = (None, None)
        try:
            if (self.store.wish_exists if wish else self.store.exists)(snapshot[1]):
                raise ValueError("same title")
            self.store.restore_row(snapshot, wish)
        except (ValueError, sqlite3.Error):
            toast(self, "Couldn’t restore it — a book with this title was added in the meantime.")
            return
        self.refresh()
        toast(self, "Restored")

    def toast_ready(self):
        t = getattr(self, "_toast", None)
        return bool(t and t.has_pending())

    def undo_shortcut(self):
        if self.toast_ready():
            self.undo_delete()

    def open_stats(self):
        StatsDialog(self, self.store).exec()

    def open_data(self):
        DataDialog(self).exec()
        self.refresh()
        self.tidy_covers()

    def auto_backup(self):
        try:
            make_backup(self.store)
        except Exception:
            pass                                    # a failed backup must never stop the app

    def backup_now(self, host=None):
        host = host or self
        try:
            dest = make_backup(self.store, force=True)
        except Exception as e:
            QMessageBox.warning(host, "Backup failed", str(e))
            return
        toast(host, f"Backup saved — {dest.name}" if dest else "A backup was already made a moment ago.",
              "Open folder" if dest else None, lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(BACKUP_DIR))))

    def restore_backup(self, host=None):
        host = host or self
        BACKUP_DIR.mkdir(exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup", str(BACKUP_DIR),
                                              "Backups (*.zip)")
        if not path:
            return
        if QMessageBox.question(self, "Restore backup",
                                "This replaces ALL books in the app with the ones in the backup.\n"
                                "A safety copy of the current library is saved first. Continue?"
                                ) != QMessageBox.Yes:
            return
        try:
            make_backup(self.store, force=True, tag="-before-restore")
            restore_from_zip(self.store, path)
        except Exception as e:
            QMessageBox.warning(self, "Restore failed", str(e))
            return
        self.refresh()
        toast(host, "Your library was restored from the backup.")

    def import_csv(self, host=None):
        host = host or self
        path, _ = QFileDialog.getOpenFileName(self, "Import books from CSV", "",
                                              "CSV files (*.csv)")
        if not path:
            return
        try:
            if self.store.count():
                make_backup(self.store, force=True, tag="-before-import")
            added, skipped, bad = import_csv_file(self.store, path)
        except Exception as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        self.refresh()
        toast(host, f"Import finished — {added} added · {skipped} already in your library"
                    + (f" · {bad} row{'s' if bad != 1 else ''} without a title" if bad else ""), ms=7000)

    def export_csv(self, host=None):
        host = host or self
        path, _ = QFileDialog.getSaveFileName(self, "Export books", "my_books.csv",
                                              "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # opens fine in Excel
            w = csv.writer(fh)
            w.writerow(["Title", "Author", "Status", "Rating", "Started", "Finished", "Notes"])
            rows = self.store.search("", "All", "Title A–Z")
            for _id, t, a, st, r, n, _c, d0, d1 in rows:
                w.writerow([t, a, st, r or "", csv_date(d0), csv_date(d1), n])
        toast(host, f"Exported {len(rows)} book{'s' if len(rows) != 1 else ''}")

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.apply_theme()
        self.remember(theme=self.theme)

    def apply_theme(self):
        T.update(THEMES[self.theme])
        QApplication.instance().setStyleSheet(make_qss())
        self.theme_btn.setText("Light mode" if self.theme == "dark" else "Dark mode")
        self.search_clear.setIcon(clear_icon())
        self.list.viewport().update()


def load_local_fonts():
    """Loads every .ttf/.otf in a 'fonts' folder next to the app (put Vazirmatn-Regular.ttf and
    Vazirmatn-Bold.ttf there) so the Persian font works without installing it system-wide.
    Returns how many font files were loaded."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    folder, n = base / "fonts", 0
    if folder.is_dir():
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf") and QFontDatabase.addApplicationFont(str(f)) >= 0:
                n += 1
    return n


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationVersion(APP_VERSION)
    load_local_fonts()
    font = QFont()
    font.setFamilies(["Vazirmatn", "Segoe UI", "Tahoma", "Noto Sans", "Arial"])
    font.setPointSize(10)
    app.setFont(font)
    w = Window()
    w.showMaximized() if w.start_maximized else w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
