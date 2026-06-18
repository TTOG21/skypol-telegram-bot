<!-- AGENTS.md für Skypol Arts & Media Telegram Support Bot -->
<!-- Diese Datei richtet sich an KI-Coding-Agenten, die mit diesem Projekt arbeiten. -->

## Projektübersicht

Dies ist ein mehrsprachiger Telegram-Support-Bot für **Skypol Arts & Media**.
Er beantwortet Kundenfragen in privaten Chats und Gruppen auf Basis der Informationen aus der YAML-Wissensdatenbank (`data/knowledge_base.yaml`).
Unterstützte Sprachen für UI-Texte und Trigger sind **Deutsch, Griechisch und Englisch**.
Die LLM-Antworten werden im Code explizit auf **Deutsch mit formalem „Sie“-Ton** festgelegt, unabhängig von der Eingabesprache des Nutzers.

Der Bot kann lokal im Polling-Modus getestet und produktiv auf **Render** als Webhook-Dienst betrieben werden.

## Technologie-Stack

- **Python** 3.11+ (asynchron, `async`/`await`)
- **python-telegram-bot** `>=20.7` mit Webhook-Unterstützung
- **FastAPI** `>=0.111.0` + **Uvicorn** als Webhook-HTTP-Server
- **Anthropic** (`AsyncAnthropic`) oder **OpenAI-kompatible APIs** (`AsyncOpenAI`, z. B. OpenRouter, Kimi/Moonshot)
- **PyYAML** zum Laden der Wissensdatenbank
- **python-dotenv** für `.env`-Konfiguration
- **httpx** für Hilfsskripte
- **pytest** + **pytest-cov** für Tests (`pytest-cov>=5.0.0` ist in `requirements.txt` enthalten)
- **SQLite** (stdlib `sqlite3`) für Persistenz
- **GitHub Actions** CI

Es gibt **kein** `pyproject.toml`, `setup.py`, `package.json` oder ähnliches.
Die Abhängigkeiten werden ausschließlich über `requirements.txt` verwaltet.

## Projektstruktur

```
.
├── data/
│   └── knowledge_base.yaml      # Wissensdatenbank des Unternehmens
├── src/
│   ├── __init__.py              # leer
│   ├── main.py                  # FastAPI-App, Webhook-Endpunkt, Startup/Shutdown, Health/Metrics
│   ├── bot.py                   # Telegram-Handler, Befehle, Callbacks, Nachrichtenverarbeitung
│   ├── config.py                # Laden und Validieren der Umgebungsvariablen
│   ├── knowledge.py             # KnowledgeBase-Loader, Retriever, Exact-Match-FAQ, gelernte FAQs
│   ├── llm.py                   # Anthropic- oder OpenAI-kompatible LLM-Clients, Guard, Cache
│   ├── memory.py                # Gesprächsspeicher mit optionaler SQLite-Persistenz
│   ├── utils.py                 # Spracherkennung, Gruppen-Trigger, Formatierung, Sanitization
│   ├── analytics.py             # Datenbank-gestützte Statistiken
│   ├── tickets.py               # Support-Ticket-System (Fassade über database.py)
│   ├── database.py              # SQLite-Datenbankschicht (Users, Tickets, Memory, Feedback, Gaps, ...)
│   ├── moderation.py            # Flood-Schutz und Admin-Checks
│   └── logging_config.py        # Zentrale Logging-Konfiguration (Text/JSON, rotierende Datei, Token-Redaktion)
├── tests/
│   ├── __init__.py              # leer
│   ├── test_main.py             # Import-Test für main + Rate-Limiter
│   ├── test_utils.py            # Tests für Sprache, Trigger, KnowledgeBase, Memory, Formatierung
│   ├── test_webhook.py          # Health- und Webhook-Endpunkt-Tests mit FastAPI TestClient
│   ├── test_enhancements.py     # Tests für Analytics, Tickets, Moderation, Admin-Checks
│   ├── test_llm.py              # Tests für LLM-Validierung, Timeout, Retry, Cache, ResponseGuard
│   ├── test_bot.py              # Tests für Telegram-Handler, /setflood und Gruppen-Moderation
│   ├── test_memory.py           # Tests für Gesprächsspeicher und Persistenz
│   ├── test_knowledge_learning.py # Tests für gelernte FAQs und Wissenslücken
│   └── test_logging_config.py   # Tests für Logging-Format, Level und Token-Redaktion
├── get_chat_id.py               # Hilfsskript zur Ermittlung der ADMIN_CHAT_ID
├── reset_webhook.py             # Löscht Webhook und ausstehende Updates
├── run_polling.py               # Lokaler Polling-Modus
├── requirements.txt             # Python-Abhängigkeiten
├── render.yaml                  # Render-Blueprint-Konfiguration
├── .env.example                 # Vorlage für Umgebungsvariablen
├── README.md                    # Benutzerdokumentation (deutsch)
└── AGENTS.md                    # Diese Datei
```

