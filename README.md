<!-- services/vectoplan-app/README.md -->

# VECTOPLAN App

`vectoplan-app` ist die zentrale Portal-, Projekt- und Workspace-Anwendung innerhalb des größeren VECTOPLAN-Systems.

Die App ist nicht als isolierte Einzelanwendung zu verstehen, sondern als Einstiegspunkt und Steuerzentrale für mehrere spezialisierte Microservices. Sie bündelt Projektverwaltung, Benutzerkontext, Berechtigungen, Workspace-Navigation, Service-Referenzen und die browserseitige Einbindung externer Arbeitsbereiche wie 3D, Map, 2D/CAD und Leistungsverzeichnis.

Der wichtigste Einstieg in der lokalen Entwicklung ist:

```text
http://localhost:5103/
```

Ein einzelnes Projekt wird über diese Form geöffnet:

```text
http://localhost:5103/project=<project_public_id>
```

Ein neues Projekt wird hier angelegt:

```text
http://localhost:5103/project=new
```

---

## Rolle im Gesamtsystem

VECTOPLAN besteht aus mehreren Services, die jeweils eigene Zuständigkeiten haben. `vectoplan-app` übernimmt dabei die Rolle der Portal- und Projekt-Shell.

Die App entscheidet, welches Projekt geöffnet ist, welcher Benutzer im aktuellen Entwicklungsstand arbeitet, welche Rechte gelten und welcher Workspace angezeigt wird. Sie speichert Projekt-Metadaten, Adressen, Koordinaten, Sichtbarkeit, Berechtigungen, Verweise auf externe Services, Versionen und Audit-Events.

Die eigentlichen Fachsysteme bleiben aber getrennt. Der 3D-Editor, die Chunk-Welt, die Kartenansicht, die 2D-/CAD-Fläche, das Leistungsverzeichnis und die Asset-Bibliothek sind eigene Services. Die App zeigt diese Services an, verwaltet aber nicht deren fachliche Wahrheit.

Vereinfacht:

```text
vectoplan-app
  = Portal, Projektverwaltung, Shell, Rechte, Referenzen, Workspace-Steuerung

vectoplan-editor
  = 3D-Editor und 3D-Arbeitsfläche

vectoplan-openLayer
  = Karten- und Map-Arbeitsfläche

vectoplan-chunk
  = Chunk-Welt, Runtime-State und editierbare Weltstruktur

vectoplan-2d
  = 2D-/CAD-/Planansicht

vectoplan-lv
  = Leistungsverzeichnis

vectoplan-library
  = Assets, Bauteile, Inventar und Bibliothek
```

Die zentrale Architekturregel lautet:

```text
Browser / iframe / redirect  → PUBLIC_URL
Backend / server-to-server   → INTERNAL_URL
```

Das bedeutet: Alles, was im Browser, in einem iframe oder in einem Link landet, muss eine browserfähige Public-URL verwenden. Docker-interne Hostnamen dürfen nur serverseitig verwendet werden.

---

## Was die App aktuell kann

Die App ist aktuell projektgeführt aufgebaut. Der sichtbare Chat wurde aus der Hauptoberfläche entfernt. Die Oberfläche besteht aus einer linken Projekt-Sidebar und einem rechten Workspace.

Die App kann aktuell:

* Projekte erstellen,
* Projekte bearbeiten,
* Projekte über `/project=<project_public_id>` öffnen,
* eine linke Projektliste anzeigen,
* aktive Projekte markieren,
* Projektinformationen speichern,
* Adressen und Koordinaten speichern,
* Sichtbarkeit verwalten,
* Projekt-Berechtigungen vorbereiten,
* Owner-Memberships erzeugen,
* Embed-Policies verwalten,
* Service-Links auf externe Systeme speichern,
* Versionseinträge pro Projekt vorbereiten,
* Audit-Events schreiben,
* Workspace-Modi wie Projekt, Map, 3D, 2D, LV und Admin steuern,
* Map/3D/2D/LV erst nach Projekt-Konfiguration freischalten,
* externe Workspaces im iframe öffnen,
* Browser-Public-URLs und Docker-Internal-URLs getrennt halten.

Die App kann bewusst noch nicht alles produktiv. Echte Authentifizierung, echte Benutzerverwaltung, produktive Chunk-Erzeugung, vollständige LV-Anbindung und vollständige Bereinigung alter Chat-/Speckle-Reste sind noch offene Schritte.

---

## Was die App nicht ist

