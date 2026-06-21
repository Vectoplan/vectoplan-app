<!-- services/vectoplan-app/IST-Zustand.md -->
# IST-Zustand – `vectoplan-app`

Stand: 2026-06-21  
Status: aktualisiert nach Phase 1 der Speckle-/Altviewer-Bereinigung  
Basis: bisherige `IST-Zustand.md`, aktueller Docker-Stack und die im Umbau angepassten Dateien

---

## 1. Zweck dieses Dokuments

Diese Datei beschreibt den aktuellen Zustand der `vectoplan-app` nach der ersten Bereinigungsphase.

Der Schwerpunkt dieser Aktualisierung liegt auf:

1. Entfernung des aktiven Speckle-/Altviewer-Pfads aus der App-Laufzeit.
2. Einbindung des lokalen `vectoplan-editor` als reines iframe-Ziel.
3. Stabilisierung der Portal-/Shell-Struktur.
4. Neutralisierung von Chat-, Upload-, Versionierungs-, Template- und Viewer-State-Pfaden.
5. Bewusster Verzicht auf neue Projekt-/World-/Datenbankstruktur in dieser Phase.

Diese Datei ist weiterhin eine Bestandsaufnahme. Sie beschreibt den Stand nach der aktuellen technischen Bereinigung, nicht das langfristige Zielmodell.

---

## 2. Kurzbefund

Die `vectoplan-app` ist weiterhin eine Flask-basierte Portal-, Chat-, Datei-, State- und UI-Shell-Anwendung.

Sie bündelt aktuell:

- Chat-Oberfläche,
- ChatAI-Anbindung,
- Datei-/Blob-Verwaltung,
- Base64-Blob-Uploads,
- lokale transcript-basierte Versionierung,
- Template-/Card-System,
- Conversation-State,
- iframe-Shell für Arbeitsbereiche,
- Editor-Iframe-Integration,
- 2D-/DXF-/CAD-Viewer,
- OpenLayer-/Map-Integration,
- Superset-Integration,
- Crawlab-Integration,
- Admin- und LV-Flächen.

Die App ist damit keine fachliche Modellwahrheit und kein 3D-Editor. Sie ist die browserbasierte Arbeits- und Integrationshülle.

Der wichtigste Browser-Einstieg bleibt:

```text
http://localhost:5103/ui/chat-3d
```

Der entscheidende Unterschied zum alten Zustand:

```text
/ui/chat-3d
  → chat_viewer.html
  → 3D-Modus lädt /ui/chat/<chat_id>/editor
  → /ui/chat/<chat_id>/editor redirectet auf vectoplan-editor
```

Der alte Speckle-/Altviewer-Pfad ist nicht mehr der aktive 3D-Pfad.

---

## 3. Aktuelle Rolle von `vectoplan-app`

`vectoplan-app` ist aktuell:

- Portal- und UI-Shell,
- Chat-Container,
- Conversation-Verwalter,
- Blob-/Dateispeicher,
- lokaler Versionierungs- und Transcript-Speicher,
- Template-/Message-API,
- State-API,
- iframe-Orchestrator für Editor, 2D, Map, LV und Admin.

`vectoplan-app` ist aktuell ausdrücklich nicht:

- der 3D-Editor,
- die World-/Chunk-Datenbank,
- die Objektbibliothek,
- ein Speckle-Proxy,
- ein externer `vectoplan.com`-Viewer-Proxy,
- ein Import-/Publish-Service für 3D-Dateien,
- Owner von Projekt-/World-Strukturen.

Der Editor wird bewusst nur eingebettet. Die App erzeugt in dieser Phase keine Projekt-/World-Struktur und schreibt keine Editor-/Chunk-Daten.

---

## 4. Aktuelle grobe Ordnerstruktur

Der relevante Pfad im Repository ist:

```text
services/vectoplan-app/
```

Im Container wird dieser Ordner nach `/app` gemountet.

Aktuelle Zielstruktur nach der Bereinigung:

```text
services/vectoplan-app/
  app.py
  wsgi.py
  config.py
  auth.py
  models.py
  versioning.py
  vectoplan.py
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
      crawlab.py
      map.py
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
      map.html

  static/
    css/
    js/
      chat/
        main.js
    local/
    npm/
```

