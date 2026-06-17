# Skypol Arts & Media – Telegram Support Bot

Ein mehrsprachiger Telegram-Bot für **Skypol Arts & Media**, der Kunden auf Basis der Website-Informationen im privaten Chat und in Gruppen unterstützt.

## Features

- 🤖 Kundensupport für private Chats und Gruppen
- 🌍 Automatische Spracherkennung: **Deutsch, Griechisch, Englisch**
- 🧠 Antworten basierend auf dem Wissensstand der Website
- 📋 Befehle: `/start`, `/services`, `/portfolio`, `/about`, `/faq`, `/testimonials`, `/booking`, `/social`, `/location`, `/contact`, `/human`, `/pinmenu`, `/reset`, `/help`, `/language`
- 🎫 Support-Ticket-System mit Admin-Antworten (`/reply`, `/close`, `/tickets`)
- 📊 Analytics & Statistiken (`/stats`), CSV-Export (`/export`) und DB-Backup (`/backup`)
- 🛡️ Gruppen-Moderation (`/warn`, `/mute`, `/kick`, `/ban`, `/block`, `/unblock`, Flood-Schutz)
- 🧹 Automatisches Löschen von Befehlsnachrichten in Gruppen
- 🖱️ Interaktive Inline-Buttons
- 🚀 Bereit für Deployment auf **Render Free Tier**
- 🔒 Webhook-Betrieb mit optionalem Secret Token, Rate-Limiting und Health/Metrics-Endpunkten

## Tech Stack

