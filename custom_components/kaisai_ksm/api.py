"""Klient API portalu Kaisai KSM (sterowanie.kaisai.com).

Portal jest aplikacja Phoenix/Elixir. Logowanie odbywa sie zwyklym formularzem
POST /<locale>/login z polami _csrf_token, email, password. W odpowiedzi
serwer ustawia ciasteczko sesji (_compit_key) i przekierowuje na panel.
Dane odczytujemy z GET /api/current_user, ktory zwraca komplet: konto,
bramki, urzadzenia i pelny stan kazdego z nich.
"""

from __future__ import annotations

import base64
import binascii
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

# JWT ma trzy czesci base64url rozdzielone kropkami i zawsze zaczyna sie od "eyJ"
TOKEN_PATTERN = re.compile(
    rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)

# nazwa ciasteczka sesji Phoenixa, w ktorym siedzi token Guardiana
SESSION_COOKIE = "_compit_key"

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
        self._token: str | None = None

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

        headers = {**DEFAULT_HEADERS, "Referer": url, "Origin": self._host}
        try:
            async with self._session.post(
                url, data=payload, headers=headers, allow_redirects=False
            ) as resp:
                status = resp.status
                location = resp.headers.get("Location", "")
                _LOGGER.debug(
                    "Logowanie: HTTP %s, Location=%s, ciasteczka=%s",
                    status,
                    location,
                    [c.key for c in self._session.cookie_jar],
                )
                if status in (301, 302, 303, 307, 308):
                    if "login" in location:
                        raise KaisaiAuthError("Nieprawidlowy login lub haslo")
                elif status == 200:
                    raise KaisaiAuthError("Logowanie odrzucone przez portal")
                else:
                    raise KaisaiConnectionError(f"Logowanie zwrocilo HTTP {status}")
        except aiohttp.ClientError as err:
            raise KaisaiConnectionError(f"Blad polaczenia przy logowaniu: {err}") from err

        # przegladarka po zalogowaniu wchodzi na strone panelu - dopiero wtedy
        # sesja jest w pelni ustanowiona
        landing = location if location.startswith("http") else f"{self._host}{location or '/' + self._locale}"
        html = ""
        try:
            async with self._session.get(landing, headers=DEFAULT_HEADERS) as resp:
                _LOGGER.debug("Strona panelu %s -> HTTP %s", landing, resp.status)
                html = await resp.text()
        except aiohttp.ClientError as err:
            _LOGGER.debug("Nie udalo sie wejsc na %s: %s", landing, err)

        # API wymaga naglowka Authorization z tokenem Guardiana; token jest
        # zapakowany w ciasteczku sesji, a zapasowo bywa w kodzie strony
        self._token = self._token_from_cookies() or self._token_from_html(html)
        if self._token:
            _LOGGER.debug("Token API wyciagniety (%d znakow)", len(self._token))
        else:
            _LOGGER.warning(
                "Nie udalo sie wyciagnac tokenu API z sesji - zapytania moga byc "
                "odrzucane przez portal"
            )

    # ----------------------------------------------------------------- token
    def _token_from_cookies(self) -> str | None:
        """Wyciagnij JWT z ciasteczka sesji Phoenixa.

        Ciasteczko ma postac SFMyNTY.<dane>.<podpis>, gdzie <dane> to base64url
        z zakodowana mapa sesji. W srodku, jako zwykly tekst, siedzi
        guardian_default_token - czyli JWT, ktorego oczekuje API.
        """
        raw_cookie = None
        for cookie in self._session.cookie_jar:
            if cookie.key == SESSION_COOKIE:
                raw_cookie = cookie.value
                break
        if not raw_cookie:
            return None

        parts = raw_cookie.split(".")
        candidates = [parts[1]] if len(parts) >= 2 else parts
        for chunk in candidates:
            try:
                decoded = base64.urlsafe_b64decode(chunk + "=" * (-len(chunk) % 4))
            except (binascii.Error, ValueError):
                continue
            match = TOKEN_PATTERN.search(decoded)
            if match:
                return match.group(0).decode("ascii")
        return None

    @staticmethod
    def _token_from_html(html: str) -> str | None:
        """Zapasowo: token bywa tez wstrzykniety w strone panelu."""
        match = TOKEN_PATTERN.search(html.encode("utf-8", "ignore"))
        return match.group(0).decode("ascii") if match else None

    def _api_headers(self) -> dict[str, str]:
        headers = {
            **DEFAULT_HEADERS,
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self._host}/{self._locale}",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    # ------------------------------------------------------------------ dane
    async def _get_current_user(self) -> dict[str, Any] | None:
        """Zwroc dane konta albo None, gdy sesja wygasla."""
        url = f"{self._host}/api/current_user"
        try:
            async with self._session.get(url, headers=self._api_headers()) as resp:
                status = resp.status
                ctype = resp.headers.get("content-type", "")
                if status in (401, 403):
                    _LOGGER.debug("/api/current_user -> HTTP %s (brak sesji)", status)
                    self._token = None
                    return None
                if status >= 400:
                    body = (await resp.text())[:400]
                    _LOGGER.debug(
                        "/api/current_user -> HTTP %s (%s). Odpowiedz: %s", status, ctype, body
                    )
                    # portal zwraca 500 takze wtedy, gdy sesja jest niewazna
                    if status == 500:
                        self._token = None
                        return None
                    raise KaisaiConnectionError(f"/api/current_user zwrocilo HTTP {status}")
                if "json" not in ctype:
                    _LOGGER.debug("/api/current_user zwrocilo %s zamiast JSON", ctype)
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
            raise KaisaiAuthError(
                "Zalogowano, ale /api/current_user nie zwrocilo danych (portal odpowiada "
                "bledem 500 lub HTML-em). Wlacz debug dla custom_components.kaisai_ksm."
            )
        return data

    async def async_get_definitions(self, gate_id: int) -> dict[str, Any] | None:
        """Pobierz definicje parametrow bramki (listy wartosci dla enumow).

        Portal wystawia je pod /api/gates/{gate}/device_definitions/list.
        Ksztalt odpowiedzi nie jest udokumentowany, wiec zwracamy surowy JSON,
        a wyciaganiem opcji zajmuje sie extract_options().
        """
        url = f"{self._host}/api/gates/{gate_id}/device_definitions/list"
        try:
            async with self._session.get(url, headers=self._api_headers()) as resp:
                if resp.status >= 400:
                    _LOGGER.debug(
                        "device_definitions/list -> HTTP %s (%s)",
                        resp.status,
                        (await resp.text())[:200],
                    )
                    return None
                if "json" not in resp.headers.get("content-type", ""):
                    return None
                return await resp.json()
        except aiohttp.ClientError as err:
            _LOGGER.debug("Nie udalo sie pobrac definicji: %s", err)
            return None

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
        headers = self._api_headers()
        if self._csrf:
            headers["x-csrf-token"] = self._csrf

        # Kolejnosc wynika z obserwacji: POST na ten adres zwraca 404, a PUT 400,
        # czyli metoda jest dobra, a chodzilo o strukture ciala. Biblioteka
        # compit-inext-api uzywa klucza "values" - i to jest wariant pierwszy.
        variants: list[tuple[str, dict[str, Any]]] = [
            ("put", {"values": [{"code": code, "value": value}]}),
            ("put", {"params": [{"code": code, "value": value}]}),
            ("put", {"code": code, "value": value}),
            ("post", {"values": [{"code": code, "value": value}]}),
        ]

        problems: list[str] = []
        for method, body in variants:
            try:
                async with self._session.request(
                    method, url, json=body, headers=headers
                ) as resp:
                    text = await resp.text()
                    if resp.status < 300:
                        _LOGGER.debug(
                            "Zapis %s=%s OK (%s %s) -> %s",
                            code,
                            value,
                            method.upper(),
                            body,
                            text[:200],
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


def as_int(raw: Any) -> int | None:
    """Zamien wartosc parametru na liczbe.

    Portal Kaisai raz oddaje numer trybu jako liczbe, raz jako napis ("3"),
    a bywa, ze z podkreslnikiem z przodu. Bez tego encje select i switch nie
    potrafily dopasowac biezacej wartosci do listy i pokazywaly "unknown".
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        digits = raw.strip().lstrip("_")
        if digits.lstrip("-").isdigit():
            return int(digits)
    return None


def extract_options(definitions: Any, code: str) -> dict[int, str]:
    """Wyciagnij mape {wartosc: etykieta} dla podanego kodu parametru.

    Struktura odpowiedzi portalu nie jest udokumentowana i potrafi sie roznic
    miedzy wersjami, wiec zamiast zakladac konkretny ksztalt przechodzimy cale
    drzewo i szukamy obiektu opisujacego ten parametr. Za liste wartosci
    uznajemy dowolna liste slownikow, w ktorych jest cos wygladajacego na
    wartosc i cos wygladajacego na etykiete.
    """
    found: dict[int, str] = {}

    label_keys = ("label", "value_label", "name", "text", "title")
    value_keys = ("value", "id", "key")
    list_keys = ("values", "options", "enum", "choices", "items", "list")


    def harvest(candidate: Any) -> dict[int, str]:
        out: dict[int, str] = {}
        if not isinstance(candidate, list):
            return out
        for item in candidate:
            if not isinstance(item, dict):
                continue
            label = next(
                (item[k] for k in label_keys if isinstance(item.get(k), str)), None
            )
            value = next(
                (as_int(item[k]) for k in value_keys if as_int(item.get(k)) is not None),
                None,
            )
            if label is not None and value is not None:
                out[value] = label
        return out

    def walk(node: Any) -> None:
        nonlocal found
        if found:
            return
        if isinstance(node, dict):
            if node.get("code") == code:
                for key in list_keys:
                    harvested = harvest(node.get(key))
                    if harvested:
                        found = harvested
                        return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(definitions)
    return found