Nicht mehr aktiv in der Flask-Factory registriert:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
```

Falls diese Dateien noch physisch im Repository liegen, sind sie Altlasten und sollten später gelöscht werden. Zur Laufzeit sind sie in der neuen `app.py` nicht mehr Teil der aktiven Blueprint-Registrierung.

---

## 5. Einstiegspunkte und Hauptseiten

### 5.1 `/ui`

Leitet auf die normale Chat-Seite weiter.

Status: behalten.

---

### 5.2 `/ui/chat`

Rendert `templates/chat.html`.

Diese Seite bleibt eine einfache Chat-only-Oberfläche.

Status: behalten.

---

### 5.3 `/ui/chat-3d`

Rendert `templates/chat_viewer.html`.

Aktueller Ablauf:

1. `chat_id` aus Query lesen.
2. Falls keine Conversation existiert: neue `Conversation` anlegen.
3. Startkarte `project_welcome` posten.
4. Redirect auf dieselbe Route mit `chat_id`.
5. Editor-URL über `/ui/chat/<chat_id>/editor` setzen.
6. `chat_viewer.html` rendern.
7. Frontend lädt den 3D-Modus als Editor-Iframe.

Nicht mehr im Ablauf:

```text
ensure_and_refresh(conv)
viewer_url(conv) gegen alten Viewer
ensure_placeholder_if_empty(conv)
Speckle-Placeholder
externer vectoplan.com Viewer
```

Status: zentraler Einstieg, jetzt auf lokalen Editor-Iframe umgestellt.

---

## 6. Editor-Integration

### Datei: `routes/ui/editor.py`

Neu hinzugefügt.

Aufgabe:

```text
GET /ui/chat/<chat_id>/editor
GET /ui/editor
```

Die Route macht nur iframe-Integration:

- liest `VECTOPLAN_EDITOR_PUBLIC_URL`,
- liest `VECTOPLAN_EDITOR_ROUTE`,
- baut eine Editor-URL,
- hängt `chat_id` und `embed=1` an,
- redirectet per 302 auf den lokalen `vectoplan-editor`.

Beispiel:

```text
/ui/chat/<chat_id>/editor
  → http://localhost:5100/editor?chat_id=<chat_id>&embed=1
```

Bewusste Nicht-Ziele dieser Datei:

- keine Datenbankabfrage,
- keine Conversation-Validierung,
- keine Projektstruktur,
- keine World-ID,
- keine Chunk-Integration,
- kein HTML-Fallback,
- kein Speckle,
- kein Upload,
- kein Import.

Status: aktiv eingebunden.

---

## 7. Zentrales Template: `chat_viewer.html`

`chat_viewer.html` ist weiterhin die Hauptoberfläche der App.

Aktuelle Funktion:

- linkes Chat-Panel,
- Composer,
- Attachment-Input,
- rechter Arbeitsbereich,
- Toolbar für Map, 3D, 2D, LV und Admin,
- Versionen-Dropdown,
- iframe `#viewer-frame`,
- `window.APP_CONFIG` mit allen relevanten Pfaden.

Wichtige neue/aktualisierte Pfade:

```text
editorPagePath: /ui/chat/<chat_id>/editor
initialEditorUrl: /ui/chat/<chat_id>/editor
viewerJsonPath: /ui/chat/<chat_id>/viewer.json     # Kompatibilität
versionsPath: /ui/chat/<chat_id>/versions.json
stateGetPath: /v1/chats/<chat_id>/viewer/selection
statePutPath: /v1/chats/<chat_id>/viewer/selection
plan2dJsonPath: /ui/chat/<chat_id>/plan2d.json
cad2dPagePath: /ui/chat/<chat_id>/cad2d
cadEmbedJsonPath: /ui/chat/<chat_id>/cad-embed.json
mapPagePath: /ui/chat/<chat_id>/map
adminPagePath: /ui/chat/<chat_id>/admin
lvPagePath: /ui/chat/<chat_id>/lv
uploadPath: /ui/chat/<chat_id>/upload
```

Der 3D-Modus ist jetzt standardmäßig Editor-Modus:

```text
defaultMode: 3d
workspaceMode: editor
```

Status: behalten, 3D-Modus auf Editor-Iframe umgestellt.

---

## 8. Frontend-Orchestrator

### Datei: `static/js/chat/main.js`

Die Datei wurde bereinigt.

Aktuelle Aufgaben:

- Layout initialisieren,
- Theme umschalten,
- Chat/Transcript/Composer verdrahten,
- Workspace-Modi steuern,
- iframe zwischen Editor, Map, 2D, LV und Admin umschalten,
- Versionen-Dropdown neutral anzeigen,
- 2D-Events weiterverarbeiten,
- Editor-postMessage-Events defensiv merken.

Nicht mehr enthalten:

- alter Speckle-URL-Bau,
- `project_id/model_id/version_id`-Versionenumschaltung,
- `vectoplan/repair`,
- `vectoplan/ensure`,
- `vectoplan/align`,
- Polling für alten Viewer,
- Proxy auf `vectoplan.com`.

Status: zentrale Shell-Logik, Speckle-/Altviewer-frei.

---

## 9. App-Factory und Blueprint-Registrierung

### Datei: `app.py`

Aktuelle Eigenschaften:

- enthält wieder stabil `create_app()` als Top-Level-Funktion,
- lädt Config,
- setzt sichere Defaults,
- initialisiert Logging und DB,
- registriert Blueprints defensiv,
- setzt Health-/Ready-Routen,
- setzt globale Security-Header,
- blockiert den App-Start nicht, wenn optionale Integrationen temporär nicht importierbar sind.

Aktiv registrierte Core-Blueprints:

- `ui_chat_bp`,
- `ui_editor_bp`,
- `chat_api_bp`,
- `files_bp`,
- `blobs_base64_bp`,
- `versions_api_bp`,
- `viewer_selection_bp`,
- `ui_2dviewer_bp`,
- `ui_map_bp`.

Optional registriert:

- `templates_api_bp`,
- `state_api_bp`,
- `ui_crawlab_bp`,
- `ui_superset_bp`.

Nicht mehr registriert:

- `embed_bp`,
- `speckle_upload_bp`,
- `vectoplan_ingest_bp`,
- `vectoplan_align_bp`.

Wichtige harte Defaults:

```text
LEGACY_SPECKLE_ENABLED = False
AUTO_UPLOAD_ATTACHMENTS = False
```

Status: bereinigt und startfähig.

---

## 10. Config

### Datei: `config.py`

Die Config ist jetzt speckle-frei ausgerichtet.

Wichtige Bereiche:

- Flask / SQLAlchemy,
- interne Services,
- ChatAI,
- Editor-Iframe,
- Chunk-/Library-Referenzen für spätere Phasen,
- CAD-Viewer,
- Datei- und Blob-Limits,
- Template-/State-Konfiguration,
- UI-Schalter,
- OpenLayer,
- Crawlab,
- Superset.

Editor-relevante Werte:

```text
VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100
VECTOPLAN_EDITOR_INTERNAL_URL=http://vectoplan-editor:5000
VECTOPLAN_EDITOR_ROUTE=/editor
```

Wichtig:

```text
AUTO_UPLOAD_ATTACHMENTS=False
LEGACY_SPECKLE_ENABLED=False
```

Nicht mehr als aktive Config-Basis verwendet:

```text
VECTOPLAN_HOST
VECTOPLAN_TOKEN
VECTOPLAN_EMBED_TOKEN
SPECKLE_UPLOAD_TIMEOUT
```

Status: Speckle-/Altviewer-Config entfernt bzw. nicht mehr aktiv.

---

## 11. Datenmodelle

### Datei: `models.py`

Die Datei wurde konservativ stabilisiert. Es wurden keine neuen fachlichen Projekt-/World-Tabellen eingeführt.

Weiterhin vorhanden:

- `Client`,
- `IdempotencyKey`,
- `Job`,
- `Blob`,
- `Conversation`,
- `MessageTemplate`,
- `ConversationState`,
- `Project`,
- `ProjectVersion`.

Wichtig:

- `Conversation.vectoplan_project_id` und `Conversation.vectoplan_model_id` bleiben aus DB-Kompatibilitätsgründen vorerst bestehen.
- Neue Routen sollen diese Felder nicht mehr schreiben oder für den Editor-Pfad benötigen.
- `ConversationState.merge_patch()` wurde robuster gedacht und soll neutralen Workspace-State speichern.

Status: behalten, keine große Datenmodell-Revolution in Phase 1.

---

## 12. Versionierung

### Datei: `versioning.py`

Die Versionierung bleibt vorerst transcript-basiert.

