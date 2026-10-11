"""Книжный блог на FastAPI: список книг, страница книги, редактирование и учётные записи."""

import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

import auth
import database

BASE_DIR = Path(__file__).parent

SESSION_COOKIE = "session"
# В домене — непустые части через точку: «a@b..c» и «a@.b.c» адресами не считаются.
EMAIL_RE = re.compile(r"""[^@\s<>"]+@[^@\s<>".]+(\.[^@\s<>".]+)+""")
MAX_EMAIL_LENGTH = 254
MAX_PASSWORD_LENGTH = 128

PAGE_SIZE = 12
# Те же числа стоят в maxlength полей form.html; браузерную проверку легко обойти, поэтому сервер проверяет сам.
MAX_LENGTHS = {"title": 200, "author": 200, "genre": 100, "description": 500, "content": 20_000}

# id книги — в пределах INTEGER SQLite: большее число роняло запрос с OverflowError.
BookId = Annotated[int, PathParam(ge=1, le=2**63 - 1)]

# Попытки входа за 15 минут: по почте — от подбора пароля к одной записи, по IP — от перебора почт.
LOGIN_WINDOW_MINUTES = 15
email_limiter = auth.AttemptLimiter(limit=5, window_seconds=LOGIN_WINDOW_MINUTES * 60)
ip_limiter = auth.AttemptLimiter(limit=20, window_seconds=LOGIN_WINDOW_MINUTES * 60)

ERROR_DETAILS = {
    400: "Некорректный запрос",
    404: "Страница не найдена",
    405: "Этот адрес не поддерживает такой запрос",
    500: "На сервере произошла ошибка. Попробуйте позже.",
}

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


