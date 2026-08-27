"""Klient API portalu Kaisai KSM (sterowanie.kaisai.com).

Portal jest aplikacja Phoenix/Elixir. Logowanie odbywa sie zwyklym formularzem
POST /<locale>/login z polami _csrf_token, email, password. W odpowiedzi
serwer ustawia ciasteczko sesji (_compit_key) i przekierowuje na panel.
Dane odczytujemy z GET /api/current_user, ktory zwraca komplet: konto,
bramki, urzadzenia i pelny stan kazdego z nich.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# token CSRF bywa w ukrytym polu formularza albo w meta tagu - probujemy oba
CSRF_PATTERNS = (
    r'name=["\']_csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
    r'value=["\']([^"\']+)["\'][^>]*name=["\']_csrf_token["\']',
    r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']',
    r'content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token["\']',
    r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
)

# czesc serwerow odrzuca zapytania bez naglowkow przegladarki
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}


class KaisaiError(Exception):
    """Blad ogolny."""


class KaisaiAuthError(KaisaiError):
    """Nieprawidlowe dane logowania."""


class KaisaiConnectionError(KaisaiError):
    """Problem z polaczeniem."""


class KaisaiKsmApi:
    """Minimalny klient portalu Kaisai KSM."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        email: str,
        password: str,
        locale: str = "pl",
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._email = email
        self._password = password
        self._locale = locale
        self._csrf: str | None = None

    # ------------------------------------------------------------------ auth
    async def _fetch_csrf(self) -> str:
        url = f"{self._host}/{self._locale}/login"
        try:
            async with self._session.get(url, headers=DEFAULT_HEADERS) as resp:
                status = resp.status
                html = await resp.text()
        except aiohttp.ClientError as err:
            raise KaisaiConnectionError(
                f"Nie mozna pobrac strony logowania {url}: {type(err).__name__}: {err}"
            ) from err

        if status >= 400:
            raise KaisaiConnectionError(f"Strona logowania {url} zwrocila HTTP {status}")

        for pattern in CSRF_PATTERNS:
            match = re.search(pattern, html)
            if match:
                self._csrf = match.group(1)
                _LOGGER.debug("Znaleziono token CSRF (%d znakow)", len(self._csrf))
                return self._csrf

        _LOGGER.debug(
            "Strona logowania bez rozpoznanego tokenu CSRF (HTTP %s, %d znakow). "
            "Poczatek odpowiedzi: %s",
            status,
            len(html),
            html[:1000],
        )
        raise KaisaiConnectionError(
            f"Nie znaleziono tokenu CSRF na {url} (HTTP {status}, {len(html)} znakow) - "
            "wlacz debug dla custom_components.kaisai_ksm, zeby zobaczyc tresc strony"
        )

    async def async_login(self) -> None:
        """Zaloguj sie i zapamietaj ciasteczko sesji w sesji aiohttp."""
        token = await self._fetch_csrf()
        url = f"{self._host}/{self._locale}/login"
        payload = {
            "_csrf_token": token,
            "email": self._email,
            "password": self._password,
        }

        try:
            async with self._session.post(
                url, data=payload, headers=DEFAULT_HEADERS, allow_redirects=False
            ) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    # przekierowanie z powrotem na login = zle dane
                    if "login" in location:
                        raise KaisaiAuthError("Nieprawidlowy login lub haslo")
                    _LOGGER.debug("Zalogowano, przekierowanie na %s", location)
                    return
                if resp.status == 200:
                    # brak przekierowania zwykle oznacza ponowne wyswietlenie formularza
                    raise KaisaiAuthError("Logowanie odrzucone przez portal")
                raise KaisaiConnectionError(f"Logowanie zwrocilo HTTP {resp.status}")
        except aiohttp.ClientError as err:
            raise KaisaiConnectionError(f"Blad polaczenia przy logowaniu: {err}") from err

    # ------------------------------------------------------------------ dane
    async def _get_current_user(self) -> dict[str, Any] | None:
        """Zwroc dane konta albo None, gdy sesja wygasla."""
        url = f"{self._host}/api/current_user"
        try:
            async with self._session.get(
                url, headers={**DEFAULT_HEADERS, "Accept": "application/json"}
            ) as resp:
                if resp.status in (401, 403):
                    return None
                if resp.status >= 400:
                    raise KaisaiConnectionError(f"/api/current_user zwrocilo HTTP {resp.status}")
                if "json" not in resp.headers.get("content-type", ""):
                    # portal odesial HTML = wylogowani
                    return None
                return await resp.json()
        except aiohttp.ClientError as err:
            raise KaisaiConnectionError(f"Blad polaczenia z {url}: {type(err).__name__}: {err}") from err

    async def async_get_data(self) -> dict[str, Any]:
        """Pobierz dane, w razie potrzeby logujac sie ponownie."""
        data = await self._get_current_user()
        if data is None:
            _LOGGER.debug("Sesja wygasla - loguje sie ponownie")
            await self.async_login()
            data = await self._get_current_user()
        if data is None:
            raise KaisaiAuthError("Ponowne logowanie nie powiodlo sie")
        return data

    # ------------------------------------------------------------------ zapis
    async def async_set_param(
        self, gate_id: int, device_id: int, code: str, value: float | int | str
    ) -> bool:
        """Ustaw parametr urzadzenia.

        UWAGA: dokladny format zapisu nie zostal jeszcze potwierdzony na zywym
        portalu, dlatego probujemy kilku wariantow i logujemy odpowiedzi.
        Jesli zapis nie dziala, wlacz debug i zobacz w logu, co odpowiada serwer.
        """
        url = f"{self._host}/api/gates/{gate_id}/devices/{device_id}/params"
        headers = {"Accept": "application/json"}
        if self._csrf:
            headers["x-csrf-token"] = self._csrf

        variants: list[tuple[str, dict[str, Any]]] = [
            ("post", {"params": [{"code": code, "value": value}]}),
            ("put", {"params": [{"code": code, "value": value}]}),
            ("post", {"code": code, "value": value}),
            ("put", {"code": code, "value": value}),
        ]

        problems: list[str] = []
        for method, body in variants:
            try:
                async with self._session.request(
                    method, url, json=body, headers=headers
                ) as resp:
                    text = await resp.text()
                    if resp.status < 300:
                        _LOGGER.info(
                            "Zapis %s=%s OK (%s, %s)", code, value, method.upper(), body
                        )
                        return True
                    problems.append(f"{method.upper()} {body} -> {resp.status}: {text[:120]}")
            except aiohttp.ClientError as err:
                problems.append(f"{method.upper()}: {err}")

        _LOGGER.error(
            "Nie udalo sie zapisac %s=%s. Proby:\n%s", code, value, "\n".join(problems)
        )
        return False


def parse_devices(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Splaszcz odpowiedz /api/current_user do slownika urzadzen."""
    devices: dict[str, dict[str, Any]] = {}
    for gate in data.get("gates", []) or []:
        gate_id = gate.get("id")
        for device in gate.get("devices", []) or []:
            device_id = device.get("id")
            state = device.get("state") or {}
            params = {
                param["code"]: param
                for param in (state.get("params") or [])
                if "code" in param
            }
            key = f"{gate_id}_{device_id}"
            devices[key] = {
                "gate_id": gate_id,
                "device_id": device_id,
                "serial_number": device.get("serial_number"),
                "code": device.get("code"),
                "label": device.get("label"),
                "producer": gate.get("producer"),
                "errors": state.get("errors") or [],
                "params": params,
            }
    return devices