Eine Version wird weiterhin als Service-Nachricht gespeichert:

```text
meta.type = version
meta.version = {...}
```

Aktuelle neutrale Felder:

```text
version_id
artifact_id
artifact_ref
kind
label
version_idx
created_at
source_message_id
source_service
source_kind
project_id
project_version_id
world_id
snapshot_id
runtime_ref
editor_url
input_blob_id
blob_id
status
meta
```

Nicht mehr neue Felder:

```text
speckle_project_id
speckle_model_id
speckle_version_id
```

Besonderheit:

- Alte Legacy-Keyword-Argumente werden defensiv ignoriert, damit noch nicht vollständig bereinigte Aufrufer nicht sofort brechen.
- Ausgelistete Versionen werden neutralisiert.
- Legacy-Metadaten werden nicht aktiv in neue Versionen übernommen.

Status: als Übergang behalten, Speckle-Felder neutralisiert.

---

## 13. Vectoplan-Kompatibilitätslayer

### Datei: `vectoplan.py`

Diese Datei importiert nicht mehr aus `src.vectoplan`.

Sie ist jetzt eine harmlose Kompatibilitäts-Fassade.

Sie bietet weiterhin Funktionsnamen für alte Imports:

```text
ensure_and_refresh
ensure_model
ensure_placeholder_if_empty
ensure_bootstrap_viewer
upload_file_to_project
viewer_url
editor_url
_cfg
```

Aktuelles Verhalten:

- `viewer_url(...)` liefert eine lokale Editor-Route.
- `ensure_and_refresh(...)` ist No-op.
- `ensure_placeholder_if_empty(...)` ist No-op.
- `upload_file_to_project(...)` lädt nichts hoch und gibt `stored_only`/`legacy_3d_upload_disabled` zurück.

Zweck:

- alte Imports brechen nicht,
- aber kein alter Backend-Pfad wird ausgeführt.

Status: behalten als temporäre No-op-Kompatibilität. Später vollständig entfernen, wenn keine Imports mehr existieren.

---

## 14. Template-Seeds und Template-API

### Datei: `seed_templates.py`

Die Default-Seeds enthalten nicht mehr:

```text
speckle_viewer
SpeckleViewerCard
```

Aktive Default-Templates:

- `project_welcome`,
- `project_info_form`,
- `project_info_summary`,
- `missing_slots`,
- `info_card`,
- `download_card`,
- `error_card`.

Der Editor wird nicht als Chat-Karte gerendert. Er ist fester iframe-Arbeitsbereich.

---

### Datei: `routes/templates.py`

Die Template-API filtert Legacy-Templates defensiv.

Aktuelle Eigenschaften:

- listet keine alten Speckle-Viewer-Templates,
- blockiert Upserts von `speckle_viewer`,
- blockiert Renderer `SpeckleViewerCard`,
- filtert Legacy-Payload-Felder,
- deaktiviert alte DB-Template-Zeilen best-effort beim Seed-Reload.

Status: Speckle-Templates entfernt bzw. blockiert.

---

## 15. Chat-API

### 15.1 `routes/chat/crud.py`

Routen:

```text
POST /v1/chats
GET  /v1/chats/<chat_id>
```

Aktuelles Verhalten:

- Conversation anlegen/laden,
- Startkarte `project_welcome` posten,
- Transcript ausgeben,
- `viewer_url` aus Kompatibilitätsgründen zurückgeben,
- `viewer_url` zeigt aber auf den lokalen Editor-Iframe-Pfad.

Nicht mehr:

- kein `ensure_and_refresh`,
- kein alter Viewer,
- kein Speckle.

Status: bereinigt.

---

### 15.2 `routes/chat/helpers.py`

Aktuelle Aufgaben:

- Attachment-Metadaten,
- File-URLs,
- Inline-Base64 für kleine Anhänge,
- ChatAI-URL,
- numerische Projekt-ID für ChatAI-Kompatibilität,
- Reply-Text-Extraktion,
- SSE-Format,
- ChatAI-Call,
- ChatAI-Actions,
- Missing-Slots-Karten,
- neutrale Versionierungs-Kompatibilität.

Geändertes Verhalten:

- `resolve_viewer_url(...)` liefert lokalen Editor-Iframe-Pfad.
- `maybe_post_viewer_card(...)` ist No-op.
- `auto_upload_supported_attachments(...)` lädt nichts mehr in alte Backends hoch.
- alte Viewer-/Speckle-Actions werden herausgefiltert.