## Build- und Startbefehle

### Lokale Einrichtung

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

`.env` aus der Vorlage erstellen und ausfüllen:

```bash
cp .env.example .env
```

### Lokaler Test (Polling)

```bash
python run_polling.py
```

Hinweis: Während ein Webhook auf Render aktiv ist, darf der Polling-Modus nicht gleichzeitig laufen – das führt zu einem `409 Conflict`.
In dem Fall alle Instanzen stoppen, `python reset_webhook.py` ausführen und neu starten.

### Produktivbetrieb (Webhook)

```bash
uvicorn src.main:app --host 0.0.0.0 --port $PORT
```

Auf Render wird genau dieser Startbefehl aus `render.yaml` verwendet.
`RENDER_EXTERNAL_URL` muss nach dem ersten Deploy auf die Render-URL gesetzt werden, damit der Webhook registriert wird.

## Laufzeitarchitektur

1. `src/main.py` erstellt beim Import die Telegram-`Application` (`create_application()` aus `src/bot.py`) und die FastAPI-App.
   Beim Import werden außerdem die Pflichtfelder `TELEGRAM_BOT_TOKEN`, `LLM_API_KEY` und `LLM_PROVIDER` validiert.
2. Beim Startup (FastAPI-`lifespan`) wird die Telegram-App initialisiert und gestartet.
   Es werden Bot-Kommandomenüs für private und Gruppenchats registriert, ein Selbsttest für Telegram-Token und LLM-Client ausgeführt und,
   falls `RENDER_EXTERNAL_URL` gesetzt ist, der Webhook auf `{RENDER_EXTERNAL_URL}/webhook` gesetzt.
3. Telegram sendet Updates per POST an `/webhook`.
   Dort wird optional das Header-Feld `X-Telegram-Bot-Api-Secret-Token` gegen `WEBHOOK_SECRET` geprüft,
   die Quell-IP gegen ein einfaches Rate-Limit geprüft, die Payload-Größe auf maximal 1 MB begrenzt
   und ein gültiges `update_id` erwartet, bevor `Update.de_json()` aufgerufen wird.
   Ungültige Payloads werden mit `200 OK` beantwortet, damit Telegram keine Retries sendet.
