<!-- /vectoplan-website/services/vectoplan-server/docs/IST-Zustand-vectoplan-app.md -->

# IST-Zustand – `vectoplan-app`

Stand: 2026-06-21
Status: nach erfolgreicher Einbindung von `vectoplan-editor` und OpenLayer in die App-Shell

---

## 1. Zweck dieses Dokuments

Diese Datei beschreibt den aktuellen IST-Zustand der `vectoplan-app` nach dem erfolgreichen Umbau der zentralen Chat- und Workspace-Oberfläche.

Die Datei ist eine Bestandsaufnahme des aktuellen lauffähigen Zustands. Sie beschreibt nicht das vollständige Zielbild, sondern den Stand, der nach der ersten erfolgreichen Integration erreicht wurde.

Aktuell erreicht:

1. Die Portal-/Shell-Rolle der `vectoplan-app` ist bestätigt.
2. Der eigene `vectoplan-editor` ist als 3D-Arbeitsfläche eingebunden.
3. Die OpenLayer-Karte ist als Map-Arbeitsfläche eingebunden.
4. Browser-public URLs und Docker-interne URLs sind getrennt.
5. Die iframe-Blockade durch `X-Frame-Options: SAMEORIGIN` ist für den Editor-Embed-Pfad gelöst.
6. Der alte Speckle-/Altviewer-Pfad wird vom primären UI-Flow nicht mehr genutzt.

Noch nicht vollständig abgeschlossen:

1. Speckle-Altlasten sind im Code teilweise noch vorhanden.
2. Chat-Helper, Auto-Upload, Versionierung und Seed-Templates müssen noch weiter entspeckelt werden.
3. Alte Routen können später entfernt oder hart hinter Feature-Flags gelegt werden.
4. OpenLayer-Header können bei Bedarf noch in `routes/map.py` und `app.py` final vereinheitlicht werden.
5. `vectoplan-app/app.py` sollte später noch global auf die neue CSP-/Frame-Policy ausgerichtet werden.

---

## 2. Kurzbefund

Die `vectoplan-app` ist eine Flask-basierte Portal- und Chat-Anwendung. Sie ist nicht die fachliche 3D-Modellwahrheit und nicht der eigentliche Editor.

Ihre aktuelle Rolle ist:

* Chat-Shell
* Upload- und Datei-Shell
* Workspace-Shell
* iframe-Orchestrator
* Conversation-Kontext
* Template-/Card-Host
* Versionen-/State-Host
* Einstiegspunkt für 3D, Map, 2D, LV und Admin

Der wichtigste Browser-Einstieg ist weiterhin:

```text
http://localhost:5103/ui/chat-3d
```

Der zentrale Unterschied zum alten Zustand:

Der 3D-Modus lädt nicht mehr den alten externen Viewer/Speckle-Pfad, sondern den lokalen Editor-Integrationspfad:

```text
/ui/chat/<chat_id>/editor
```

Diese App-Route leitet browserseitig auf den Editor-Service weiter:

```text
http://localhost:5100/editor?embed=1&chat_id=<chat_id>
```

Der Map-Modus lädt entsprechend:

```text
/ui/chat/<chat_id>/map
```

Diese App-Route leitet browserseitig auf OpenLayer weiter:

```text
http://localhost:5190/map?embed=1&chat_id=<chat_id>
```

---

## 3. Aktuelle Service-Rollen

### 3.1 `vectoplan-app`

Rolle:

* Portal
* Chat
* Shell
* iframe-Orchestrierung
* Conversation-State
* Upload-/Blob-Verwaltung
* lokale Versionierungsanzeige
* zentrale UI

Nicht Rolle:

* kein 3D-Editor
* keine Modellwahrheit
* kein Chunk-Owner
* kein OpenLayer-Ersatz
* kein Speckle-Proxy im primären Flow

---

### 3.2 `vectoplan-editor`

Rolle:

* eigentlicher 3D-Editor
* Browser-Runtime
* Pointer-Lock-/First-Person-Oberfläche
* Chunk-Service-Proxy-Verbraucher
* Library-/Inventory-Verbraucher

Direkter Browser-Test:

```text
http://localhost:5100/editor
```

Embed-Pfad aus der App:

```text
http://localhost:5100/editor?embed=1&chat_id=<chat_id>
```

Wichtige gelöste Änderung:

Bei `?embed=1` wird `X-Frame-Options` entfernt und `Content-Security-Policy: frame-ancestors ...` erlaubt die App-Origin.

---

### 3.3 `vectoplan-openLayer`

Rolle:

* eigenständiger Map-Service
* OpenLayer-Kartenoberfläche
* Ziel des Map-iframes der App

Direkter Browser-Test:

```text
http://localhost:5190/map
```

Embed-Pfad aus der App:

```text
http://localhost:5190/map?embed=1&chat_id=<chat_id>
```

Wichtige gelöste Änderung:

Die App nutzt jetzt die browserseitige Public-URL `http://localhost:5190` und nicht mehr den internen Container-Port `http://localhost:8090`.

---

### 3.4 `vectoplan-chunk`

Rolle:

* Runtime-/Chunk-Daten
* interne Service-Kommunikation
* keine direkte Browser-URL im App-iframe

Browser soll nicht direkt verwenden:

```text
http://vectoplan-chunk:5000
```

---

### 3.5 `vectoplan-library`

Rolle:

* Library-/Inventory-Quelle
* interne Service-Kommunikation
* keine direkte Browser-URL im App-iframe

Browser soll nicht direkt verwenden:

```text
http://vectoplan-library:5000
```

---

## 4. Zentrale Architekturentscheidung

Die wichtigste technische Korrektur war die Trennung zwischen Browser-URLs und Docker-internen URLs.

### 4.1 Browser-/Public-URLs

Diese URLs dürfen im Browser, in iframes, Redirects und Links erscheinen:

```text
VECTOPLAN_APP_PUBLIC_URL=http://localhost:5103
VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100
OPENLAYER_PUBLIC_URL=http://localhost:5190
```

### 4.2 Docker-/Internal-URLs

Diese URLs sind nur für Server-zu-Server-Kommunikation innerhalb Docker gedacht:

```text
VECTOPLAN_EDITOR_INTERNAL_URL=http://vectoplan-editor:5000
OPENLAYER_INTERNAL_URL=http://openlayer:8090
VECTOPLAN_CHUNK_INTERNAL_URL=http://vectoplan-chunk:5000
VECTOPLAN_LIBRARY_INTERNAL_URL=http://vectoplan-library:5000
```

Regel:

```text
Browser/iframe/redirect  → PUBLIC_URL
Backend/server-to-server → INTERNAL_URL
```

Diese Trennung ist jetzt in Compose, Config und den relevanten App-Routen vorbereitet.

---

## 5. Aktueller funktionierender UI-Gesamtfluss

```text
Browser
  ↓
GET http://localhost:5103/ui/chat-3d
  ↓
vectoplan-app/routes/ui/chat.py
  ↓
Conversation suchen oder erzeugen
  ↓
chat_viewer.html rendern
  ↓
static/js/chat/main.js bootet
  ↓
Workspace iframe wird gesteuert
  ↓
3D:
  /ui/chat/<chat_id>/editor
    → http://localhost:5100/editor?embed=1&chat_id=<chat_id>

Map:
  /ui/chat/<chat_id>/map
    → http://localhost:5190/map?embed=1&chat_id=<chat_id>

2D:
  /ui/chat/<chat_id>/cad2d
  oder CAD-Embed aus /ui/chat/<chat_id>/cad-embed.json

LV:
  /ui/chat/<chat_id>/lv

Admin:
  /ui/chat/<chat_id>/admin
```

---

