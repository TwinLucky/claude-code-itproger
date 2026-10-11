"""Работа с базой данных SQLite: схема, начальные данные и CRUD-операции для книг, пользователи и сессии."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "books.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    author      TEXT    NOT NULL,
    year        INTEGER,
    genre       TEXT    NOT NULL DEFAULT '',
    rating      INTEGER NOT NULL DEFAULT 0,
    description TEXT    NOT NULL DEFAULT '',
    content     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
)
"""

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
)
"""

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id),
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
)
"""

FIELDS = ("title", "author", "year", "genre", "rating", "description", "content")
# Колонки для карточек на главной: заметки туда не попадают и из базы не читаются.
LIST_COLUMNS = ("id", "title", "author", "year", "genre", "rating", "description")

SEED_BOOKS = [
    {
        "title": "Мастер и Маргарита",
        "author": "Михаил Булгаков",
        "year": 1967,
        "genre": "Роман",
        "rating": 5,
        "description": "Дьявол приезжает в Москву тридцатых годов, а в это же время "
        "разворачивается история любви и рукописи, которая не горит.",
        "content": "Книга, которую каждый раз читаешь по-новому. В юности это "
        "весёлая фантасмагория про кота с примусом, позже — роман о трусости, "
        "которую Булгаков называет самым страшным из пороков.\n\n"
        "Три линии — московская, ершалаимская и история Мастера — сплетены так, "
        "что ни одну нельзя вынуть, не обрушив остальные. Особенно хороши главы "
        "о Пилате: сухой, почти документальный язык на фоне московской чертовщины "
        "звучит как совсем другая книга, и в этом весь замысел.\n\n"
        "Перечитывать стоит медленно и с карандашом.",
    },
    {
        "title": "1984",
        "author": "Джордж Оруэлл",
        "year": 1949,
        "genre": "Антиутопия",
        "rating": 5,
        "description": "Уинстон Смит работает в Министерстве правды и однажды "
        "решается на дневник — первое преступление против Партии.",
        "content": "Самое пугающее в романе — не телекраны и не комната 101, а "
        "новояз: идея, что мысль можно сделать невозможной, просто убрав для неё "
        "слова.\n\n"
        "Оруэлл пишет подчёркнуто просто, без украшений, и от этого текст "
        "действует сильнее. Вторая часть с книгой Голдстейна читается как "
        "отдельное эссе о том, как устроена власть, которой не нужна цель, "
        "кроме самой себя.\n\n"
        "Финал — один из самых безжалостных в литературе двадцатого века.",
    },
    {
        "title": "Маленький принц",
        "author": "Антуан де Сент-Экзюпери",
        "year": 1943,
        "genre": "Сказка",
        "rating": 4,
        "description": "Лётчик, потерпевший аварию в пустыне, встречает мальчика "
        "с далёкого астероида.",
        "content": "Тонкая книжка, которую принято дарить детям, хотя написана "
        "она скорее для взрослых, забывших, что когда-то были детьми.\n\n"
        "Каждая планета на пути принца — маленькая притча о взрослой жизни: "
        "король без подданных, делец, считающий звёзды, фонарщик, который один "
        "занят делом, а не собой.\n\n"
        "Разговор с Лисом о том, что значит приручить, — сердце всей истории.",
    },
    {
        "title": "Сто лет одиночества",
        "author": "Габриэль Гарсиа Маркес",
        "year": 1967,
        "genre": "Магический реализм",
        "rating": 5,
        "description": "Семь поколений семьи Буэндиа и история городка Макондо "
        "от основания до последнего ветра.",
        "content": "Роман, в котором дождь идёт четыре года, а девушка возносится "
        "на небо, развешивая простыни, — и всё это рассказано тем же ровным "
        "тоном, что и гражданские войны.\n\n"
        "Имена повторяются из поколения в поколение, и поначалу в них путаешься. "
        "Потом понимаешь, что так и задумано: время в Макондо идёт по кругу, и "
        "каждый Аурелиано обречён на своё одиночество.\n\n"
        "Совет: держите под рукой родословное древо первые сто страниц.",
    },
    {
        "title": "Дюна",
        "author": "Фрэнк Герберт",
        "year": 1965,
        "genre": "Научная фантастика",
        "rating": 4,
        "description": "Дом Атрейдесов получает в управление пустынную планету "
        "Арракис — единственный источник пряности во Вселенной.",
        "content": "Под обложкой космической оперы спрятан роман об экологии, "
        "религии и о том, как опасны мессии — даже те, что на нашей стороне.\n\n"
        "Герберт строит мир с редкой тщательностью: у фрименов есть свой язык, "
        "свои обряды воды, своя экономика. Пустыня здесь не декорация, а главный "
        "персонаж.\n\n"
        "Начало тяжеловато из-за обилия терминов, но глоссарий в конце книги "
        "выручает.",
    },
    {
        "title": "Преступление и наказание",
        "author": "Фёдор Достоевский",
        "year": 1866,
        "genre": "Роман",
        "rating": 5,
        "description": "Бывший студент Раскольников проверяет на практике теорию "
        "о людях, которым позволено больше других.",
        "content": "Детектив наоборот: убийца известен с первых глав, и вопрос "
        "не в том, кто, а в том, выдержит ли он собственную идею.\n\n"
        "Петербург у Достоевского душный, жёлтый, тесный — город давит на героя "
        "не меньше, чем совесть. А три разговора с Порфирием Петровичем — "
        "образец психологической дуэли, из которой выросла половина "
        "современных триллеров.\n\n"
        "Эпилог многие ругают, но без него роман остался бы без выхода.",
    },
]


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db() -> None:
    """Создаёт таблицы и при первом запуске наполняет полку примерами."""
    with get_connection() as conn:
        conn.execute(SCHEMA)
        conn.execute(USERS_SCHEMA)
        conn.execute(SESSIONS_SCHEMA)
        # user_version отмечает, что база уже инициализирована: иначе примеры
        # возвращались бы при каждом запуске, стоило удалить все книги.
        if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0:
                for book in SEED_BOOKS:
                    _insert_book(conn, book)
            conn.execute("PRAGMA user_version = 1")