`vectoplan-app` ist keine 3D-Modellwahrheit. Sie ist auch kein Ersatz für den Editor, keine Chunk-Datenbank, kein CAD-Service, kein LV-Service und kein Kartenserver.

Die App speichert zum Beispiel nicht die eigentliche 3D-Welt. Sie speichert nur Referenzen wie:

```text
chunk_project_id
chunk_world_id
plan2d_id
lv_id
```

Die fachlichen Daten liegen später in den passenden Services.

Dadurch bleibt die App schlank: Sie organisiert Projekte und Workspaces, aber sie übernimmt nicht die Aufgaben der spezialisierten Services.

---

## Aktuelle Benutzeroberfläche

Die neue Hauptoberfläche ist projektorientiert.

Das Layout ist:

```text
Projekt-Sidebar | Workspace
```

Links befindet sich die Projekt-Sidebar mit aktueller Projektkarte, Suchfeld, Projektliste und Button für ein neues Projekt. Rechts befindet sich der Workspace mit Toolbar und iframe.

Die Toolbar enthält aktuell:

```text
Projekt
Map
3D
2D
LV
Admin
Versionen
Theme
Öffnen
```

Der sichtbare Chat ist nicht mehr Bestandteil der Hauptoberfläche. Entfernt wurden:

```text
VectoAI - Gebäudegenerierung & Datenanalyse
Chat-Schließen-X
Nachrichtenliste
Datei-anhängen-Button
Nachricht eingeben…
Senden-Button
Disclaimer
Chat-Öffnen-Icon neben Projekt
```

Einige Dateien tragen aus historischen Gründen noch `chat` im Namen, obwohl sie inzwischen die Projekt-/Workspace-Shell steuern.

---

## Projekt- und Workspace-Logik

Der Workspace startet immer im Modus `Projekt`. Dort wird das Projektformular geladen.

Für ein neues Projekt:

```text
/project=new
  → App-Shell
  → iframe: /ui/project/new
```

Für ein bestehendes Projekt:

```text
/project=<project_public_id>
  → App-Shell
  → iframe: /ui/project/<project_public_id>/project
```

Wenn ein Projekt gespeichert und ausreichend definiert ist, gilt es als konfiguriert. Dann werden weitere Arbeitsbereiche freigeschaltet.

Die aktuelle Freischaltlogik ist:

```text
Neues Projekt
  → nur Projektformular aktiv

Gespeichertes, aber nicht konfiguriertes Projekt
  → Projekt, Admin und Versionen aktiv
  → Map, 3D, 2D und LV gesperrt

Konfiguriertes Projekt
  → Projekt, Admin, Versionen, Map, 3D, 2D und LV aktiv
```

Ein Projekt gilt aktuell als konfiguriert, wenn die minimale Projektdefinition vorhanden ist. Dazu gehören insbesondere ein Projektname und eine nutzbare Adresse oder Koordinaten.

---

## Wichtige UI-Flüsse

### Neues Projekt erstellen

```text
User öffnet /project=new
  ↓
routes/ui/projects.py rendert die Shell
  ↓
templates/chat_viewer.html lädt die Projekt-Shell
  ↓
iframe lädt /ui/project/new
  ↓
templates/viewer/project.html zeigt das Formular
  ↓
static/js/project/project_form.js sendet POST /v1/projects
  ↓
services/project_service.py erstellt das Projekt
  ↓
Owner-Membership, Embed-Policy und Audit-Event werden erzeugt
  ↓
Frontend navigiert auf /project=<project_public_id>
```

### Projekt bearbeiten

```text
User öffnet /project=<project_public_id>
  ↓
Projekt wird geladen
  ↓
Berechtigung wird geprüft
  ↓
iframe lädt /ui/project/<project_public_id>/project
  ↓
Projektformular wird mit vorhandenen Daten angezeigt
  ↓
Änderungen werden per PATCH /v1/projects/<project_public_id> gespeichert
```

### 3D öffnen

```text
User klickt 3D
  ↓
main.js prüft, ob Projekt konfiguriert ist
  ↓
iframe lädt /ui/project/<project_public_id>/editor
  ↓
routes/ui/editor.py baut browserfähige Editor-URL
  ↓
vectoplan-editor wird im iframe geöffnet
```

### Map öffnen

```text
User klickt Map
  ↓
main.js prüft, ob Projekt konfiguriert ist
  ↓
iframe lädt /ui/project/<project_public_id>/map
  ↓
routes/ui/map.py baut browserfähige OpenLayer-URL
  ↓
vectoplan-openLayer wird im iframe geöffnet
```