4. `src/bot.py` verteilt die Updates auf Command-Handler, Callback-Handler, ChatMember-Handler und den allgemeinen Text-Handler.
5. Freitextnachrichten werden in `handle_message` verarbeitet:
   - Prüfung gegen die Blockliste.
   - In Gruppen antwortet der Bot nur bei Mention, Antwort auf seine Nachrichten, Bot-Namen ohne `@` oder definierten Schlüsselwörtern.
   - Keyword-Trigger verwenden Wortgrenzen, um Fehltriggerungen durch Teilwörter zu vermeiden.
   - Bei einem Gruppen-Trigger (ohne direkte Antwort auf den Bot) zeigt der Bot ein kompaktes Inline-Menü mit den wichtigsten Optionen an, anstatt sofort eine LLM-Antwort zu generieren.
   - Antworten auf Bot-Nachrichten in Gruppen werden weiterhin als Konversation per LLM beantwortet.
   - Flood-Schutz löscht bei zu vielen Nachrichten eines Nutzers innerhalb des konfigurierten Zeitfensters und schaltet den Nutzer vorübergehend stumm.
   - Die Sprache wird mit `detect_language` bzw. `get_user_language` bestimmt (Reihenfolge: DB-Präferenz → Telegram-`language_code` → Text-Heuristik).
   - Die Nachricht und der Gesprächsverlauf werden in `ConversationMemory` gespeichert.
   - Der LLM-Client (`src/llm.py`) wählt aus der Knowledge Base die für die Nutzerfrage relevantesten FAQ-Einträge und Leistungen aus (`find_relevant_context`) und injiziert nur diesen fokussierten Kontext in den Prompt. Steht nichts Passendes zur Verfügung, fällt er auf den vollen Kontext zurück.
   - FAQ-Einträge und Services werden einmalig beim Start vor-tokenisiert, um wiederholte Berechnungen zu vermeiden.
   - Eingehende Texte werden vor Speicherung und LLM-Aufruf bereinigt (`sanitize_input`).
   - Wenn die Nutzerfrage exakt einer FAQ-Frage entspricht, wird die hinterlegte Antwort direkt ausgegeben (kein LLM-Aufruf). Gelernte FAQs werden vor den statischen FAQs geprüft.
   - Jede LLM-Antwort wird auf verbotene Preisangaben, informelle Anrede und Instruction-Leaks geprüft (`ResponseGuard`, `validate_response`).
   - Identische Nutzerfragen werden über einen TTL-Cache beantwortet, ohne erneuten API-Aufruf.
   - Die Antwort wird escaped und mit `ParseMode.MARKDOWN` gesendet.
   - Fallback-Antworten werden als unbeantwortete Fragen (`unanswered_questions`) in der Datenbank erfasst.
6. Die JobQueue führt zwei wiederkehrende Jobs aus:
   - `_ticket_reminder_job` erinnert Admins alle 4 Stunden an offene Tickets.
   - `_auto_close_job` schließt Tickets nach 7 Tagen Inaktivität automatisch.

## Code-Organisation und Module

- `src/config.py` – Zentrale Konfiguration. Lädt `.env` aus dem Projektroot, definierte Pfade und validiert Pflichtfelder (`TELEGRAM_BOT_TOKEN`, `LLM_API_KEY`, `LLM_PROVIDER`).
- `src/knowledge.py` – Lädt `data/knowledge_base.yaml`, stellt Zugriffsmethoden bereit und implementiert mit `find_relevant_context()` einen einfachen Retriever für fokussierte Prompt-Kontexte. Unterstützt gelernte FAQs aus der Datenbank (`add_learned_faq`), die vor den statischen FAQs in Exact-Match und Relevanz-Scoring einbezogen werden.
- `src/llm.py` – Abstrakte `BaseLLMClient`-Klasse mit System-Prompt-Builder, fokussiertem Kontext, `ResponseGuard`, Response-Validierung, Timeout, Retry und einem TTL-basierten Response-Cache. Konkrete Implementierungen `AnthropicClient` und `OpenAICompatibleClient`.
- `src/logging_config.py` – Zentrale Logging-Konfiguration. Unterstützt textbasierte (Default) und JSON-Ausgabe (`LOG_FORMAT=json`), konfigurierbares `LOG_LEVEL` sowie optionalen rotierenden Datei-Output (`LOG_FILE`, `LOG_FILE_MAX_BYTES`, `LOG_FILE_BACKUPS`). Beide Formatter reden das Bot-Token (`<REDACTED>`).
- `src/memory.py` – `ConversationMemory` mit `collections.deque` pro `(chat_id, user_id)`, begrenzt durch `MAX_HISTORY`, optional mit **SQLite-Persistenz** (`PERSIST_MEMORY=true`). Die Datenbank ist die Source of Truth; ein kleiner In-Memory-Cache hält zuletzt genutzte Verläufe hot.
- `src/utils.py` – Spracherkennung, Gruppen-Trigger-Logik (ladbar aus `data/knowledge_base.yaml`), Input-Sanitization, Markdown-Escaping, Formatierung von Services, FAQ, About, Testimonials, Booking, Social und Location.
- `src/analytics.py` – Datenbank-gestützter Tracker für Nachrichten, Befehle, Support-Tickets und Flood-Events. `get_stats()` liefert Werte für `/stats` und `/metrics`.
- `src/tickets.py` – Unterstützungssystem mit Erstellen, Nachrichten hinzufügen, Schließen, Listen, Export und automatischem Schließen inaktiver Tickets. Dünne Fassade über `src/database.py`.
- `src/database.py` – SQLite-Datenbankschicht für Nutzer, Tickets, Gesprächsverlauf, Feedback, unbeantwortete Fragen, gelernte FAQs, Blockliste, Flood-Tracking, Analytics und Sprachpräferenzen.
- `src/moderation.py` – Per-Chat-Flood-Schutz mit Datenbank-Backend, Laufzeit-Konfiguration der Thresholds und Hilfsfunktion `is_admin_user` zur Admin-Prüfung.
- `src/bot.py` – Alle Telegram-Handler, Inline-Tastaturen, Gruppen-Admin-Features (Willkommensnachricht, Pin-Menü), Ticket-System, Feedback, Wissenslücken, Erinnerungsjobs, Analytics, Moderation und Fehlerbehandlung.

