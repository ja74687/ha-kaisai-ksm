<p align="center">
  <img src="https://raw.githubusercontent.com/ja74687/ha-kaisai-ksm/main/assets/icon-256.png" alt="ha-kaisai-ksm" width="120" height="120" />
</p>

<h1 align="center">Kaisai KSM — integracja Home Assistant</h1>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom" /></a>
  <a href="https://buycoffee.to/softime-pk" target="_blank"><img src="https://img.shields.io/badge/%E2%98%95-Postaw%20mi%20kaw%C4%99-FFDD00?style=flat&labelColor=000000" alt="Postaw mi kawę na buycoffee.to" /></a>
</p>

Nieoficjalna integracja Home Assistant dla pomp ciepła **Kaisai** wyposażonych w
moduł **KSM / ZNS (Zdalny Nadzór Serwisowy)**, obsługiwany przez portal
[sterowanie.kaisai.com](https://sterowanie.kaisai.com) i aplikację
„Sterowanie Kaisai ZNS".

> ⚠️ Projekt niezależny, niezwiązany z firmami Kaisai ani Compit.
> Nazwy handlowe użyte wyłącznie w celu opisania zgodności.

## Po co to komu

Pompy Kaisai serii R290 (klony Midea M-Thermal) nie mają oficjalnej integracji
z Home Assistant, a dostęp do magistrali Modbus zwykle oznacza zerwanie plomby
gwarancyjnej. Moduł KSM wysyła jednak komplet telemetrii do chmury producenta —
i to z niej korzysta ta integracja. **Nic nie trzeba otwierać ani przerabiać.**

## Co daje

**Odczyty** (ok. 30 encji, zależnie od modelu):

- temperatura zewnętrzna, zasobnika CWU, bufora, pomieszczenia
- temperatura zasilania i powrotu, przepływ wody
- częstotliwość sprężarki, obroty wentylatorów
- napięcie i prąd AC, prąd sprężarki, napięcie szyny DC
- pełna diagnostyka obiegu chłodniczego (ssanie, tłoczenie, parownik, EVI, ciśnienie)

**Sensory wyliczane** — to, czego nie daje ani portal, ani aplikacja:

| Encja | Jak liczona |
|---|---|
| `sensor.moc_cieplna` | przepływ × 1,163 × (zasilanie − powrót) |
| `sensor.pobor_mocy` | √3 × napięcie × prąd × cosφ (lub bez √3 dla 1 fazy) |
| `sensor.cop` | moc cieplna ÷ pobór mocy |

**Sterowanie** (encje `number`):

- nastawa temperatury CWU (R01)
- nastawa temperatury grzania (R02)

Zakresy min/max integracja bierze wprost z API, więc nie da się ustawić
wartości spoza tego, co dopuszcza sterownik.

Zmiana nastawy pokazuje się natychmiast, choć potwierdzenie z chmury przychodzi
z opóźnieniem — droga prowadzi przez bramkę KSM aż do sterownika pompy i wraca.
Do czasu potwierdzenia encja ma atrybut `oczekuje_na_potwierdzenie: true`
oraz `wartosc_w_portalu` z wartością, którą wciąż raportuje portal.

## Instalacja

### HACS (zalecane)

1. HACS → Integracje → ⋮ → **Repozytoria niestandardowe**
2. Adres tego repozytorium, kategoria **Integration**
3. Zainstaluj **Kaisai KSM** i zrestartuj Home Assistant

### Ręcznie

Skopiuj katalog `custom_components/kaisai_ksm` do `config/custom_components/`
i zrestartuj Home Assistant.

## Konfiguracja

**Ustawienia → Urządzenia i usługi → Dodaj integrację → Kaisai KSM**

| Pole | Opis |
|---|---|
| E-mail / Hasło | dane logowania do portalu (te same co do aplikacji) |
| Adres portalu | domyślnie `https://sterowanie.kaisai.com` |
| Liczba faz | 3 dla pomp trójfazowych, 1 dla jednofazowych |
| Współczynnik mocy | domyślnie 0,95 — wpływa tylko na wyliczany pobór mocy |
| Częstotliwość odpytywania | domyślnie 60 s |

Nie ustawiaj odpytywania częściej niż co 30 s — to chmura producenta,
a dane i tak odświeżają się rzadziej.

## Zużycie energii i COP dobowy

Integracja podaje moc chwilową. Żeby dostać kWh i średni COP, dodaj w
`configuration.yaml` całkowanie Riemanna:

```yaml
sensor:
  - platform: integration
    source: sensor.pobor_mocy
    name: Pompa energia elektryczna
    unit_time: h
    method: left
    max_sub_interval:
      minutes: 5

  - platform: integration
    source: sensor.moc_cieplna
    name: Pompa energia cieplna
    unit_time: h
    method: left
    max_sub_interval:
      minutes: 5
```

Średni COP za dobę to iloraz dobowych `utility_meter` z obu tych liczników —
i to jest znacznie uczciwsza miara niż COP chwilowy.

## Znane ograniczenia

- **Zapis nastaw jest eksperymentalny.** Format zapytania zapisującego nie
  został jeszcze potwierdzony na wszystkich wersjach portalu — integracja
  próbuje kilku wariantów i loguje odpowiedzi serwera. Jeśli zapis nie działa,
  włącz debug (niżej) i zgłoś issue z treścią logu.
- Integracja czyta z chmury producenta — bez internetu nie działa.
- Nazwy parametrów pochodzą z modelu R290/KHX. Inne serie mogą wystawiać inny
  zestaw kodów; nieznane kody są po prostu pomijane (zgłoś je w issue, dopiszemy).

## Debugowanie

```yaml
logger:
  default: info
  logs:
    custom_components.kaisai_ksm: debug
```

## Jak to działa

Portal to aplikacja Phoenix/Elixir. Integracja:

1. pobiera stronę logowania i wyciąga z niej token CSRF,
2. wysyła `POST /pl/login` z polami `_csrf_token`, `email`, `password`,
3. zapamiętuje ciasteczko sesji `_compit_key`,
4. wyciąga z tego ciasteczka token JWT (Guardian) — samo ciasteczko nie
   wystarcza, API wymaga nagłówka `Authorization: Bearer`, a token siedzi
   w zakodowanej mapie sesji Phoenixa,
5. odpytuje `GET /api/current_user` — jeden endpoint zwraca konto, bramki,
   urządzenia i pełny stan każdego z nich,
6. przy wygaśnięciu sesji loguje się ponownie automatycznie.

Bez nagłówka z tokenem portal odpowiada `HTTP 500`, a nie `401` — stąd
dodatkowa obsługa: pięćsetka z API jest traktowana jak nieważna sesja.

## Wydawanie nowej wersji

Release powstaje automatycznie. Wystarczy podbić `version` w
`custom_components/kaisai_ksm/manifest.json`, zacommitować i wypchnąć na `main` —
workflow sam utworzy tag, release i załączy spakowaną integrację.

Każdy push jest dodatkowo sprawdzany przez hassfest (walidacja manifestu
Home Assistant) i walidator HACS.

## Wesprzyj projekt

Jeśli ta integracja oszczędziła Ci wieczoru z dokumentacją Modbusa albo
uratowała gwarancję pompy — możesz postawić mi kawę:

<a href="https://buycoffee.to/softime-pk" target="_blank"><img src="https://buycoffee.to/static/img/share/share-button-primary.png" width="254" height="66" alt="Postaw mi kawę na buycoffee.to"></a>

## Licencja

Apache License 2.0.

Inspirowane biblioteką [compit-inext-api](https://github.com/Przemko92/compit-inext-api)
oraz [integracją Compit dla Home Assistant](https://github.com/CompitHomeAssistant/HomeAssistant)
(obie na Apache 2.0). Kod tej integracji napisano od zera na podstawie
obserwacji publicznego API portalu — serwer Kaisai nie udostępnia API mobilnego
Compitu, więc klient jest własny.