# JSON-API у сайта нет, так что /docs, /redoc и /openapi.json отключены: они лишь раскрывали список маршрутов.
app = FastAPI(title="Книжная полка", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def get_current_user(request: Request):
    """Возвращает вошедшего пользователя или None; результат запоминается на время запроса."""
    if not hasattr(request.state, "user"):
        token = request.cookies.get(SESSION_COOKIE)
        request.state.user = database.get_user_by_session(auth.hash_token(token)) if token else None
    return request.state.user


def user_context(request: Request) -> dict:
    """Делает current_user и csrf_token доступными во всех шаблонах, включая шапку и футер."""
    token = request.cookies.get(SESSION_COOKIE)
    return {
        "current_user": get_current_user(request),
        "csrf_token": auth.csrf_token(token) if token else "",
    }


def check_csrf(request: Request, form) -> None:
    """Отклоняет форму без токена: так её не отправит чужой сайт от имени вошедшего посетителя."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not auth.verify_csrf(token, str(form.get("csrf_token") or "")):
        raise HTTPException(
            status_code=403, detail="Форма устарела. Вернитесь назад, обновите страницу и отправьте её снова."
        )


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


templates = Jinja2Templates(directory=BASE_DIR / "templates", context_processors=[user_context])


def format_date(value: str) -> str:
    """'2026-10-10T12:30:00' -> '10 октября 2026'."""
    date = datetime.fromisoformat(value)
    return f"{date.day} {MONTHS[date.month - 1]} {date.year}"


templates.env.filters["date"] = format_date


def parse_book_form(form) -> tuple[dict, dict]:
    """Приводит данные формы к полям книги и возвращает (данные, ошибки)."""

    def text(name: str) -> str:
        return str(form.get(name) or "").replace("\r\n", "\n").strip()

    data = {
        "title": text("title"),
        "author": text("author"),
        "year": text("year"),
        "genre": text("genre"),
        "rating": text("rating"),
        "description": text("description"),
        "content": text("content"),
    }
    errors = {}

    for name, limit in MAX_LENGTHS.items():
        if len(data[name]) > limit:
            errors[name] = f"Не длиннее {limit} символов (сейчас {len(data[name])})"

    if not data["title"]:
        errors["title"] = "Укажите название книги"
    if not data["author"]:
        errors["author"] = "Укажите автора"

    max_year = datetime.now().year + 1
    if data["year"]:
        try:
            # int() принял бы и «+1999», и «1_999», и полноширинные цифры — годом считаем только обычные цифры.
            if not (data["year"].isascii() and data["year"].isdigit()):
                raise ValueError
            data["year"] = int(data["year"])
            if not 1 <= data["year"] <= max_year:
                raise ValueError
        except ValueError:
            errors["year"] = f"Год должен быть числом от 1 до {max_year}"
    else:
        data["year"] = None

    try:
        data["rating"] = int(data["rating"] or 0)
        if not 0 <= data["rating"] <= 5:
            raise ValueError
    except ValueError:
        errors["rating"] = "Оценка должна быть от 0 до 5"

    return data, errors


def parse_email(form) -> tuple[str, str | None]:
    """Возвращает (почта в нижнем регистре, текст ошибки или None)."""
    email = str(form.get("email") or "").strip().lower()
    if not email:
        return email, "Укажите почту"
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_RE.fullmatch(email):
        return email, "Укажите корректный адрес почты"
    return email, None


def start_session(response, user_id: int) -> None:
    """Создаёт сессию в базе и кладёт её токен в cookie ответа."""
    token = auth.new_session_token()
    lifetime = timedelta(days=auth.SESSION_DAYS)
    expires_at = (datetime.now() + lifetime).isoformat(timespec="seconds")
    database.create_session(user_id, auth.hash_token(token), expires_at)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(lifetime.total_seconds()), httponly=True, samesite="lax"
    )


def get_book_or_404(book_id: int):
    book = database.get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return book


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", page: int = 1):
    page = max(page, 1)
    books, total = database.list_books(q, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    pages = max(1, -(-total // PAGE_SIZE))
    if page > pages:
        # Страницы с таким номером нет (например, книги удалили) — показываем последнюю.
        page = pages
        books, total = database.list_books(q, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    return templates.TemplateResponse(
        request, "index.html", {"books": books, "total": total, "q": q.strip(), "page": page, "pages": pages}
    )


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html")


@app.get("/books/new", response_class=HTMLResponse)
def new_book_form(request: Request):
    if not get_current_user(request):
        return login_redirect()
    return templates.TemplateResponse(request, "form.html", {"book": {}, "errors": {}, "is_new": True})


@app.post("/books/new", response_class=HTMLResponse)
async def create_book(request: Request):
    if not get_current_user(request):
        return login_redirect()
    form = await request.form()
    check_csrf(request, form)
    data, errors = parse_book_form(form)
    if errors:
        return templates.TemplateResponse(
            request, "form.html", {"book": data, "errors": errors, "is_new": True}, status_code=422
        )
    book_id = database.create_book(data)
    return RedirectResponse(f"/books/{book_id}", status_code=303)


@app.get("/books/{book_id}", response_class=HTMLResponse)
def view_book(request: Request, book_id: BookId):
    book = get_book_or_404(book_id)
    return templates.TemplateResponse(request, "book.html", {"book": book})


@app.get("/books/{book_id}/edit", response_class=HTMLResponse)
def edit_book_form(request: Request, book_id: BookId):
    if not get_current_user(request):
        return login_redirect()
    book = get_book_or_404(book_id)
    return templates.TemplateResponse(request, "form.html", {"book": book, "errors": {}, "is_new": False})


@app.post("/books/{book_id}/edit", response_class=HTMLResponse)
async def update_book(request: Request, book_id: BookId):
    if not get_current_user(request):
        return login_redirect()
    form = await request.form()
    check_csrf(request, form)
    get_book_or_404(book_id)
    data, errors = parse_book_form(form)
    if errors:
        return templates.TemplateResponse(
            request,
            "form.html",
            {"book": {**data, "id": book_id}, "errors": errors, "is_new": False},
            status_code=422,
        )
    database.update_book(book_id, data)
    return RedirectResponse(f"/books/{book_id}", status_code=303)


@app.post("/books/{book_id}/delete")
async def delete_book(request: Request, book_id: BookId):
    if not get_current_user(request):
        return login_redirect()
    check_csrf(request, await request.form())
    get_book_or_404(book_id)
    database.delete_book(book_id)
    return RedirectResponse("/", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    if get_current_user(request):
        return RedirectResponse("/account", status_code=303)
    return templates.TemplateResponse(request, "register.html", {"form": {}, "errors": {}})


@app.post("/register", response_class=HTMLResponse)
async def register(request: Request):
    form = await request.form()
    email, email_error = parse_email(form)
    password = str(form.get("password") or "")
    errors = {}

    if email_error:
        errors["email"] = email_error
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        errors["password"] = f"Пароль должен быть не короче {auth.MIN_PASSWORD_LENGTH} символов"
    elif len(password) > MAX_PASSWORD_LENGTH:
        errors["password"] = f"Пароль должен быть не длиннее {MAX_PASSWORD_LENGTH} символов"
    elif not password.strip():
        errors["password"] = "Пароль не может состоять из одних пробелов"
    elif password != str(form.get("password2") or ""):
        errors["password2"] = "Пароли не совпадают"

    user_id = None
    if not errors:
        # Проверка в create_user (UNIQUE в базе) страхует от одновременной регистрации.
        if not database.get_user_by_email(email):
            # Хеширование занимает доли секунды, поэтому идёт в пуле потоков, а не в цикле событий.
            user_id = database.create_user(email, await run_in_threadpool(auth.hash_password, password))
        if user_id is None:
            errors["email"] = "Пользователь с такой почтой уже зарегистрирован"

    if errors:
        return templates.TemplateResponse(
            request, "register.html", {"form": {"email": email}, "errors": errors}, status_code=422
        )
    response = RedirectResponse("/account", status_code=303)
    start_session(response, user_id)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if get_current_user(request):
        return RedirectResponse("/account", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"form": {}, "errors": {}})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    form = await request.form()
    email = str(form.get("email") or "").strip().lower()
    password = str(form.get("password") or "")

    def failed(message: str, status_code: int):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"form": {"email": email[:MAX_EMAIL_LENGTH]}, "errors": {"form": message}},
            status_code=status_code,
        )

    # Пустая форма — не попытка подбора: отвечаем сразу и лимит не тратим.
    if not email or not password:
        return failed("Укажите почту и пароль", 422)

    ip_key = request.client.host if request.client else "unknown"
    email_key = email[:MAX_EMAIL_LENGTH]
    if ip_limiter.is_blocked(ip_key) or email_limiter.is_blocked(email_key):
        return failed(f"Слишком много попыток входа. Попробуйте через {LOGIN_WINDOW_MINUTES} минут.", 429)
    # Попытка засчитывается до проверки пароля, чтобы пачка одновременных запросов не обошла лимит.
    ip_limiter.add_failure(ip_key)
    email_limiter.add_failure(email_key)

    user = database.get_user_by_email(email)
    stored = user["password_hash"] if user else None
    if not await run_in_threadpool(auth.verify_password, password, stored):
        return failed("Неверная почта или пароль", 422)
    email_limiter.reset(email_key)
    # По IP снимаем только эту попытку: полный сброс позволил бы обнулять лимит входом в свою запись.
    ip_limiter.forget_last(ip_key)
    response = RedirectResponse("/account", status_code=303)
    start_session(response, user["id"])
    return response


@app.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        check_csrf(request, await request.form())
        database.delete_session(auth.hash_token(token))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, saved: bool = False):
    if not get_current_user(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "account.html", {"form": {}, "errors": {}, "saved": saved})


@app.get("/account/email")
def change_email_form():
    # Сюда попадают, обновив страницу с ошибками формы через адресную строку.
    return RedirectResponse("/account", status_code=303)


@app.post("/account/email", response_class=HTMLResponse)
async def change_email(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    check_csrf(request, form)
    email, email_error = parse_email(form)
    errors = {}

    if email_error:
        errors["email"] = email_error
    elif email == user["email"]:
        errors["email"] = "Это ваша текущая почта"
    password = str(form.get("password") or "")
    if not await run_in_threadpool(auth.verify_password, password, user["password_hash"]):
        errors["password"] = "Неверный пароль"

    if not errors and not database.update_user_email(user["id"], email):
        errors["email"] = "Пользователь с такой почтой уже зарегистрирован"

    if errors:
        return templates.TemplateResponse(
            request,
            "account.html",
            {"form": {"email": email}, "errors": errors, "saved": False},
            status_code=422,
        )
    return RedirectResponse("/account?saved=1", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    # У ошибок самого Starlette detail — английская фраза статуса («Not Found»); заменяем её русским текстом.
    detail = exc.detail
    if detail == HTTPStatus(exc.status_code).phrase:
        detail = ERROR_DETAILS.get(exc.status_code, "")
    return templates.TemplateResponse(
        request, "error.html", {"status_code": exc.status_code, "detail": detail}, status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Нечисловой id в адресе (/books/abc) для посетителя — просто несуществующая страница.

    Ошибка в параметрах запроса (/?page=abc) — это 400: сама страница существует.
    """
    status_code = 404 if any(error["loc"][0] == "path" for error in exc.errors()) else 400
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": status_code, "detail": ERROR_DETAILS[status_code]},
        status_code=status_code,
    )


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    """Непредвиденная ошибка: вместо голого текста показываем ту же страницу ошибки."""
    return templates.TemplateResponse(
        request, "error.html", {"status_code": 500, "detail": ERROR_DETAILS[500]}, status_code=500
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