## Datenbankschema

Die SQLite-Datenbank (Default: `.bot_data.db`) enthält folgende Tabellen:

| Tabelle | Zweck |
|---------|-------|
| `tickets` | Support-Tickets mit Status, Nachrichten-JSON und Timestamps |
| `memory` | Persistierter Gesprächsverlauf pro `chat_id:user_id` |
| `analytics` | Event-Stream für Nachrichten, Befehle, Tickets, Flood-Events |
| `flood_events` | Zeitstempel-basiertes Flood-Tracking pro Nutzer/Chat |
| `user_preferences` | Bevorzugte UI-Sprache pro Nutzer |
| `users` | Nutzerverzeichnis für Broadcasts, Export und Tracking |
| `blocked_users` | Bot-Blockliste mit optionalem Grund |
| `feedback` | Nutzer-Feedback und Sterne-Bewertungen |
| `unanswered_questions` | Als Wissenslücken erfasste Fragen |
| `learned_faq` | Von Admins gelernte FAQ-Einträge |

## Konfiguration

Alle sensiblen Werte kommen aus `.env` (siehe `.env.example`):

| Variable | Bedeutung | Pflicht |
|----------|-----------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot-Token von @BotFather | Ja |
| `LLM_PROVIDER` | `anthropic` oder `openai` | Ja (Default intern: `openai`; `.env.example`/`render.yaml`: `anthropic`) |
| `LLM_API_KEY` | API-Key für den gewählten Provider | Ja |
| `LLM_BASE_URL` | OpenAI-kompatible Base-URL (z. B. Kimi/Moonshot/OpenRouter) | Optional |
| `LLM_MODEL` | Modellname | Optional (Default: `kimi-k2-0711-preview`) |
| `LLM_TIMEOUT` | Timeout in Sekunden für einen LLM-API-Aufruf | Optional (Default: `30`) |
| `LLM_MAX_RETRIES` | Anzahl der Wiederholungen bei temporären LLM-Fehlern | Optional (Default: `1`) |
| `LLM_CACHE_TTL_SECONDS` | Cache-Lebensdauer für identische LLM-Anfragen in Sekunden | Optional (Default: `300`) |
| `LLM_CACHE_MAX_ENTRIES` | Maximale Anzahl zwischengespeicherter LLM-Antworten | Optional (Default: `1000`) |
| `RENDER_EXTERNAL_URL` | Öffentliche URL für Webhook (z. B. `https://...onrender.com`) | Optional |
| `ADMIN_CHAT_ID` | Chat-ID(s) für Benachrichtigungen bei `/human` und Admin-Befehle | Optional |
| `WEBHOOK_SECRET` | Geheimer Token für Webhook-Header | Optional |
| `MAX_HISTORY` | Gesprächsverlauf pro User | Optional (Default: `10`) |
| `FLOOD_MAX_MESSAGES` | Maximale Nachrichtenanzahl pro Nutzer im Flood-Fenster | Optional (Default: `5`) |
| `FLOOD_WINDOW_SECONDS` | Dauer des Flood-Schutz-Fensters in Sekunden | Optional (Default: `10`) |
| `FLOOD_MUTE_SECONDS` | Dauer der automatischen Stummschaltung bei Flood in Sekunden | Optional (Default: `60`) |
| `WEBHOOK_RATE_LIMIT_RPS` | Maximale Webhook-Anfragen pro Sekunde pro IP | Optional (Default: `10`) |
| `WEBHOOK_RATE_LIMIT_WINDOW` | Zeitfenster für Webhook-Rate-Limit in Sekunden | Optional (Default: `1`) |
| `PERSIST_MEMORY` | Gesprächsspeicher in SQLite persistieren (`true`/`false`) | Optional (Default: `false`) |
| `DATABASE_PATH` | Pfad zur SQLite-Datenbank | Optional (Default: `.bot_data.db`) |
| `LOG_LEVEL` | Logging-Level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | Optional (Default: `INFO`) |
| `LOG_FORMAT` | Logging-Format: `text` oder `json` | Optional (Default: `text`) |
| `LOG_FILE` | Pfad zur optionalen Log-Datei | Optional (Default: leer) |
| `LOG_FILE_MAX_BYTES` | Maximale Größe einer Log-Datei vor Rotation | Optional (Default: `10485760`) |
| `LOG_FILE_BACKUPS` | Anzahl aufzubewahrender Backup-Log-Dateien | Optional (Default: `5`) |