Status: bereinigt.

---

### 15.3 `routes/chat/sync.py`

Route:

```text
POST /v1/chat
```

Aktuelles Verhalten:

1. Request lesen.
2. Conversation holen oder erzeugen.
3. User-Message speichern.
4. Attachment-Metadaten laden.
5. ChatAI synchron aufrufen.
6. alte Publish-Intents erkennen, aber nicht ausführen.
7. erlaubte ChatAI-Actions anwenden.
8. Assistant-Message speichern.
9. Editor-URL als `viewer_url`/`editor_url` zurückgeben.

Nicht mehr:

- kein Auto-Upload,
- kein alter Publish,
- keine Hintergrundthreads,
- kein Speckle.

Status: bereinigt.

---

### 15.4 `routes/chat/stream.py`

Route:

```text
POST /v1/chat/stream
```

Aktuelle SSE-Events:

```text
start
actions
delta
done
error
publish   # nur Hinweis, falls alter Publish-Intent erkannt wurde; executed=false
```

Nicht mehr:

```text
upload-Event für alten 3D-Upload
Hintergrundthread bei defer_upload
Speckle-Publish
Vectoplan-Repair/Align
```

Status: bereinigt.

---

## 16. UI-Routen

### Datei: `routes/ui/chat.py`

Aktuelle Aufgaben:

- `/ui`,
- `/ui/chat`,
- `/ui/chat-3d`,
- `/ui/chat/<chat_id>/lv`,
- `/ui/chat/<chat_id>/admin`,
- `/ui/chat/<chat_id>/viewer.json`,
- `/ui/chat/<chat_id>/versions.json`,
- `/ui/templates.json`,
- `/ui/chat/<chat_id>/upload`.

Wichtige Änderung:

- `/ui/chat-3d` nutzt Editor-URL statt alter Viewer-URL.
- `viewer.json` bleibt als Kompatibilitätsendpunkt, liefert aber Editor-Daten.
- UI-Upload speichert Dateien als Blob und lokale neutrale Version, aber veröffentlicht nichts in Speckle.

Status: zentraler Shell-Pfad bereinigt.

---

## 17. Dateien und Blobs

### Datei: `routes/files.py`

Routen:

```text
POST /v1/files
GET  /v1/files/<file_id>
GET  /v1/files/<file_id>/download
GET  /v1/files/<file_id>/content
```

Aktuelles Verhalten:

- speichert Multipart-Dateien als `Blob`,
- erzeugt SHA256,
- erkennt MIME defensiv,
- liefert Metadaten,
- liefert Download,
- liefert Inline-Content,
- setzt ETag-/Cache-/Security-Header.

Nicht enthalten:

- kein Publish,
- kein Import,
- keine Speckle-Kopplung.

Status: behalten und stabilisiert.

---

### Datei: `routes/blobs_base64.py`

Route:

```text
POST /v1/chats/<chat_id>/blobs/base64
```

Aktuelles Verhalten:

- akzeptiert Base64 oder Data-URL,
- limitiert Größe über `BASE64_UPLOAD_MAX_MB`,
- erkennt MIME über Hint, Dateiname und Magic Bytes,
- speichert `Blob`,
- optional Attachment-Nachricht im Transcript,
- liefert neutrale Blob-Metadaten.

Nicht enthalten:

- kein 3D-Publish,
- keine Speckle-Metadaten.

Status: behalten und stabilisiert.

---

## 18. Versionierungs-API

### Datei: `routes/versions_api.py`

Routen:

```text
POST   /v1/chats/<chat_id>/versions
GET    /v1/chats/<chat_id>/versions
GET    /v1/chats/<chat_id>/versions/<version_id>
DELETE /v1/chats/<chat_id>/versions/<version_id>
```

Aktuelles Verhalten:

- erstellt neutrale lokale Versionen,
- akzeptiert `input_blob_id` oder `blob_id`,
- akzeptiert Inline-Base64 oder Inline-JSON,
- schreibt Versionen über `versioning.record_version`,
- listet Versionen neutral,
- löscht transcript-basierte Versionseinträge.

Nicht mehr:

- keine Speckle-Felder,
- kein `speckle`-Payload,
- kein alter Viewer.

Status: bereinigt.