- Python 3.11+
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) (async)
- [Anthropic Claude API](https://www.anthropic.com/api)
- FastAPI + Uvicorn (Webhook-Server)
- PyYAML (Wissensdatenbank)

## Projektstruktur

```
.
├── data/
│   └── knowledge_base.yaml      # Wissensdatenbank aus der Website
├── src/
│   ├── main.py                  # FastAPI App + Webhook
│   ├── bot.py                   # Telegram Handler & Commands
│   ├── config.py                # Umgebungsvariablen
│   ├── knowledge.py             # Wissensdatenbank-Loader
│   ├── llm.py                   # Anthropic Claude Client
│   ├── memory.py                # Gesprächsspeicher
│   └── utils.py                 # Hilfsfunktionen
├── .env.example                 # Beispiel-Konfiguration
├── requirements.txt
├── render.yaml                  # Render Deployment Config
└── README.md
```

## Einrichtung

### 1. Repository klonen

```bash
git clone <repo-url>
cd "skypol Telegram bot"
```

### 2. Virtuelle Umgebung erstellen

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Telegram Bot erstellen

1. Öffne Telegram und suche nach **@BotFather**.
2. Sende `/newbot` und folge den Anweisungen.
3. Kopiere den **Bot Token**.

### 4. Anthropic API Key

1. Gehe zu [console.anthropic.com](https://console.anthropic.com/).
2. Erstelle einen API Key.

### 5. Konfiguration

Kopiere `.env.example` zu `.env` und fülle die Werte aus:

```bash
cp .env.example .env
```

```env
TELEGRAM_BOT_TOKEN=dein_telegram_bot_token
LLM_PROVIDER=anthropic
LLM_API_KEY=dein_api_key
LLM_BASE_URL=https://api.kimi.com/coding/
LLM_MODEL=kimi-k2-0711-preview
RENDER_EXTERNAL_URL=
ADMIN_CHAT_ID=
WEBHOOK_SECRET=
MAX_HISTORY=10
```

## Lokaler Test (Polling)

Für lokale Tests kannst du einen einfachen Polling-Modus verwenden. Erstelle dazu eine Datei `run_polling.py`:

```python
import asyncio
from src.bot import create_application
from src import config

async def main():
    app = create_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
```

Starte dann:

```bash
python run_polling.py
```

### 409 Conflict / "terminated by other getUpdates request"

Dieser Fehler bedeutet, dass zwei Bot-Instanzen gleichzeitig laufen. Lösung:

1. Stoppe alle laufenden `run_polling.py`-Prozesse (`Ctrl+C`).
2. Prüfe, ob der Bot noch auf Render oder einem anderen Server läuft.
3. Setze Webhook und Pending Updates zurück:

```bash
python reset_webhook.py
```

4. Starte den Bot neu:

```bash
python run_polling.py
```

## Deployment auf Render

### Option A: Mit `render.yaml` (Blueprint)

1. Lade das Projekt auf GitHub hoch.
2. Gehe zu [render.com](https://render.com/) und erstelle einen neuen **Web Service** aus dem Blueprint.
3. Füge die Umgebungsvariablen in den Render-Einstellungen hinzu:
   - `TELEGRAM_BOT_TOKEN`
   - `LLM_PROVIDER` (`anthropic` für Kimi coding, `openai` für OpenRouter/Kimi OpenAI, `anthropic` für Anthropic)
   - `LLM_API_KEY`
   - `LLM_BASE_URL` (z. B. `https://kimi.com/coding/v1`)
   - `LLM_MODEL` (z. B. `kimi-k2-0711-preview`)
   - `RENDER_EXTERNAL_URL` (wird nach dem ersten Deploy angezeigt, z. B. `https://skypol-bot.onrender.com`)
   - Optional: `ADMIN_CHAT_ID`, `WEBHOOK_SECRET`
4. Nach dem Deploy setze `RENDER_EXTERNAL_URL` auf die Render-URL und deploye neu.

### Option B: Manuelles Deployment

1. Erstelle einen neuen **Web Service** auf Render.
2. Verbinde das GitHub-Repository.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn src.main:app --host 0.0.0.0 --port $PORT`
5. Füge die Umgebungsvariablen hinzu (siehe oben).

## Gruppenchat-Verhalten

Der Bot kann problemlos in Gruppenchats hinzugefügt werden und antwortet nur, wenn:

- Er mit `@botname` erwähnt wird
- Jemand auf eine Nachricht des Bots antwortet
- Der Bot-Name ohne `@` im Text vorkommt
- Einer der folgenden Begriffe im Text vorkommt:
  - **DE:** skypol, hilfe, support, frage, info, leistung, preis, termin
  - **EL:** skypol, βοήθεια, υποστήριξη, ερώτηση, πληροφορίες, υπηρεσία, τιμή, ραντεβού
  - **EN:** skypol, help, support, question, info, service, price, appointment

So vermeidest du Spam im Gruppenchat.

### Wichtig: Datenschutzmodus deaktivieren

Damit der Bot in Gruppen Nachrichten lesen und auf Schlüsselwörter reagieren kann, muss der **Datenschutzmodus (Privacy Mode)** in [@BotFather](https://t.me/botfather) deaktiviert sein:

1. Öffne [@BotFather](https://t.me/botfather).
2. Sende `/mybots` und wähle deinen Bot.
3. Wähle **Bot Settings** → **Group Privacy** → **Turn off**.
4. Starte den Bot neu.

Ohne diesen Schritt sieht der Bot nur direkte Erwähnungen und Antworten auf seine eigenen Nachrichten.

### Bot als Gruppen-Admin

Für volle Funktionalität empfiehlt es sich, den Bot in der Gruppe zum **Administrator** zu ernennen:

1. Gruppeninfo öffnen → **Bearbeiten** → **Administratoren**.
2. **Administrator hinzufügen** → Bot auswählen.
3. Mindestens diese Rechte aktivieren:
   - **Nachrichten löschen**
   - **Mitglieder einschränken**
   - **Nachrichten anpinnen**

Als Admin kann der Bot:

- alle Gruppennachrichten lesen (auch bei aktiviertem Privacy Mode)
- neue Mitglieder begrüßen
- das Hauptmenü mit `/pinmenu` anpinnen

## Befehle

| Befehl | Beschreibung |
|--------|--------------|
| Befehl | Beschreibung |
|--------|--------------|
| `/start` | Willkommensnachricht mit Hauptmenü |
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
| `/help` | Hilfe |
| `/stats` | Bot-Statistiken (Admin) |
| `/reply` | Auf Ticket antworten: `/reply <id> <Text>` (Admin) |
| `/close` | Ticket schließen: `/close <id>` (Admin) |
| `/warn` | Nutzer verwarnen (auf Nachricht antworten, Admin) |
| `/mute` | Nutzer stummschalten (auf Nachricht antworten, Admin) |
| `/kick` | Nutzer kicken (auf Nachricht antworten, Admin) |
| `/ban` | Nutzer bannen (auf Nachricht antworten, Admin) |

## Wissensdatenbank aktualisieren

Ändere einfach die Datei `data/knowledge_base.yaml`. Der Bot lädt sie beim Start neu. Du kannst dort:

- neue Leistungen hinzufügen,
- FAQ-Einträge ergänzen,
- Kontaktdaten ändern,
- Portfolio-Kategorien anpassen,
- Kundenbewertungen pflegen,
- Buchungsinformationen aktualisieren,
- Willkommensnachrichten in weiteren Sprachen hinzufügen.

## Support-Ticket-System

Wenn ein Kunde `/human` oder den Support-Button nutzt, wird ein Ticket erstellt. Alle weiteren Nachrichten des Kunden werden an den Admin weitergeleitet, bis das Ticket geschlossen wird.

**Admin-Workflow:**

1. Admin erhält Benachrichtigung: `🆘 Ticket #1 – User: ...`
2. Admin antwortet auf die weitergeleitete Nachricht oder schreibt: `/reply 1 Hallo, ich helfe dir gerne!`
3. Der Bot leitet die Antwort an den Kunden weiter.
4. Admin schließt das Ticket: `/close 1`

Gibt es nur ein offenes Ticket, reicht auch: `/reply Hallo, ich helfe dir!`

Für die Admin-Befehle muss `ADMIN_CHAT_ID` mit der Chat-ID des Admins gesetzt sein. Mehrere Admins können komma-getrennt hinterlegt werden, z. B. `ADMIN_CHAT_ID=123456789,987654321`.

## Datenschutz / DSGVO

- Der Bot speichert keine personenbezogenen Daten dauerhaft.
- Der Gesprächsspeicher liegt nur im Arbeitsspeicher und ist begrenzt (`MAX_HISTORY`).
- Bei `/reset` wird der Speicher sofort gelöscht.
- Für den produktiven Betrieb solltest du deine Datenschutzerklärung um den Bot ergänzen.

## Support

Bei Fragen melde dich bei:

- 📧 info@skypol.de
- 📞 +49 176 8789 0032
- 📸 [@skypol_arts_media](https://www.instagram.com/skypol_arts_media/)

---

Made with passion for Skypol Arts & Media.