Hinweise:

- `src/config.py` bietet Rückwärtskompatibilität zu älteren Variablennamen (`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`).
- `ADMIN_CHAT_ID` unterstützt eine einzelne numerische ID, mehrere komma-getrennte IDs oder Kanal-Usernames wie `@channelname`. Bei Gruppen-Moderationsbefehlen (`/warn`, `/mute`, `/kick`, `/ban`) wird zusätzlich der tatsächliche Gruppen-Admin-Status geprüft; Kanal-Usernames werden dabei nicht als Admin-User erkannt.
- `LLM_PROVIDER` wird in `src/config.py` auf `"anthropic"` oder `"openai"` normalisiert. Ist die Variable nicht gesetzt, fällt `config.py` intern auf `"openai"` zurück; `.env.example` und `render.yaml` setzen sie explizit auf `"anthropic"`.
- `render.yaml` verwendet `LLM_PROVIDER=anthropic` mit `LLM_BASE_URL=https://api.kimi.com/coding/` – das ist ein Anthropic-kompatibler Kimi-Endpunkt.
- `MEMORY_FILE_PATH` existiert als Legacy-Variable, wird aber nicht mehr verwendet; die Memory-Persistenz läuft über `DATABASE_PATH`.

## Befehlsübersicht

Nutzerbefehle:

| Befehl | Beschreibung |
|--------|--------------|
| `/start` | Willkommensnachricht mit Hauptmenü (privat) |
| `/menu` | Hauptmenü anzeigen (privat und Gruppe) |
| `/services` | Liste aller Leistungen |
| `/portfolio` | Portfolio-Kategorien und Link |
| `/about` | Über Skypol Arts & Media |
| `/faq` | Häufige Fragen |
| `/testimonials` | Kundenbewertungen |
| `/booking` | Termin buchen / Kontaktformular |
| `/social` | Social-Media-Kanäle |
| `/location` | Standort mit Google-Maps-Link |
| `/contact` | Kontaktdaten |
| `/human` | Anfrage an Menschen weiterleiten (erstellt Ticket) |
| `/pinmenu` | Hauptmenü in Gruppe anpinnen (nur als Admin) |
| `/reset` | Gespräch zurücksetzen |
| `/feedback <Text>` | Feedback an die Betreiber senden |
| `/language [de\|el\|en]` | Sprache für feste UI-Texte festlegen |
| `/help` | Hilfe |