---

## Ordner- und Dateistruktur

Die aktuelle Struktur der App ist in Schichten organisiert: App-Start, Models, Services, Routes, Templates und Static Assets.

```text
services/vectoplan-app/
│
├── app.py
├── config.py
├── extensions.py
├── auth.py
├── versioning.py
├── seed_templates.py
│
├── models/
│   ├── __init__.py
│   ├── base.py
│   ├── users.py
│   ├── legacy.py
│   ├── projects.py
│   ├── project_access.py
│   ├── project_embed.py
│   ├── project_links.py
│   ├── project_versions.py
│   ├── project_audit.py
│   └── core.py
│
├── services/
│   ├── current_user.py
│   ├── project_permissions.py
│   └── project_service.py
│
├── routes/
│   ├── projects_api.py
│   │
│   ├── ui/
│   │   ├── projects.py
│   │   ├── chat.py
│   │   ├── editor.py
│   │   ├── map.py
│   │   ├── viewer2d.py
│   │   ├── crawlab.py
│   │   └── superset.py
│   │
│   ├── chat/
│   │   ├── __init__.py
│   │   ├── crud.py
│   │   ├── helpers.py
│   │   ├── sync.py
│   │   └── stream.py
│   │
│   ├── files.py
│   ├── blobs_base64.py
│   ├── versions_api.py
│   ├── templates.py
│   ├── state.py
│   ├── viewer_selection.py
│   │
│   ├── embed.py
│   ├── speckle_upload.py
│   ├── vectoplan_ingest.py
│   └── vectoplan_align.py
│
├── templates/
│   ├── chat_viewer.html
│   │
│   ├── partials/
│   │   └── project_sidebar.html
│   │
│   └── viewer/
│       ├── project.html
│       ├── admin.html
│       ├── cad2d.html
│       ├── lv.html
│       └── map.html
│
└── static/
    ├── css/
    │   ├── chat.css
    │   ├── project_sidebar.css
    │   ├── project_workspace.css
    │   └── cards.css
    │
    └── js/
        ├── chat/
        │   ├── main.js
        │   ├── project_sidebar_data.js
        │   ├── project_sidebar_resize.js
        │   └── project_sidebar.js
        │
        └── project/
            └── project_form.js
```

---

## App-Start und Konfiguration

`app.py` ist die Flask-App-Factory. Dort wird die App erzeugt, konfiguriert und mit Blueprints verbunden. Außerdem werden Datenbank, Health-/Ready-Routen, Startup-Checks und Sicherheitsheader vorbereitet.

`config.py` enthält die Konfiguration aus Environment-Variablen. Besonders wichtig ist dort die Trennung zwischen Public- und Internal-URLs.

`extensions.py` stellt zentrale Flask-Erweiterungen bereit, insbesondere die SQLAlchemy-Datenbankinstanz `db`.

---

## Model-Schicht

Die Models sind modular aufgebaut. Das alte monolithische Modell wurde in mehrere Dateien aufgeteilt.

`models/base.py` enthält gemeinsame Grundlagen: JSON-Typen, Zeitstempel, Soft-Delete, sichere Helper, ID-Generatoren und Normalisierungen.

`models/users.py` enthält `AppUser`. Aktuell arbeitet die App in der ersten Entwicklungsphase mit einem Platzhalter-User:

```text
id = 1
public_id = u_demo_1
handle = demo
display_name = Demo User
role = admin
```

`models/projects.py` enthält das zentrale Projektmodell `Project`. Dort liegen Projektname, Beschreibung, Adresse, Koordinaten, Sichtbarkeit, Setup-Status und Referenzen auf andere Services.

`models/project_access.py` enthält `ProjectMembership`. Darüber werden Rollen und Rechte verwaltet.

`models/project_embed.py` enthält `ProjectEmbedPolicy`. Darüber wird gesteuert, ob und wie ein Projekt eingebettet werden darf.

`models/project_links.py` enthält `ProjectServiceLink`. Damit werden App-Projekte mit Ressourcen anderer Services verbunden.

`models/project_versions.py` enthält `ProjectVersion`. Damit werden Projektstände, Snapshots und Artefakte referenziert.

`models/project_audit.py` enthält `ProjectAuditEvent`. Damit können Projektaktionen nachvollzogen werden.

