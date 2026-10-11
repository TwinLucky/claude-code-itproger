"""Пароли и токены сессий: хеширование и проверка без обращения к базе и HTTP."""

import hashlib
import hmac
import secrets
import time
from collections import deque

MIN_PASSWORD_LENGTH = 8
SESSION_DAYS = 30

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
_DUMMY_SALT = secrets.token_bytes(16)


def _derive(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def hash_password(password: str) -> str:
    """Возвращает строку вида 'pbkdf2_sha256$итерации$соль$хеш' со случайной солью."""
    salt = secrets.token_bytes(16)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${_derive(password, salt, ITERATIONS)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Проверяет пароль; stored=None — пользователя нет, но хеш всё равно считается.

    Иначе по времени ответа было бы видно, зарегистрирована ли почта.
    """
    if stored is None:
        _derive(password, _DUMMY_SALT, ITERATIONS)
        return False
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        actual = _derive(password, bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """В базе хранится только хеш токена: утечка таблицы не выдаёт действующие сессии."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(session_token: str) -> str:
    """Токен для скрытого поля форм: выводится из токена сессии, который чужому сайту недоступен."""
    return hmac.new(session_token.encode("utf-8"), b"csrf", hashlib.sha256).hexdigest()


def verify_csrf(session_token: str, value: str) -> bool:
    return hmac.compare_digest(csrf_token(session_token).encode("utf-8"), value.encode("utf-8"))


class AttemptLimiter:
    """Счётчик неудачных попыток по ключу (почта, IP) в скользящем окне.

    Хранится в памяти процесса: после перезапуска счётчики обнуляются,
    а при нескольких процессах у каждого они свои.
    """

    MAX_KEYS = 10_000

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        failures = self._failures.get(key)
        if failures is None:
            return deque()
        border = time.monotonic() - self.window
        while failures and failures[0] <= border:
            failures.popleft()
        if not failures:
            del self._failures[key]
        return failures

    def is_blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.limit

    def add_failure(self, key: str) -> None:
        if key not in self._failures and len(self._failures) >= self.MAX_KEYS:
            for old_key in list(self._failures):
                self._recent(old_key)
        self._failures.setdefault(key, deque()).append(time.monotonic())

    def forget_last(self, key: str) -> None:
        """Снимает последнюю засчитанную попытку — когда она оказалась успешной."""
        failures = self._failures.get(key)
        if failures:
            failures.pop()
            if not failures:
                del self._failures[key]

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