Admin-Befehle:

| Befehl | Beschreibung |
|--------|--------------|
| `/stats` | Bot-Statistiken |
| `/reply` | Auf Ticket antworten: `/reply <id> <Text>`, auf weitergeleitete Nachricht antworten, oder den *Antworten*-Button unter einer Admin-Benachrichtigung nutzen (dann einfach die Antwort als normale Nachricht senden) |
| `/close` | Ticket schließen: `/close <id>` oder den *Schließen*-Button unter einer Admin-Benachrichtigung nutzen |
| `/cancel` | Laufende Ticket-Antwort abbrechen (Admin) |
| `/notifytest` | Testbenachrichtigung an alle konfigurierten Admins senden |
| `/setflood` | Flood-Schutz-Schwellen ändern: `/setflood <max_messages> <window_seconds>` |
| `/warn` | Nutzer verwarnen (auf Nachricht antworten) |
| `/mute` | Nutzer stummschalten (auf Nachricht antworten) |
| `/kick` | Nutzer kicken (auf Nachricht antworten) |
| `/ban` | Nutzer bannen (auf Nachricht antworten) |
| `/block <user_id> [reason]` | Nutzer für den Bot sperren (keine Befehle/Nachrichten mehr möglich) |
| `/unblock <user_id>` | Nutzer von der Bot-Sperrliste entfernen |
| `/tickets [page]` | Offene Support-Tickets auflisten (paginiert) |
| `/export [users\|tickets]` | CSV-Export der Nutzer- oder Ticket-Daten senden |
| `/broadcast <message>` | Nachricht an alle bekannten Nutzer senden |
| `/backup` | Aktuelle SQLite-Datenbank als Dokument an Admin senden |
| `/gaps` | Unbeantwortete Fragen (Wissenslücken) auflisten (Admin) |
| `/learn <gap_id> <Antwort>` | Aus einer Wissenslücke eine gelernte FAQ-Antwort erstellen (Admin) |
| `/learned` | Alle gelernten FAQ-Antworten auflisten (Admin) |

## Testbefehle

Die Tests liegen in `tests/` und funktionieren sowohl als ausführbare Skripte als auch unter `pytest`.

### Ausführung als Skripte

```bash
python tests/test_main.py
python tests/test_utils.py
python tests/test_webhook.py
python tests/test_enhancements.py
python tests/test_llm.py
python tests/test_bot.py
python tests/test_memory.py
python tests/test_knowledge_learning.py
python tests/test_logging_config.py
```

### Ausführung mit pytest

```bash
python -m pytest tests/
```

### Mit Coverage-Ziel (70 %)

```bash
python -m pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=70
```

Wichtig: `test_main.py` und `test_webhook.py` setzen Dummy-Umgebungsvariablen, bevor `src.main` importiert wird, da `main.py` beim Import die Konfiguration validiert.

## CI-Pipeline

`.github/workflows/ci.yml`:

- Auslösung bei `push` und `pull_request` auf `main`/`master`.
- Matrix: Python 3.11 und 3.12.
- Installiert Abhängigkeiten aus `requirements.txt`.
- Führt aus:
  ```bash
  python -m pytest tests/ -v
  ```
- Setzt dabei Dummy-Umgebungsvariablen:
  - `TELEGRAM_BOT_TOKEN`
  - `ANTHROPIC_API_KEY` (Rückwärtskompatibilitäts-Alias)
  - `DATABASE_PATH=/tmp/test_bot.db`

## Code-Style-Richtlinien