---

## 19. State und Viewer-Selection

### Datei: `routes/state.py`

Status: weiterhin allgemeine State-API.

---

### Datei: `routes/viewer_selection.py`

Routen:

```text
GET /v1/chats/<chat_id>/viewer/selection
PUT /v1/chats/<chat_id>/viewer/selection
```

Die URL bleibt aus Kompatibilitätsgründen erhalten.

Aktueller Payload ist neutral:

```json
{
  "ok": true,
  "mode": "editor",
  "workspace_mode": "3d",
  "legacy_3d_backend": false
}
```

Erlaubte Modi:

```text
editor
map
2d
lv
admin
```

Alte Felder werden nicht mehr gespeichert:

```text
project_id
model_id
version_id
commit_id
stream_id
viewer_url
raw_viewer_url
speckle_*
```

Status: bereinigt, URL bleibt kompatibel.

---

## 20. Alte Speckle-/Altviewer-Routen

Diese Dateien sind nicht mehr aktiv registriert:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
```

Aktueller Status:

- Sie sollten nicht mehr über `app.py` erreichbar sein.
- Falls sie noch im Repository liegen, sind sie zu löschen oder archivieren.
- Neue Routen dürfen sie nicht mehr importieren.

Empfohlene nächste Bereinigung:

```text
1. Projektweite Suche nach Imports dieser Dateien.
2. Wenn keine Imports mehr existieren: Dateien löschen.
3. src/vectoplan/ prüfen und anschließend löschen, falls nicht mehr importiert.
```

---

## 21. 2D-/CAD-Viewer

### Struktur

```text
routes/ui/viewer2d/
  __init__.py
  helpers.py
  pages.py
  cad_embed.py
```

Der Bereich bleibt erhalten und ist weiterhin ein gutes Muster für modulare Integrationen.

---

### `routes/ui/viewer2d/helpers.py`

Aktuelle Aufgaben:

- neutrale DXF-Version finden,
- Blob-Zugriff prüfen,
- Cache-/Frame-Header setzen,
- CAD-Service interne/public URLs lesen,
- HTTP JSON/File Helpers,
- CAD-Dateinamen säubern,
- Blob-Bytes laden,
- lokale Pfade intern abrufen.

Wichtige Änderung:

- `_find_latest_dxf_version` arbeitet mit neutralen Versionen.
- Speckle-/Altviewer-Metadaten werden ignoriert.

Status: stabilisiert.

---

### `routes/ui/viewer2d/pages.py`

Routen:

```text
GET /ui/chat/<chat_id>/cad2d
GET /ui/chat/<chat_id>/plan2d.json
GET /ui/chat/<chat_id>/file/<blob_id>.dxf
```

Aktuelles Verhalten:

- rendert 2D-Viewer,
- liefert aktuelle DXF-Quelle,
- erlaubt lokale DXF-Fallbacks,
- blockiert externe DXF-URLs, wenn `ALLOW_CDN=false`,
- liefert Blob-DXF nur, wenn Blob über Version mit Conversation verknüpft ist.

Status: stabilisiert.

---

### `routes/ui/viewer2d/cad_embed.py`

Route:

```text
GET /ui/chat/<chat_id>/cad-embed.json
```

Aktuelles Verhalten:

- findet neueste DXF-Version,
- baut CAD-Dateinamen,
- prüft Datei im CAD-Service,
- lädt DXF bei Bedarf hoch,
- liefert `embed_url`/`iframe_url`,
- arbeitet ohne 3D-/Speckle-Annahmen.

Status: stabilisiert.

---

## 22. Map / OpenLayer

### Datei: `routes/ui/map.py`

Routen:

```text
GET /ui/chat/<chat_id>/map
GET /ui/chat/<chat_id>/map.json
GET /ui/chat/<chat_id>/wfs
```

Aktuelles Verhalten:

- `/map` redirectet direkt auf den OpenLayer-Microservice,
- `/map.json` liefert nicht-geheime Kartenkonfiguration,
- `/wfs` bleibt sicherer GeoServer-WFS-Proxy.

Sicherheitsmerkmale:

- Query-Parameter-Allowlist,
- `typeNames`/`typeName`-Allowlist,
- serverseitige GeoServer-Credentials,
- keine Credentials im Frontend,
- keine Speckle-/Viewer-Abhängigkeit.

Status: behalten und stabilisiert.

---

## 23. Superset-Integration

### Datei: `routes/ui/superset.py`

Status: noch zu prüfen.

Soll-Zustand:

- reine Admin-/Analyse-Iframe-Integration,
- sichere relative Pfadbehandlung,
- keine alte Viewer-Abhängigkeit.

---

## 24. Crawlab-Integration

### Datei: `routes/ui/crawlab.py`

Status: noch zu prüfen.

Soll-Zustand:

- reine Admin-/DataMining-Iframe-Integration,
- sichere relative Pfadbehandlung,
- keine alte Viewer-Abhängigkeit.

---

## 25. Templates

### `templates/chat.html`

Status: behalten.

---

### `templates/chat_viewer.html`

Status: aktualisiert.

Wichtig:

- `viewer-frame` lädt initial Editor-URL,
- 3D-Button ist aktiv,
- `editorPagePath` ist in `APP_CONFIG`,
- `viewerJsonPath` bleibt nur kompatibel.

---

### `templates/viewer/admin.html`

Status: behalten.

---

### `templates/viewer/cad2d.html`

Status: behalten.

---

### `templates/viewer/lv.html`

Status: behalten.

---

### `templates/viewer/map.html`

Status: prüfen.

Da `/ui/chat/<chat_id>/map` aktuell direkt zum OpenLayer-Microservice redirectet, ist diese lokale Map-Datei vermutlich Legacy/Testcode.

---

## 26. Aktueller `/ui/chat-3d`-Gesamtfluss

```text
Browser
  ↓