## 6. Aktuell angepasste Dateien

Im aktuellen Umbau wurden folgende Dateien aktualisiert oder neu ausgerichtet.

### 6.1 Compose

```text
/vectoplan-website/services/vectoplan-server/docker-compose.all.yml
```

Relevante Änderungen:

* Public/Internal-URL-Trennung eingeführt.
* Editor-Public-URL ergänzt.
* OpenLayer-Public-URL auf `http://localhost:5190` gesetzt.
* OpenLayer-Internal-URL bleibt `http://openlayer:8090`.
* App kennt Editor, OpenLayer, Chunk und Library jeweils mit passenden Public/Internal-Werten.
* iframe-relevante ENV-Variablen ergänzt.
* Healthchecks robuster gemacht.

---

### 6.2 App-Config

```text
services/vectoplan-app/config.py
```

Relevante Änderungen:

* `VECTOPLAN_EDITOR_PUBLIC_URL`
* `VECTOPLAN_EDITOR_INTERNAL_URL`
* `VECTOPLAN_EDITOR_ROUTE`
* `VECTOPLAN_EDITOR_EMBED_ENABLED`
* `OPENLAYER_PUBLIC_URL`
* `OPENLAYER_INTERNAL_URL`
* `OPENLAYER_ROUTE`
* `OPENLAYER_EMBED_ENABLED`
* `VECTOPLAN_APP_ALLOWED_FRAME_SRC`
* `VECTOPLAN_ALLOWED_FRAME_PARENTS`
* Speckle-Guards:

  * `LEGACY_SPECKLE_ENABLED = False`
  * `AUTO_UPLOAD_ATTACHMENTS = False`

Wichtige Korrektur:

```text
OPENLAYER_PUBLIC_URL=http://localhost:5190
```

statt alt/falsch:

```text
http://localhost:8090
```

---

### 6.3 App Map-Route

```text
services/vectoplan-app/routes/ui/map.py
```

Relevante Änderungen:

* `/ui/chat/<chat_id>/map` nutzt jetzt konsequent `OPENLAYER_PUBLIC_URL`.
* Bekannte Legacy-Ziele wie `localhost:8090` werden defensiv repariert.
* Redirect-Ziel wird browserfähig gebaut.
* `embed=1` und `chat_id` werden an OpenLayer weitergegeben.
* WFS-Proxy bleibt erhalten.
* WFS-Credentials bleiben serverseitig.

Aktueller Zweck:

```text
/ui/chat/<chat_id>/map
  → http://localhost:5190/map?embed=1&chat_id=<chat_id>&...
```

---

### 6.4 App Chat-/Shell-Route

```text
services/vectoplan-app/routes/ui/chat.py
```

Relevante Änderungen:

* `/ui/chat-3d` rendert die Shell mit lokalen App-Routen für 3D und Map.
* `editor_url` zeigt auf:

  ```text
  /ui/chat/<chat_id>/editor
  ```
* `map_url` zeigt auf:

  ```text
  /ui/chat/<chat_id>/map
  ```
* `viewer.json` liefert neutralen Editor-/Workspace-Kontext.
* Kein primärer Speckle-/Altviewer-Aufruf im 3D-Flow.
* Workspace-CSP für Editor/OpenLayer vorbereitet.

---

### 6.5 Haupttemplate

```text
services/vectoplan-app/templates/chat_viewer.html
```

Relevante Änderungen:

* `APP_CONFIG.editorPagePath` zeigt auf lokale App-Route.
* `APP_CONFIG.initialEditorUrl` zeigt auf lokale App-Route.
* `APP_CONFIG.mapPagePath` zeigt auf lokale App-Route.
* iframe erlaubt jetzt:

  ```text
  fullscreen; pointer-lock; clipboard-read; clipboard-write
  ```
* `viewer_url` bleibt nur noch Kompatibilitätsfeld.
* `legacySpeckleEnabled=false`
* `oldViewerEnabled=false`
* `workspaceTargetsAreAppRoutes=true`