- **Sprache:** Kommentare und Dokumentation sind überwiegend auf Deutsch; Code-Bezeichner sind auf Englisch.
- **Formatierung:** 4 Leerzeichen Einrückung, Zeilenlänge nicht streng begrenzt, aber lesbar halten.
- **Import-Reihenfolge:** Standardbibliothek → Drittanbieter → eigene `src`-Module.
- **Async:** Fast alle Telegram-Handler und LLM-Aufrufe sind `async`.
- **Logging:** Verwendung des Standard-`logging`-Moduls mit dem Format `%(asctime)s - %(name)s - %(levelname)s - %(message)s`.
- **UI-Texte:** Mehrsprachige Strings werden in Dictionary-Strukturen mit den Schlüsseln `de`, `el`, `en` gehalten.
- **Minimalismus:** Änderungen sollten so klein wie möglich sein und dem bestehenden Stil folgen.

## Sicherheitsaspekte

- **Secrets:** `TELEGRAM_BOT_TOKEN` und `LLM_API_KEY` werden aus `.env` geladen und dürfen niemals committet werden (`.gitignore` enthält `.env` und `venv/`).
- **Webhook-Sicherheit:** Optionaler `WEBHOOK_SECRET` wird im Header `X-Telegram-Bot-Api-Secret-Token` geprüft. Der `/webhook`-Endpunkt validiert außerdem Payload-Größe und `update_id`, bevor `Update.de_json()` aufgerufen wird. Jede erfolgreich deserialisierte Webhook-Anfrage wird mit `update_id`, `chat_id` und Quell-IP geloggt (kein Nachrichtentext). Ein einfacher IP-basierter Rate-Limiter schützt vor Überlastung. Ohne Secret wird jede Anfrage akzeptiert.
- **Datenschutz:** Standardmäßig liegen Gespräche im Arbeitsspeicher und sind pro User begrenzt (`MAX_HISTORY`). `/reset` löscht den Speicher sofort. Mit `PERSIST_MEMORY=true` werden Gespräche in der SQLite-Datenbank gespeichert; die Datenbankdatei muss dann entsprechend geschützt werden.
- **Gruppen-Spam-Schutz:** In Gruppen antwortet der Bot nur bei `@botname`-Mention, Antworten auf seine Nachrichten, dem Bot-Namen ohne `@` oder definierten Schlüsselwörtern in `src/utils.py`. Damit Keyword-Trigger in Gruppen funktionieren, muss der Privacy Mode des Bots in BotFather deaktiviert sein.
- **Admin-Prüfung:** Admin-Befehle prüfen, ob die `user_id` des Absenders in `ADMIN_CHAT_ID` enthalten ist. In Gruppen werden Moderationsbefehle (`/warn`, `/mute`, `/kick`, `/ban`) zusätzlich gegen den tatsächlichen Gruppen-Admin-Status des Absenders geprüft. Kanal-Usernames in `ADMIN_CHAT_ID` werden dabei nicht als Admin-User erkannt.
- **User-Directory & Blockliste:** Bei jeder Interaktion werden Nutzer in der SQLite-Datenbank (`users`) erfasst oder aktualisiert. Über `/block <user_id>` können Admins Nutzer sperren; gesperrte Nutzer erhalten keine Antworten mehr.
- **Feedback & Bewertungen:** Nutzer können mit `/feedback <Text>` Feedback hinterlassen. Nach dem Schließen eines Tickets werden automatisch 1-5-Sterne-Bewertungen erfragt und in der Datenbank gespeichert.
- **Wissenslücken & Selbstlernen:** Wenn der Bot auf eine Frage mit der sicheren Fallback-Antwort antwortet, wird die Nutzerfrage als `unanswered_questions` in der Datenbank erfasst. Admins können sie mit `/gaps` einsehen; wiederkehrende Fragen werden priorisiert. Mit `/learn <gap_id> <Antwort>` entsteht eine gelernte FAQ, die sofort in Exact-Match und Prompt-Kontext genutzt wird. `/learned` listet den aktuellen Bestand.
- **Erinnerungen:** Ein JobQueue-Job erinnert Admins alle 4 Stunden an offene Tickets; ein weiterer Job schließt Tickets nach 7 Tagen Inaktivität.
- **Health & Metrics:** FastAPI liefert `/health` (DB, Telegram, LLM, Config) und `/metrics` (Prometheus-Textformat). Bei Fehlern wird Admins maximal alle 60 Sekunden eine Benachrichtigung gesendet.
- **Logging:** Logs können wahlweise als Text oder JSON ausgegeben werden. Optional erfolgt eine rotierende Dateiausgabe über `LOG_FILE`.
- **LLM-Kontrolle:** Der System-Prompt in `src/llm.py` zwingt den Bot zu einem deutschen, formellen Ton und verbietet Preisangaben, die nicht in der Knowledge Base stehen. Zusätzlich prüft `BaseLLMClient.validate_response()` Preismuster und der `ResponseGuard` prüft formelle Anrede sowie Instruction-Leaks. Bei Verstoß fällt der Bot auf eine sichere Vorlage zurück.
- **Markdown:** `escape_markdown` und `escape_markdown_basic` sind vorhanden. Formatierungen nutzen überwiegend das ältere Telegram-Markdown (`ParseMode.MARKDOWN`).