GET /ui/chat-3d
  ↓
routes/ui/chat.py::chat_viewer_page
  ↓
Conversation suchen oder erzeugen
  ↓
project_welcome-Karte posten
  ↓
editor_url = /ui/chat/<chat_id>/editor
  ↓
render_template("chat_viewer.html", chat_id, editor_url, viewer_url)
  ↓
Frontend lädt static/js/chat/main.js
  ↓
main.js nutzt window.APP_CONFIG
  ↓
Workspace-Modi laden iframe-Ziele:
    3D    → /ui/chat/<chat_id>/editor
    Map   → /ui/chat/<chat_id>/map
    2D    → /ui/chat/<chat_id>/cad2d oder CAD embed
    LV    → /ui/chat/<chat_id>/lv
    Admin → /ui/chat/<chat_id>/admin
  ↓
/ui/chat/<chat_id>/editor
  ↓
302 Redirect zu VECTOPLAN_EDITOR_PUBLIC_URL + VECTOPLAN_EDITOR_ROUTE
```

Nicht mehr im Flow:

```text
ensure_and_refresh
ensure_placeholder_if_empty
Speckle upload
vectoplan.com proxy
/embed/frame
/vectoplan/repair
/vectoplan/align
```

---

## 27. Aktueller Teststatus

Der Importfehler:

```text
ImportError: cannot import name 'create_app' from 'app' (/app/app.py)
```

wurde auf eine falsche/überschriebene `app.py` bzw. eine nicht robuste App-Factory-Situation zurückgeführt.

Aktueller Fix:

- `app.py` enthält wieder Top-Level `create_app()`.
- Blueprints werden defensiv in der Factory importiert.
- `__all__ = ["create_app"]` ist gesetzt.

Erfolgreicher Test:

```powershell
docker compose -f docker-compose.all.yml run --rm --entrypoint sh vectoplan-app -lc 'cd /app && python -c "from app import create_app; print(create_app)"'
```

Status: Import der App-Factory funktioniert wieder.

---

## 28. Was bereits bereinigt wurde

```text
app.py
config.py
routes/ui/editor.py
routes/ui/chat.py
templates/chat_viewer.html
static/js/chat/main.js
versioning.py
routes/versions_api.py
routes/viewer_selection.py
routes/chat/helpers.py
routes/chat/sync.py
routes/chat/stream.py
routes/chat/crud.py
seed_templates.py
routes/templates.py
vectoplan.py
routes/ui/viewer2d/cad_embed.py
routes/ui/viewer2d/helpers.py
routes/ui/viewer2d/pages.py
routes/files.py
routes/blobs_base64.py
models.py
routes/ui/map.py
```

---

## 29. Was noch offen ist

### 29.1 Physisches Löschen alter Legacy-Dateien

Prüfen und löschen, sobald keine Imports mehr existieren:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
src/vectoplan/
```