---

### 6.6 App Frontend-Orchestrator

```text
services/vectoplan-app/static/js/chat/main.js
```

Relevante Änderungen:

* 3D-Modus lädt ausschließlich lokale App-Route:

  ```text
  /ui/chat/<chat_id>/editor
  ```
* Map-Modus lädt ausschließlich lokale App-Route:

  ```text
  /ui/chat/<chat_id>/map
  ```
* Docker-interne oder alte Browserziele werden blockiert/repariert.
* `localhost:8090` wird nicht mehr als Browserziel genutzt.
* iframe bekommt zuverlässig Pointer-Lock-Allow.
* Workspace-Fallback und Debug-API ergänzt.
* Alte `viewer_url`-Pfade werden nicht mehr als primärer 3D-Modus genutzt.

Debug-Hook im Browser:

```js
window.__VECTOPLAN_WORKSPACE_DEBUG__
```

---

### 6.7 Viewer-Selection

```text
services/vectoplan-app/routes/viewer_selection.py
```

Relevante Änderungen:

* Endpoint bleibt kompatibel:

  ```text
  /v1/chats/<chat_id>/viewer/selection
  ```
* Speichert aber nur noch neutralen Workspace-State.
* Erlaubte Modi:

  ```text
  editor / 3d
  map
  2d
  lv
  admin
  ```
* Alte Projekt-/Modell-/Version-/Speckle-Felder werden rekursiv entfernt.
* Endpoint-Name ist kompatibel zu:

  ```text
  viewer_selection.viewer_selection
  ```

---

### 6.8 Editor Config

```text
services/vectoplan-editor/config.py
```

Relevante Änderungen:

* Public-/Embed-Config ergänzt.
* `VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100`
* `VECTOPLAN_APP_PUBLIC_URL=http://localhost:5103`
* `VECTOPLAN_EDITOR_ROUTE=/editor`
* `VECTOPLAN_EDITOR_EMBED_ENABLED=true`
* `VECTOPLAN_EDITOR_FRAME_ANCESTORS`
* `build_embed_security_config()`
* `build_security_header_config()`

---

### 6.9 Editor Route

```text
services/vectoplan-editor/routes/editor.py
```

Relevante Änderungen:

* `/editor?embed=1` wird iframe-fähig.
* Im Embed-Modus wird `X-Frame-Options` entfernt.
* CSP `frame-ancestors` erlaubt die App-Origin.
* Standalone `/editor` bleibt mit `X-Frame-Options: SAMEORIGIN` geschützt.
* Embed-Kontext wird an Template/Bootstrap durchgereicht.

Wichtige Header im Embed-Modus:

```text
X-VECTOPLAN-Editor-Embed: true
Content-Security-Policy: frame-ancestors 'self' http://localhost:5103 http://127.0.0.1:5103
```

---

### 6.10 Editor App-Factory

```text
services/vectoplan-editor/app.py
```

Relevante Änderungen:

* Globale Header-Schicht blockiert `/editor?embed=1` nicht mehr.
* Standalone `/editor` bleibt geschützt.
* Health-Routen defensiv ergänzt.
* Frame-Ancestors werden in App-Metadaten hinterlegt.

---

### 6.11 OpenLayer Settings

```text
services/vectoplan-openLayer/settings.py
```

Relevante Änderungen:

* `OPENLAYER_PUBLIC_URL=http://localhost:5190`
* `OPENLAYER_ROUTE=/map`
* `OPENLAYER_EMBED_ENABLED=true`
* `OPENLAYER_FRAME_ANCESTORS`
* `OPENLAYER_FRAME_ANCESTORS_CSP`
* `VECTOPLAN_APP_PUBLIC_URL=http://localhost:5103`
* Flask-Config-Werte für spätere Header-Schicht vorbereitet.

---

## 7. Aktueller Status der wichtigsten Routen