## Deployment

Das Projekt ist für Render vorkonfiguriert:

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `uvicorn src.main:app --host 0.0.0.0 --port $PORT`
- **Blueprint:** `render.yaml` definiert den Web-Service inklusive Standard-Umgebungsvariablen.

Ablauf:

1. Repository auf GitHub hochladen.
2. Neuen Web Service aus dem Blueprint `render.yaml` erstellen.
3. Alle `sync: false`-Umgebungsvariablen (Token, API-Key, URL, Admin-ID, Secret) in Render eintragen.
4. Erstes Deploy durchführen.
5. `RENDER_EXTERNAL_URL` auf die tatsächliche Render-URL setzen und neu deployen, damit der Webhook registriert wird.

## Nützliche Hilfsskripte

- `python get_chat_id.py` – Startet einen kurzen Polling-Bot, der die eigene Chat-ID ausgibt (nützlich für `ADMIN_CHAT_ID`).
- `python reset_webhook.py` – Löscht Webhook und ausstehende Updates bei `409 Conflict`.
- `python run_polling.py` – Lokale Entwicklung im Polling-Modus.

## Wissensdatenbank aktualisieren

Inhalte des Bots werden in `data/knowledge_base.yaml` gepflegt.
Dort können Services, FAQ-Einträge, Kontaktdaten, Portfolio, Testimonials, Buchungsinformationen und Willkommensnachrichten angepasst werden.
Der Bot lädt die Datei beim Start neu; bei laufendem Webhook-Modus ist ein Neustart des Dienstes nötig.

## Hinweise für Agenten

- `src/main.py` validiert die Konfiguration beim Import. Beim Schreiben von Tests daher Dummy-Umgebungsvariablen setzen, bevor `main` importiert wird.
- Die mehrsprachige Ausgabe betrifft primär feste UI-Texte; der LLM-Client antwortet immer auf Deutsch.
- Neue Befehle oder Callback-Daten sollten in `src/bot.py` registriert und in der Haupttastatur (`main_menu_keyboard`) ergänzt werden.
- Änderungen an Gruppen-Trigger-Wörtern müssen in `src/utils.py` in `GROUP_KEYWORDS` erfolgen.
- Befehle, die in Gruppen automatisch gelöscht werden sollen, können mit dem `@cleanup_command()`-Dekorator versehen werden.
- Befehle, die in der Analytics-Statistik erfasst werden sollen, können mit dem `@tracked_command("name")`-Dekorator versehen werden.
- Memory-Persistenz läuft über SQLite (`PERSIST_MEMORY=true`, `DATABASE_PATH`), nicht über eine JSON-Datei.
- Analytics-Werte stammen aus der SQLite-Datenbank; `started_at` ist der einzige in-memory-Wert.