def list_books(query: str = "", limit: int | None = None, offset: int = 0) -> tuple[list[sqlite3.Row], int]:
    """Возвращает (книги страницы, сколько всего найдено). Заметки (content) не выбираются."""
    with get_connection() as conn:
        books = conn.execute(
            f"SELECT {', '.join(LIST_COLUMNS)} FROM books ORDER BY created_at DESC, id DESC"
        ).fetchall()
    # LIKE в SQLite не учитывает регистр кириллицы, поэтому фильтруем в Python.
    # «ё» и «е» считаются одной буквой, а слова запроса ищутся по отдельности: «булгаков мастер».
    words = query.casefold().replace("ё", "е").split()
    if words:
        books = [
            b for b in books
            if all(
                word in f"{b['title']} {b['author']} {b['genre']}".casefold().replace("ё", "е")
                for word in words
            )
        ]
    total = len(books)
    end = None if limit is None else offset + limit
    return books[offset:end], total


def get_book(book_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()


def _insert_book(conn: sqlite3.Connection, data: dict) -> int:
    now = _now()
    columns = ", ".join(FIELDS)
    placeholders = ", ".join("?" for _ in FIELDS)
    cursor = conn.execute(
        f"INSERT INTO books ({columns}, created_at, updated_at) "
        f"VALUES ({placeholders}, ?, ?)",
        (*(data[f] for f in FIELDS), now, now),
    )
    return cursor.lastrowid


def create_book(data: dict) -> int:
    with get_connection() as conn:
        return _insert_book(conn, data)


def update_book(book_id: int, data: dict) -> None:
    assignments = ", ".join(f"{f} = ?" for f in FIELDS)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE books SET {assignments}, updated_at = ? WHERE id = ?",
            (*(data[f] for f in FIELDS), _now(), book_id),
        )


def delete_book(book_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


def get_user(user_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_email(email: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def create_user(email: str, password_hash: str) -> int | None:
    """Создаёт пользователя и возвращает его id; None — если почта уже занята."""
    now = _now()
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (email, password_hash, now, now),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None


def update_user_email(user_id: int, email: str) -> bool:
    """Меняет почту пользователя; False — если она уже занята другим."""
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
                (email, _now(), user_id),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def create_session(user_id: int, token_hash: str, expires_at: str) -> None:
    with get_connection() as conn:
        # Заодно убираем просроченные сессии, чтобы таблица не росла бесконечно.
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, _now(), expires_at),
        )


def get_user_by_session(token_hash: str) -> sqlite3.Row | None:
    """Возвращает пользователя по действующей сессии."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
            (token_hash, _now()),
        ).fetchone()


def delete_session(token_hash: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