### 7.1 App

```text
http://localhost:5103/ui/chat-3d
```

Status:

```text
funktioniert
```

Rolle:

* zentrale Chat-plus-Workspace-Shell

---

### 7.2 Editor direkt

```text
http://localhost:5100/editor
```

Status:

```text
funktioniert
```

Rolle:

* Standalone-Editor

---

### 7.3 Editor eingebettet

```text
http://localhost:5103/ui/chat/<chat_id>/editor
```

leitet auf:

```text
http://localhost:5100/editor?embed=1&chat_id=<chat_id>
```

Status:

```text
funktioniert
```

---

### 7.4 Map direkt

```text
http://localhost:5190/map
```

Status:

```text
funktioniert
```

---

### 7.5 Map eingebettet

```text
http://localhost:5103/ui/chat/<chat_id>/map
```

leitet auf:

```text
http://localhost:5190/map?embed=1&chat_id=<chat_id>
```

Status:

```text
funktioniert
```

---

## 8. Aktuelle Ordnerstruktur im relevanten Bereich

Die relevante Struktur ist aktuell:

```text
services/vectoplan-app/
  app.py
  config.py
  models.py
  versioning.py
  seed_templates.py

  routes/
    ui/
      chat.py
      map.py
      editor.py
      crawlab.py
      superset.py
      viewer2d/
        __init__.py
        helpers.py
        pages.py
        cad_embed.py

    chat/
      __init__.py
      crud.py
      helpers.py
      sync.py
      stream.py

    files.py
    blobs_base64.py
    versions_api.py
    templates.py
    state.py
    viewer_selection.py

    embed.py
    speckle_upload.py
    vectoplan_ingest.py
    vectoplan_align.py

  templates/
    chat.html
    chat_viewer.html
    viewer/
      admin.html
      cad2d.html
      lv.html
      map.html

  static/
    css/
      chat.css
      cards.css
    js/
      chat/
        main.js
```

Hinweis:

Die alten Dateien `embed.py`, `speckle_upload.py`, `vectoplan_ingest.py` und `vectoplan_align.py` können noch im Codebestand vorhanden sein. Sie sind aber nicht mehr der primäre 3D-Flow der Shell.

---

## 9. Was jetzt funktioniert

Aktuell funktioniert:

```text
App Shell:
http://localhost:5103/ui/chat-3d

3D direkt:
http://localhost:5100/editor

3D in App:
http://localhost:5103/ui/chat/<chat_id>/editor
→ http://localhost:5100/editor?embed=1&chat_id=<chat_id>

Map direkt:
http://localhost:5190/map

Map in App:
http://localhost:5103/ui/chat/<chat_id>/map
→ http://localhost:5190/map?embed=1&chat_id=<chat_id>
```

Die zentrale App kann damit wieder als Portal-Shell verwendet werden.

---

## 10. Was bewusst noch nicht final ist

### 10.1 Speckle-Altlasten

Speckle ist im primären UI-Pfad nicht mehr aktiv, aber im Codebestand noch nicht vollständig entfernt.