### 29.2 Crawlab/Superset prüfen

Noch zu prüfen:

```text
routes/ui/crawlab.py
routes/ui/superset.py
```

### 29.3 Docker-Compose ergänzen

In `docker-compose.all.yml` sollte für `vectoplan-app` ergänzt oder geprüft werden:

```text
VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100
VECTOPLAN_EDITOR_ROUTE=/editor
```

Optional:

```text
vectoplan-app depends_on vectoplan-editor
```

Nur nötig, wenn die App beim Start zwingend davon ausgehen soll, dass der Editor bereit ist. Für reine iframe-URLs ist es nicht zwingend.

### 29.4 Projekt-/World-/Chunk-Kontext bewusst später

Noch nicht umgesetzt:

```text
/v1/chats/<chat_id>/editor/context
project_id/world_id/chunk_snapshot_id Integration
Editor-/Chunk-Schreibpfad
Import-Jobs
```

Diese Themen sind bewusst auf später verschoben.

---

## 30. Bekannte Risiken nach der Bereinigung

### 30.1 Alte Dateien können noch im Repository liegen

Auch wenn sie nicht mehr registriert sind, können alte Dateien noch vorhanden sein. Das ist kein unmittelbarer Runtime-Fehler, aber ein Wartungsrisiko.

### 30.2 `vectoplan.py` ist nur temporäre Kompatibilität

Die Datei verhindert Importbrüche, soll aber langfristig verschwinden.

### 30.3 Versionierung liegt weiterhin im Transcript

Das wurde neutralisiert, aber nicht strukturell gelöst.

Langfristig sollte geprüft werden:

```text
ProjectVersion statt Transcript-Versionen
Migration alter Versionseinträge
Indexierbarkeit
Auditierbarkeit
Rechte/Rollen
```

### 30.4 Blob-Daten liegen weiterhin direkt in der DB

Für Alpha brauchbar. Langfristig Object Storage prüfen.

### 30.5 Editor ist nur iframe-integriert

Es gibt noch keine App-seitige Projekt-/World-Kontextlogik. Das ist aktuell Absicht.

---

## 31. Empfohlene nächste Schritte

1. `routes/ui/crawlab.py` prüfen.
2. `routes/ui/superset.py` prüfen.
3. Alte Legacy-Dateien physisch löschen, wenn keine Imports mehr existieren.
4. Projektweite Suche durchführen:

```text
speckle
Speckle
VECTOPLAN_HOST
VECTOPLAN_TOKEN
VECTOPLAN_EMBED_TOKEN
SPECKLE_UPLOAD_TIMEOUT
/embed
/vectoplan/repair
/vectoplan/align
upload_file_to_project
ensure_and_refresh
ensure_placeholder_if_empty
```

5. App komplett starten:

```powershell
docker compose -f docker-compose.all.yml up -d --build vectoplan-app
```

6. Browser-Test:

```text
http://localhost:5103/ui/chat-3d
```

7. Prüfen:

```text
3D öffnet Editor
Map öffnet OpenLayer
2D öffnet CAD/DXF
LV öffnet Placeholder
Admin öffnet Admin-Shell
Chat funktioniert synchron
Chat funktioniert per SSE
Upload speichert Blob, aber published nicht
Versionen zeigen neutrale Einträge
```

---

## 32. Gesamtfazit

Die `vectoplan-app` ist jetzt deutlich näher an ihrer eigentlichen Rolle:

```text
vectoplan-app
  = Portal + Chat + Dateien + State + Versionen + UI-Shell

vectoplan-editor
  = 3D-Arbeitsfläche im iframe

vectoplan-chunk
  = spätere World-/Chunk-Daten

vectoplan-library
  = Objekt-/Asset-/Katalogdaten
```

Der alte Speckle-/Altviewer-Pfad ist aus der aktiven App-Laufzeit entfernt oder neutralisiert.

Der aktuelle Umbau hat bewusst nicht versucht, Projekt-/World-/Chunk-Strukturen neu zu modellieren. Der Editor wird zunächst nur eingebettet. Dadurch bleibt der Umbau kontrolliert, testbar und risikoarm.

Nächster sinnvoller Schritt ist nicht eine neue Architektur, sondern das vollständige Aufräumen der Restdateien und das Testen aller UI-Modi.