`models/legacy.py` enthält technische Basisobjekte, die noch gebraucht werden, zum Beispiel `Conversation`, `Blob`, `Job` und `ConversationState`.

`models/core.py` definiert keine eigenen Models mehr. Es dient nur noch als Kompatibilitäts-Export für ältere Imports.

`models/__init__.py` ist der zentrale Import-Hub.

Beispiel:

```python
from models import Project, ProjectMembership, ProjectVersion
```

---

## Service-Schicht

Die Service-Schicht enthält die fachliche Logik der App.

`services/current_user.py` kapselt den aktuellen Benutzer. Aktuell wird immer der Demo-User mit `id=1` verwendet. Später kann diese Datei an echte Authentifizierung angebunden werden.

`services/project_permissions.py` enthält die Rechte- und Rollenlogik. Die App kennt aktuell die Rollen:

```text
owner
admin
editor
viewer
```

und die Rechte:

```text
view
edit
manage
delete
transfer
embed
```

`services/project_service.py` enthält die zentrale Projektlogik. Dort werden Projekte erstellt, aktualisiert, gelöscht, serialisiert und für die Sidebar aufbereitet. Außerdem werden Owner-Membership, Embed-Policy, Service-Links, Versionen und Audit-Events erzeugt oder aktualisiert.

Der typische Backend-Fluss ist:

```text
Route
  ↓
project_service.py
  ↓
project_permissions.py
  ↓
models/
  ↓
db.session
```

---

## Routen

Die wichtigste API-Datei ist:

```text
routes/projects_api.py
```

Sie stellt die JSON-API für Projekte bereit.

Wichtige Endpoints:

```text
GET    /v1/projects/_status
GET    /v1/projects/current-user
GET    /v1/projects
POST   /v1/projects
GET    /v1/projects/sidebar
GET    /v1/projects/<project_id>
PATCH  /v1/projects/<project_id>
DELETE /v1/projects/<project_id>
GET    /v1/projects/<project_id>/members
POST   /v1/projects/<project_id>/versions
POST   /v1/projects/<project_id>/service-links
GET    /v1/projects/<project_id>/embed-policy
```

Die wichtigste UI-Datei ist:

```text
routes/ui/projects.py
```

Sie rendert die neue projektgeführte Shell.

Wichtige UI-Routen:

```text
GET /
GET /project=new
GET /project=<project_id>
GET /ui/project/new
GET /ui/project/<project_id>/project
GET /ui/project/<project_id>/admin
GET /ui/project/<project_id>/lv
GET /ui/project/<project_id>/context.json
```

Weitere UI-Gateways:

```text
routes/ui/editor.py   → 3D-Editor
routes/ui/map.py      → OpenLayer Map
routes/ui/viewer2d.py → 2D/CAD
```

`routes/ui/chat.py` und `routes/chat/` sind aktuell noch vorhanden, gehören aber nicht mehr zum neuen primären UI-Fluss. Sie sind Legacy-/Kompatibilitätsbereiche und sollten später weiter bereinigt werden.

---

## Templates und Frontend

`templates/chat_viewer.html` ist trotz Name die Haupt-Shell der App. Dort werden Projekt-Sidebar, Toolbar, iframe und `window.APP_CONFIG` erzeugt.

`templates/partials/project_sidebar.html` enthält die linke Projektliste.

`templates/viewer/project.html` enthält das Projektformular, das im iframe geladen wird.

`static/css/chat.css` steuert das Shell-Layout. Der Name ist historisch. Die Datei enthält inzwischen das Layout für Projekt-Sidebar und Workspace und deaktiviert Chat-UI-Reste.

`static/css/project_sidebar.css` steuert die Projekt-Sidebar.

`static/css/project_workspace.css` steuert das Projektformular.

`static/js/chat/main.js` ist der Workspace-Orchestrator. Der Name ist historisch. Die Datei steuert Workspace-Wechsel, iframe-Ziele, Projekt-Gating, Theme, Versionen und Sidebar-Integration.

`static/js/chat/project_sidebar_data.js`, `project_sidebar_resize.js` und `project_sidebar.js` steuern die Projekt-Sidebar.

`static/js/project/project_form.js` steuert das Projektformular und sendet Projektänderungen an die API.

---

## Beispiel: Projekt erstellen