Noch zu prüfen/bereinigen:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
vectoplan.py
src.vectoplan/
routes/chat/helpers.py
routes/chat/sync.py
routes/chat/stream.py
versioning.py
seed_templates.py
```

---

### 10.2 Auto-Upload aus ChatAI-Intent

Der neue UI-Upload in `routes/ui/chat.py` ist neutralisiert. Der ältere ChatAI-Intent-Pfad in `routes/chat/` kann aber noch alte Upload-/Publish-Logik enthalten.

Noch zu bereinigen:

```text
auto_upload_supported_attachments(...)
maybe_post_viewer_card(...)
record_version_safe(...)
UPLOAD_VERSION
PUBLISH_3D
```

Ziel:

Keine automatische Veröffentlichung in Speckle oder alten Viewer-Pfad.

---

### 10.3 Versionierung

Die lokale Versionierung funktioniert als Übergang, ist aber noch transcript-basiert.

Aktuell problematisch:

* Versionen liegen als Service-Nachrichten im `Conversation.transcript`.
* Alte Speckle-Felder können im Modell noch auftauchen.
* Langfristig sollte ein echtes Versionierungsmodell genutzt werden.

Zielstruktur später:

```json
{
  "version_id": "...",
  "kind": "BGA_MESH",
  "label": "...",
  "status": "stored",
  "artifact_ref": {
    "type": "blob",
    "blob_id": "..."
  },
  "source_service": "vectoplan-app",
  "legacy_3d_backend": false
}
```

---

### 10.4 Templates

Noch zu bereinigen:

```text
speckle_viewer
SpeckleViewerCard
```

Diese Templates sollten entfernt oder durch neutrale Editor-/Workspace-Karten ersetzt werden.

---

### 10.5 `vectoplan-app/app.py`

Die App-Factory wurde in diesem Schritt noch nicht final bereinigt.

Noch sinnvoll:

* globale CSP/Frame-Policy auf Editor/OpenLayer abstimmen
* alte Blueprints hinter `LEGACY_SPECKLE_ENABLED` legen
* alte Blueprints später entfernen
* `AUTO_UPLOAD_ATTACHMENTS` nicht per `setdefault` absichern, sondern hart aus Config steuern

---

### 10.6 OpenLayer Header

Da die Map jetzt funktioniert, ist die akute Blockade gelöst.

Trotzdem sinnvoll für saubere Architektur:

```text
services/vectoplan-openLayer/routes/map.py
services/vectoplan-openLayer/app.py
```

später noch so ergänzen, dass `/map?embed=1` explizit:

```text
X-Frame-Options entfernt
Content-Security-Policy: frame-ancestors 'self' http://localhost:5103 http://127.0.0.1:5103
```

setzt.

---

## 11. Was behalten werden sollte

Diese Teile sind weiterhin sinnvoll:

```text
services/vectoplan-app/app.py
services/vectoplan-app/config.py
services/vectoplan-app/models.py
services/vectoplan-app/auth.py
services/vectoplan-app/versioning.py       # übergangsweise
services/vectoplan-app/seed_templates.py   # bereinigen, nicht entfernen

routes/chat/
routes/ui/chat.py
routes/ui/editor.py
routes/ui/map.py
routes/ui/viewer2d/
routes/ui/superset.py
routes/ui/crawlab.py
routes/files.py
routes/blobs_base64.py
routes/versions_api.py
routes/templates.py
routes/state.py
routes/viewer_selection.py

templates/chat.html
templates/chat_viewer.html
templates/viewer/admin.html
templates/viewer/cad2d.html
templates/viewer/lv.html
```

---

## 12. Was später entfernt oder deaktiviert werden sollte

Diese Teile sind nicht mehr Teil des Zielpfads:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
```

Zusätzlich entfernen oder neutralisieren:

```text
speckle_project_id
speckle_model_id
speckle_version_id
speckle_viewer
SpeckleViewerCard
SPECKLE_UPLOAD_TIMEOUT
VECTOPLAN_HOST als externer Speckle-/Viewer-Host
VECTOPLAN_TOKEN für externen Viewer
VECTOPLAN_EMBED_TOKEN
```

Nicht blind entfernen:

```text
vectoplan.py
```

Diese Datei kann entweder gelöscht oder als neue neutrale Editor-/Workspace-Fassade neu definiert werden. Vorher muss geprüft werden, ob noch alte Imports darauf zeigen.

---

## 13. Aktueller Zielzustand der App-Shell

Die `vectoplan-app` ist jetzt näher am richtigen Zielbild:

```text
vectoplan-app
  = Portal
  = Chat
  = Shell
  = Workspace-Orchestrator
  = State-/Conversation-Host

vectoplan-editor
  = 3D-Editor
  = Runtime
  = Chunk-/Library-Verbraucher

vectoplan-openLayer
  = Map-Service

vectoplan-chunk
  = Chunk-State

vectoplan-library
  = Library-/Inventory-Wahrheit
```

Die App ist nicht mehr als 3D-Viewer-Ersatz zu behandeln. Sie zeigt den Editor an.

---

## 14. Neuer primärer 3D-Flow

Der aktuelle primäre 3D-Flow ist:

```text
Browser
  ↓
http://localhost:5103/ui/chat-3d
  ↓
chat_viewer.html
  ↓
main.js
  ↓
mode 3D
  ↓
iframe src = /ui/chat/<chat_id>/editor
  ↓
redirect
  ↓
http://localhost:5100/editor?embed=1&chat_id=<chat_id>
  ↓
vectoplan-editor rendert Editor
```

Wichtig:

`viewer_url` existiert nur noch als Kompatibilitätsfeld. Fachlich ist der neue 3D-Modus der Editor-Modus.

---

## 15. Neuer primärer Map-Flow

Der aktuelle primäre Map-Flow ist:

```text
Browser
  ↓
http://localhost:5103/ui/chat-3d
  ↓
chat_viewer.html
  ↓
main.js
  ↓
mode Map
  ↓
iframe src = /ui/chat/<chat_id>/map
  ↓
redirect
  ↓
http://localhost:5190/map?embed=1&chat_id=<chat_id>
  ↓
OpenLayer rendert Karte
```

Wichtig:

`http://localhost:8090` ist kein Browserziel mehr.

---

## 16. Bekannte technische Risiken im aktuellen Stand

### 16.1 Alte Routen existieren noch

Auch wenn der primäre Flow funktioniert, können alte Routen noch registriert sein.

Risiko:

* versehentlicher Zugriff
* alte Upload-/Sync-Logik
* unklare API-Oberfläche

Maßnahme:

* per Feature-Flag deaktivieren
* später entfernen

---

### 16.2 ChatAI-Actions können noch alte Pfade triggern

Die UI-Shell ist bereinigt. Die ChatAI-Action-Logik muss noch geprüft werden.

Risiko:

* `UPLOAD_VERSION`
* `PUBLISH_3D`
* alte Auto-Upload-Funktionen

Maßnahme:

* Auto-Upload standardmäßig aus
* alte Actions neutralisieren
* neue Actions später auf eigene Import-/Editor-Jobs mappen

---

### 16.3 Versionierung ist nur Übergang

Transcript-Versionierung ist für Alpha okay, aber nicht dauerhaft.

Maßnahme:

* Speckle-Felder entfernen
* neutrales `artifact_ref` einführen
* langfristig eigene Tabelle nutzen

---

### 16.4 Globale App-CSP muss noch final geprüft werden

Die Shell funktioniert aktuell. Für robusten Betrieb sollte `vectoplan-app/app.py` trotzdem noch global kontrollieren:

```text
frame-src / child-src:
  'self'
  http://localhost:5100
  http://127.0.0.1:5100
  http://localhost:5190
  http://127.0.0.1:5190
```

---

### 16.5 Produktivbetrieb braucht andere Origins

Aktuelle Origins sind lokal:

```text
localhost:5103
localhost:5100
localhost:5190
127.0.0.1:5103
127.0.0.1:5100
127.0.0.1:5190
```

Für Staging/Production müssen diese Werte über ENV gesetzt werden.

---

## 17. Empfohlene nächste Bereinigungsschritte

### Schritt 1: `vectoplan-app/app.py`

Ziel:

* alte Speckle-/Altviewer-Blueprints hinter Flag legen
* globale CSP sauber setzen
* `LEGACY_SPECKLE_ENABLED=false` hart respektieren
* App-Metadaten für Editor/OpenLayer ergänzen

---

### Schritt 2: `routes/ui/editor.py`

Falls schon vorhanden: prüfen und finalisieren.

Soll:

```text
GET /ui/chat/<chat_id>/editor
```

macht Redirect auf:

```text
VECTOPLAN_EDITOR_PUBLIC_URL + VECTOPLAN_EDITOR_ROUTE + ?embed=1&chat_id=...
```

Nie:

```text
http://vectoplan-editor:5000
```

an den Browser senden.

---

### Schritt 3: Chat-Helper bereinigen

Dateien:

```text
routes/chat/helpers.py
routes/chat/sync.py
routes/chat/stream.py
```

Ziel:

* alte Upload-/Publish-Actions neutralisieren
* Speckle-Karten nicht mehr posten
* Auto-Upload standardmäßig aus
* später neue Editor-/Import-Actions definieren

---

### Schritt 4: Versionierung neutralisieren

Datei:

```text
versioning.py
```

Ziel:

* Speckle-Felder entfernen
* neutrale Artefakt-Referenzen verwenden
* Kompatibilität zu vorhandenen Einträgen defensiv erhalten

---

### Schritt 5: Template-Seeds bereinigen

Datei:

```text
seed_templates.py
```

Ziel:

* `speckle_viewer` entfernen
* `SpeckleViewerCard` entfernen
* ggf. neutrale Workspace-/Editor-Karte ergänzen

---

### Schritt 6: Alte Routen entfernen

Sobald keine Imports oder UI-Abhängigkeiten mehr bestehen:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
```

entfernen.

---

## 18. Aktuelle Soll-Struktur nach nächster Bereinigung

```text
services/vectoplan-app/
  app.py
  wsgi.py
  config.py
  auth.py
  models.py
  versioning.py
  seed_templates.py

  routes/
    chat/
      __init__.py
      crud.py
      helpers.py
      sync.py
      stream.py

    ui/
      chat.py
      editor.py
      map.py
      crawlab.py
      superset.py
      viewer2d/
        __init__.py
        helpers.py
        pages.py
        cad_embed.py

    files.py
    blobs_base64.py
    versions_api.py
    templates.py
    state.py
    viewer_selection.py

  templates/
    chat.html
    chat_viewer.html
    viewer/
      admin.html
      cad2d.html
      lv.html

  static/
    css/
    js/
```

Nicht mehr enthalten:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
```

Optional neu oder ersetzt:

```text
editor_gateway.py
```

oder:

```text
routes/ui/editor.py
```

als einzige Editor-Integrationsfassade.

---

## 19. Gesamtfazit

Die `vectoplan-app` ist jetzt wieder auf dem richtigen Architekturpfad.

Vorher war sie eine Portal-Shell mit veraltetem 3D-Pfad. Der zentrale Fehler lag nicht in der Shell-Idee, sondern in der alten Speckle-/Altviewer-Kopplung und in der Vermischung von Browser- und Docker-internen URLs.

Der aktuelle funktionierende Stand ist:

1. `vectoplan-app` bleibt Portal und Shell.
2. `vectoplan-editor` ist der echte 3D-Workspace.
3. OpenLayer ist der echte Map-Workspace.
4. Die App steuert beide über lokale App-Routen.
5. Browserziele verwenden Public-URLs.
6. Docker-interne URLs bleiben intern.
7. Der primäre UI-Flow nutzt Speckle nicht mehr.
8. Die alte Speckle-Logik ist noch als technische Altlast im Codebestand vorhanden und sollte als nächstes bereinigt werden.

Die wichtigste funktionierende Route ist:

```text
http://localhost:5103/ui/chat-3d
```

Dort sind jetzt 3D und Map korrekt eingebettet.

Nächster sinnvoller technischer Schritt:

```text
services/vectoplan-app/app.py
```

bereinigen, damit die alte Speckle-/Altviewer-Registrierung sauber deaktiviert wird und die globale CSP der App endgültig zur neuen Workspace-Architektur passt.