```bash
curl -X POST http://localhost:5103/v1/projects \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Testprojekt",
    "description": "Erstes Testprojekt",
    "address_text": "Musterstraße 1, 12345 Berlin, Deutschland",
    "street": "Musterstraße",
    "house_number": "1",
    "postal_code": "12345",
    "city": "Berlin",
    "region": "Berlin",
    "country": "DE",
    "latitude": 52.52,
    "longitude": 13.405,
    "visibility": "private",
    "is_public": false
  }'
```

Erwartetes Ergebnis ist ein Projekt mit `public_id`, Setup-Status und Redirect-URL.

---

## Beispiel: Projektliste für die Sidebar laden

```bash
curl http://localhost:5103/v1/projects/sidebar
```

Die Antwort enthält die Projekte, die links in der Sidebar angezeigt werden.

---

## Beispiel: Projektversion anlegen

```bash
curl -X POST http://localhost:5103/v1/projects/prj_234bdebdd2c841d6a6560630/versions \
  -H "Content-Type: application/json" \
  -d '{
    "label": "Projektstand 1",
    "description": "Erster gespeicherter Projektstand",
    "kind": "metadata",
    "status": "stored",
    "service": "app",
    "artifact_ref": {
      "type": "project_metadata"
    }
  }'
```

---

## Entwicklungsdatenbank

In der aktuellen Entwicklungsphase muss keine Kompatibilität mit alten lokalen Tabellen gewährleistet werden.

Wichtig:

```text
db.create_all() ergänzt keine fehlenden Spalten in bestehenden Tabellen.
```

Wenn sich Models geändert haben und die lokale Datenbank noch alte Tabellen enthält, sollte die Entwicklungsdatenbank zurückgesetzt werden:

```bash
docker compose down -v
docker compose up --build
```

Typische Fehler bei altem Schema:

```text
column app_users.handle does not exist
column conversations.owner_user_id does not exist
address_street is an invalid keyword argument for Project
```

---

## Aktuelle Altlasten

Einige Namen und Dateien sind historisch, weil die App ursprünglich stärker aus der Chat-Shell heraus entstanden ist.

Historische Namen:

```text
templates/chat_viewer.html
static/css/chat.css
static/js/chat/main.js
static/js/chat/project_sidebar*.js
```

Später könnten diese umbenannt werden zu:

```text
templates/app_shell.html
static/css/app_shell.css
static/js/shell/main.js
static/js/project_sidebar/*
```

Außerdem existieren noch alte Chat-/Speckle-/Altviewer-Bereiche, die später geprüft und entfernt oder deaktiviert werden sollten:

```text
routes/chat/
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
versioning.py
seed_templates.py
```

---

## Debug im Browser

Die App stellt im Browser eine Debug-API bereit:

```js
window.__VECTOPLAN_WORKSPACE_DEBUG__
```

Beispiele:

```js
window.__VECTOPLAN_WORKSPACE_DEBUG__.project.current()
window.__VECTOPLAN_WORKSPACE_DEBUG__.project.configured()
window.__VECTOPLAN_WORKSPACE_DEBUG__.setWorkspaceMode("map")
window.__VECTOPLAN_WORKSPACE_DEBUG__.projectSidebar.refresh()
```

Das aktuelle Projekt liegt zusätzlich hier:

```js
window.__VECTOPLAN_CURRENT_PROJECT__
```

---

## Aktueller Stand

Die `vectoplan-app` ist aktuell eine projektgeführte Portal- und Workspace-Shell.

Vorher war die App stärker als Chat-Shell mit Workspace gedacht. Jetzt ist das Projekt der Mittelpunkt. Die Projektliste ist links sichtbar, der Workspace ist rechts eingebettet, und die einzelnen Arbeitsbereiche werden abhängig vom Projektstatus freigeschaltet.

Der wichtigste aktuelle Einstieg ist:

```text
http://localhost:5103/
```

Der wichtigste Projekt-Einstieg ist:

```text
http://localhost:5103/project=<project_public_id>
```

Der technische Kern der App ist aktuell:

```text
Project
ProjectMembership
ProjectEmbedPolicy
ProjectServiceLink
ProjectVersion
ProjectAuditEvent
```

Die nächsten sinnvollen Schritte sind:

1. `app.py` final prüfen.
2. Blueprint-Registrierung und CSP auf den neuen Projektfluss abstimmen.
3. Alte Chat-/Speckle-Routen deaktivieren oder entfernen.
4. Versionierung vollständig auf `ProjectVersion` umstellen.
5. Service-Links produktiv an Chunk, 2D, LV und Map anbinden.
6. Historische Dateinamen später umbenennen.
