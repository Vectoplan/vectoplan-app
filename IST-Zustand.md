Teil 1 aktualisiert die vorhandene IST-Datei und integriert den aktuellen Stand nach Chunk-Anbindung, Runtime-Tests und der `ConversationState`-Reparatur. Grundlage ist deine bestehende IST-Datei. 

<!-- /vectoplan-website/services/vectoplan-server/docs/IST-Zustand-vectoplan-app.md -->

# IST-Zustand – `vectoplan-app`

Stand: 2026-06-24
Status: Projektgeführte Portal-App mit App↔Chunk-Provisioning, Workspace-Orchestrierung, modularen App-Models und repariertem Conversation-State-Kompatibilitätspfad

> Teil 1 von 2

---

## 1. Zweck dieses Dokuments

Diese Datei beschreibt den aktuellen IST-Zustand der `vectoplan-app` nach dem Umbau von einer chatgeführten Shell zu einer projektgeführten Portal-, Projekt- und Workspace-App.

Die Datei ist eine Bestandsaufnahme des aktuellen Entwicklungsstands. Sie beschreibt:

```text
- welche Rolle vectoplan-app im Gesamtsystem hat,
- welche Dateien neu entstanden oder wesentlich angepasst wurden,
- wie Projektverwaltung, Workspace-Shell und Service-Referenzen funktionieren,
- wie vectoplan-app mit vectoplan-chunk zusammenarbeitet,
- welche Legacy-/Kompatibilitätspfade noch existieren,
- welche Punkte stabil sind,
- welche Punkte später bereinigt werden sollten.
```

Der sichtbare Chat ist aus der Hauptoberfläche entfernt. Die App ist primär eine Projekt-Shell:

```text
Projektliste | Workspace
```

Nicht mehr:

```text
Projektliste | Chat | Workspace
```

Der technische Conversation-/State-Unterbau existiert weiterhin, aber nicht mehr als sichtbarer Chat-Fokus.

---

## 2. Kurzbefund

Die `vectoplan-app` ist jetzt die zentrale Portal- und Projektverwaltungs-App.

Ihre aktuelle Rolle ist:

```text
vectoplan-app
  = Portal
  = Projektverwaltung
  = Berechtigungsverwaltung
  = Workspace-Shell
  = iframe-Orchestrator
  = zentrale Referenzverwaltung
  = App-seitiger Audit-/Version-/Service-Link-Host
  = App-seitiger Einstieg in Chunk-Provisioning
```

Nicht ihre Rolle:

```text
vectoplan-app
  ≠ 3D-Editor
  ≠ Chunk-Welt-Wahrheit
  ≠ CAD-Geometrie-Wahrheit
  ≠ LV-Fachdaten-Wahrheit
  ≠ OpenLayer-Ersatz
  ≠ sichtbarer Chat-Client
```

Primäre Browser-Einstiege sind:

```text
http://localhost:5103/
http://localhost:5103/project=new
http://localhost:5103/project=<project_public_id>
```

Beispiel:

```text
http://localhost:5103/project=prj_979eb0a4d8894086a5b2a74b
```

Die frühere Route `/ui/chat-3d` kann technisch noch existieren, ist aber nicht mehr der Zielpfad für die neue Projekt-Shell.

---

## 3. Aktueller Gesamtstand

Aktuell erreicht:

```text
1. Die App startet projektgeführt.
2. Links befindet sich eine Projekt-Sidebar.
3. Der Workspace startet im Modus Projekt.
4. Der sichtbare Chat wurde aus der Hauptoberfläche entfernt.
5. Das Chat-Öffnen-Symbol neben Projekt wurde entfernt.
6. Projekt-Erstellung und Projekt-Bearbeitung laufen über /ui/project/...
7. Projekt-Daten werden über /v1/projects erstellt und aktualisiert.
8. Map, 3D, 2D und LV werden erst nach Projekt-Konfiguration freigeschaltet.
9. Projekt-Models wurden modularisiert.
10. models/core.py ist Kompatibilitäts-Export.
11. routes/projects_api.py stellt die Projekt-JSON-API bereit.
12. routes/ui/projects.py rendert die Projekt-Shell.
13. Browser-Public-URLs und Docker-Internal-URLs bleiben getrennt.
14. vectoplan-app speichert nur App-Projekt-/Referenz-/Berechtigungsdaten.
15. vectoplan-app kann über chunk_client.py einen Chunk-Projektgraphen im Chunk-Service sicherstellen.
16. vectoplan-chunk liefert für App-Projekte eine konkrete world_spawn.
17. Editor kann über App-Projektkontext Chunks aus world_spawn laden.
18. ConversationState-Kompatibilität ist repariert.
```

Wichtigster aktueller Testbefund:

```text
vectoplan-app
  → erzeugt/öffnet App-Projekt

vectoplan-app
  → PUT /projects/by-app/<app_project_public_id>
  → vectoplan-chunk legt/holt Chunk-Projekt, Universe und world_spawn

vectoplan-editor
  → lädt über Chunk-Service world_spawn chunks/batch

Resultat:
  App ↔ Chunk ↔ Editor funktioniert.
```

---

## 4. Zuletzt reparierte Kernprobleme

### 4.1 Chunk-Integration

Vorher war der Chunk-Readiness-Pfad teilweise defekt, weil die Default-Welt `world_spawn` fehlte oder `flat` fälschlich als konkrete Welt verwendet wurde.

Aktueller Zielzustand:

```text
world_spawn = konkrete editierbare WorldInstance
flat        = Template-/Provider-Welt
```

Dieser Zustand ist jetzt im Chunk-Service hergestellt und von der App nutzbar.

Für die App bedeutet das:

```text
App-Projekt
  ↓
Chunk-Provisioning
  ↓
Chunk-Projekt
  ↓
Chunk-Universe
  ↓
WorldInstance world_spawn
  ↓
Editor lädt chunks/batch
```

---

### 4.2 App-interner ConversationState-Fehler

Aufgetretener Fehler:

```text
AttributeError: type object 'ConversationState' has no attribute 'merge_patch'
AttributeError: type object 'ConversationState' has no attribute 'get_or_create'
```

Ursache:

```text
routes/viewer_selection.py erwartete ältere/kompatible Classmethods auf ConversationState.
models/legacy.py stellte diese Methoden nicht bereit.
```

Reparatur in:

```text
services/vectoplan-app/models/legacy.py
```

Neu bzw. ergänzt:

```text
ConversationState.state_json
ConversationState.get_or_create(...)
ConversationState.merge_patch(...)
ConversationState.replace_state(...)
merge_conversation_state_patch(...)
replace_conversation_state(...)
```

Auswirkung:

```text
/v1/chats/<chat_id>/viewer/selection
```

kann den neutralen Workspace-/Viewer-State wieder speichern.

Die Warnung

```text
blueprint state_api_bp unavailable; skipped
```

ist weiterhin separat zu betrachten. Sie ist aktuell kein Blocker, weil die App trotzdem startet und die konkret fehlerhafte ConversationState-API repariert wurde.

---

## 5. Aktuelle Service-Rollen

### 5.1 `vectoplan-app`

Rolle:

```text
Portal
Projektverwaltung
Projekt-Sidebar
Workspace-Shell
iframe-Orchestrator
Projekt-Metadaten
Projekt-Berechtigungen
Embed-Policy
Service-Links
Version-Referenzen
Audit-Events
App↔Chunk-Provisioning-Orchestrierung
technischer Conversation-/Workspace-State
```

Nicht Rolle:

```text
3D-Modellwahrheit
Chunk-Welt-Wahrheit
OpenLayer-Datenwahrheit
2D-CAD-Geometriewahrheit
LV-Fachdatenwahrheit
sichtbarer Chat-Client
```

Die App verwaltet zentrale App-Objekte wie:

```text
Project
ProjectMembership
ProjectEmbedPolicy
ProjectServiceLink
ProjectVersion
ProjectAuditEvent
AppUser
Conversation
ConversationState
Blob
Job
```

---

### 5.2 `vectoplan-editor`

Rolle:

```text
3D-Editor
Browser-Runtime
3D-Interaktion
Pointer-Lock-/First-Person-Workspace
Verbraucher von Chunk-/Library-Informationen
```

Direkter Browser-Test:

```text
http://localhost:5100/editor
```

Embed aus der App:

```text
/ui/project/<project_public_id>/editor
```

Die App darf im Browser nur die Public-URL verwenden:

```text
VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100
```

Nicht im Browser verwenden:

```text
http://vectoplan-editor:5000
```

---

### 5.3 `vectoplan-openLayer`

Rolle:

```text
Map-Service
OpenLayer-Kartenoberfläche
Geodaten-/Karten-Workspace
```

Direkter Browser-Test:

```text
http://localhost:5190/map
```

Embed aus der App:

```text
/ui/project/<project_public_id>/map
```

Die App darf im Browser nur die Public-URL verwenden:

```text
OPENLAYER_PUBLIC_URL=http://localhost:5190
```

Nicht im Browser verwenden:

```text
http://openlayer:8090
```

---

### 5.4 `vectoplan-chunk`

Rolle:

```text
Chunk-Projekt
Chunk-Universe
Chunk-Welt
bearbeitbare Welt-/Runtime-Wahrheit
interne Service-Kommunikation
```

Nicht Browser-Ziel:

```text
http://vectoplan-chunk:5000
```

Die App kommuniziert serverseitig mit:

```text
VECTOPLAN_CHUNK_INTERNAL_URL=http://vectoplan-chunk:5000
```

Die App speichert nur Referenzen wie:

```text
chunk_project_id
chunk_universe_id
chunk_world_id
```

Wichtig:

```text
chunk_world_id = world_spawn
```

für den aktuell getesteten Standardpfad.

---

### 5.5 `vectoplan-2d`

Rolle:

```text
2D-/CAD-Workspace
Plan-/DXF-/CAD-Anzeige
```

Die App speichert nur Referenzen wie:

```text
plan2d_id
```

---

### 5.6 `vectoplan-lv`

Rolle:

```text
Leistungsverzeichnis
LV-Fachdaten
```

Die App speichert nur Referenzen wie:

```text
lv_id
```

---

### 5.7 `vectoplan-library`

Rolle:

```text
Library-/Inventory-Quelle
Assets
Bauteilbibliothek
```

Nicht Browser-Ziel aus der App-Shell:

```text
http://vectoplan-library:5000
```

---

## 6. Zentrale Architekturentscheidung

Die wichtigste Regel bleibt:

```text
Browser / iframe / redirect  → PUBLIC_URL
Backend / server-to-server   → INTERNAL_URL
```

### 6.1 Browser-/Public-URLs

Diese URLs dürfen im Browser, in iframes, Redirects und Links erscheinen:

```text
VECTOPLAN_APP_PUBLIC_URL=http://localhost:5103
VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100
OPENLAYER_PUBLIC_URL=http://localhost:5190
```

### 6.2 Docker-/Internal-URLs

Diese URLs sind nur für Server-zu-Server-Kommunikation innerhalb Docker gedacht:

```text
VECTOPLAN_EDITOR_INTERNAL_URL=http://vectoplan-editor:5000
OPENLAYER_INTERNAL_URL=http://openlayer:8090
VECTOPLAN_CHUNK_INTERNAL_URL=http://vectoplan-chunk:5000
VECTOPLAN_LIBRARY_INTERNAL_URL=http://vectoplan-library:5000
```

Browser dürfen diese internen Hostnamen nicht sehen:

```text
vectoplan-editor
openlayer
vectoplan-chunk
vectoplan-library
```

---

## 7. Aktueller UI-Gesamtfluss

### 7.1 Root

```text
Browser
  ↓
GET http://localhost:5103/
  ↓
routes/ui/projects.py
  ↓
_render_project_shell(selected_project=None, is_new=True)
  ↓
templates/chat_viewer.html
  ↓
Projekt-Sidebar + Workspace
  ↓
iframe src = /ui/project/new
  ↓
templates/viewer/project.html
```

Ergebnis:

```text
Projektliste links
Projektformular im Workspace rechts
kein sichtbarer Chat
```

---

### 7.2 Neues Projekt

```text
Browser
  ↓
GET http://localhost:5103/project=new
  ↓
Projekt-Shell
  ↓
iframe src = /ui/project/new
  ↓
Projektformular
```

Beim Speichern:

```text
POST /v1/projects
```

Danach:

```text
Projekt wird app-seitig erstellt
Owner-Membership wird erstellt
Embed-Policy wird erstellt
Audit-Event wird geschrieben
Chunk-Provisioning wird angestoßen oder nachgelagert sichergestellt
Sidebar Refresh
Parent Redirect auf /project=<project_public_id>
Workspace-Gating wird aktualisiert
```

---

### 7.3 Bestehendes Projekt

```text
Browser
  ↓
GET http://localhost:5103/project=<project_public_id>
  ↓
Projekt laden
  ↓
Berechtigung prüfen
  ↓
Projekt-Shell rendern
  ↓
iframe src = /ui/project/<project_public_id>/project
```

---

### 7.4 Workspace-Modi

Toolbar-Modi:

```text
Projekt
Map
3D
2D
LV
Admin
Versionen
```

Regel:

```text
Projekt: immer verfügbar
Admin: verfügbar, wenn Projekt existiert
Map/3D/2D/LV: verfügbar, wenn Projekt konfiguriert ist
Versionen: verfügbar, wenn Projekt existiert
```

---

## 8. Projekt-Konfiguration

Ein Projekt gilt als konfiguriert, wenn die Minimaldefinition vorhanden ist.

Aktuell relevant:

```text
name
address_text oder street/city oder coordinates
```

Nach erfolgreicher Konfiguration:

```text
setup_status = configured
is_configured = true
```

Dann werden freigeschaltet:

```text
Map
3D
2D
LV
```

---

## 9. Aktuelle App↔Chunk-Integration

### 9.1 Ziel

Die App soll beim Anlegen oder Öffnen eines App-Projekts sicherstellen können, dass im Chunk-Service ein passender Chunk-Projektgraph existiert.

Zielgraph im Chunk-Service:

```text
Chunk Project
  └─ Universe
      └─ WorldInstance world_spawn
```

Die App speichert danach nur Referenzen.

---

### 9.2 Neuer/angepasster App-Client

Datei:

```text
services/vectoplan-app/services/chunk_client.py
```

Zweck:

```text
serverseitiger Client für vectoplan-chunk
```

Aufgaben:

```text
Chunk-Service-Status prüfen
Chunk-Projekt für App-Projekt sicherstellen
Antwort normalisieren
Chunk-Referenzen extrahieren
Fehler kontrolliert zurückgeben
keine Browser-internen Docker-URLs leaken
```

Wichtige Zielroute im Chunk-Service:

```text
PUT /projects/by-app/<app_project_public_id>
```

Beispiel:

```text
PUT http://vectoplan-chunk:5000/projects/by-app/prj_979eb0a4d8894086a5b2a74b
```

Erwartetes Resultat:

```text
201 Created
chunk_project_id vorhanden
chunk_universe_id vorhanden
chunk_world_id = world_spawn
```

---

### 9.3 App-seitig gespeicherte Chunk-Referenzen

Im App-Projekt werden Referenzen gepflegt wie:

```text
chunk_project_id
chunk_universe_id
chunk_world_id
```

Zusätzlich können Service-Links gesetzt werden:

```text
ProjectServiceLink(service="chunk", resource_type="chunk_project", ...)
ProjectServiceLink(service="chunk", resource_type="chunk_universe", ...)
ProjectServiceLink(service="chunk", resource_type="chunk_world", ...)
```

Wichtig:

```text
vectoplan-app speichert nicht die Chunk-Daten selbst.
vectoplan-app speichert nur IDs und Links.
```

---

### 9.4 Aktuell getesteter Flow

```text
vectoplan-app
  ↓
PUT /projects/by-app/<app_project_public_id>
  ↓
vectoplan-chunk
  ↓
201 Created
  ↓
App-Projekt enthält Chunk-Referenzen
  ↓
vectoplan-editor
  ↓
POST /projects/<chunk_project_id>/worlds/world_spawn/chunks/batch
  ↓
200 OK
```

Damit ist der primäre App↔Chunk↔Editor-Flow funktionsfähig.

---

## 10. Aktuelle Datenmodell-Architektur

Die alte flache Model-Struktur wurde modularisiert.

Früher:

```text
services/vectoplan-app/models.py
```

Zwischenstand:

```text
services/vectoplan-app/models/core.py
```

Jetzt:

```text
services/vectoplan-app/models/
  __init__.py
  base.py
  users.py
  legacy.py
  projects.py
  project_access.py
  project_embed.py
  project_links.py
  project_versions.py
  project_audit.py
  core.py
```

---

### 10.1 `models/base.py`

Zweck:

```text
gemeinsame DB-/Helper-Basis
```

Enthält:

```text
db
JSON/JSONB-Typ
TimestampMixin
SoftDeleteMixin
SerializationMixin
safe_* Helper
ID-Generatoren
Status-/Visibility-/Role-Normalisierung
Viewer-State-Sanitizer
```

Beispiele:

```python
safe_str(value, default="", max_len=240)
safe_int(value, default=0)
safe_bool(value, default=False)
project_public_id()
version_public_id()
utcnow()
```

---

### 10.2 `models/users.py`

Zweck:

```text
App-lokaler User-Platzhalter und spätere Auth-Anbindung
```

Zentrales Model:

```text
AppUser
```

Aktueller Standard-User:

```text
id = 1
public_id = u_demo_1
handle = demo
display_name = Demo User
role = admin
is_placeholder = true
is_system = true
```

Wichtige Helper:

```text
ensure_default_user()
current_user_id_placeholder()
serialize_user()
get_user_model_status()
```

---

### 10.3 `models/legacy.py`

Zweck:

```text
nicht-projektbezogene Basis-/Altmodelle, die noch technisch gebraucht werden
```

Models:

```text
Client
IdempotencyKey
Job
Blob
Conversation
MessageTemplate
ConversationState
```

Wichtig:

`Conversation` ist nicht mehr sichtbarer Chat-UI-Treiber, bleibt aber als technischer Container für Historie, State oder Kompatibilität nutzbar.

Aktueller wichtiger Fix:

```text
ConversationState ist wieder kompatibel mit älteren Route-Erwartungen.
```

Neu bzw. repariert:

```text
ConversationState.state_json
ConversationState.get_or_create(...)
ConversationState.merge_patch(...)
ConversationState.replace_state(...)
merge_conversation_state_patch(...)
replace_conversation_state(...)
```

Damit funktioniert:

```text
routes/viewer_selection.py
```

wieder ohne `AttributeError`.

---

### 10.4 `models/projects.py`

Zweck:

```text
zentrales App-Projektmodell
```

Model:

```text
Project
```

Speichert:

```text
Projektname
Beschreibung
Adresse
Koordinaten
Owner
Sichtbarkeit
Setup-Status
Service-Referenzen
Artefakt-Referenzen
Lifecycle
```

Speichert nicht:

```text
3D-Welt
Chunk-Inhalte
CAD-Geometrie
LV-Positionen
Map-Features
```

Wichtige Felder:

```text
id
public_id
owner_user_id
conversation_id
name
description
address_text
street
house_number
postal_code
city
region
country
latitude
longitude
coordinate_srid
chunk_project_id
chunk_universe_id
chunk_world_id
plan2d_id
lv_id
service_refs
artifact_refs
visibility
is_public
setup_status
setup_completed_at
status
settings
metadata_json
```

Beispiel-Projekt:

```json
{
  "public_id": "prj_979eb0a4d8894086a5b2a74b",
  "name": "Testprojekt",
  "description": "Test",
  "address_text": "Innenried 31",
  "street": "Innenried",
  "house_number": "31",
  "city": "Beispielstadt",
  "country": "DE",
  "setup_status": "configured",
  "is_configured": true,
  "chunk_project_id": "chk_prj_prj_979eb0a4d8894086a5b2a74b_2653d3872366",
  "chunk_universe_id": "dev-universe",
  "chunk_world_id": "world_spawn"
}
```

---

### 10.5 `models/project_access.py`

Zweck:

```text
Projekt-Mitgliedschaften und Rechte
```

Model:

```text
ProjectMembership
```

Rollen:

```text
owner
admin
editor
viewer
```

Rechte:

```text
view
edit
manage
delete
transfer
embed
```

Wichtige Felder:

```text
project_id
user_id
role
can_view
can_edit
can_manage
can_delete
can_transfer
can_embed
status
invited_by_user_id
accepted_at
revoked_at
metadata_json
```

Beispiel:

```json
{
  "project_id": 1,
  "user_id": 1,
  "role": "owner",
  "permissions": {
    "view": true,
    "edit": true,
    "manage": true,
    "delete": true,
    "transfer": true,
    "embed": true
  }
}
```

---

### 10.6 `models/project_embed.py`

Zweck:

```text
Embed-/iframe-Policy pro Projekt
```

Model:

```text
ProjectEmbedPolicy
```

Speichert:

```text
ob Embedding erlaubt ist
welche Modi erlaubt sind
ob Auth/Token nötig ist
welche Origins erlaubt sind
welche Workspace-Bereiche eingebettet werden dürfen
```

Beispiel:

```json
{
  "enabled": true,
  "allow_iframe": true,
  "mode": "spectator",
  "allowed_modes": ["spectator", "readonly"],
  "allow_map": true,
  "allow_editor3d": true,
  "allow_2d": true,
  "allow_lv": true,
  "require_auth": true,
  "require_project_permission": true
}
```

---

### 10.7 `models/project_links.py`

Zweck:

```text
Referenzen von App-Projekten auf Microservice-Ressourcen
```

Model:

```text
ProjectServiceLink
```

Speichert nur Referenzen, nicht die Daten selbst.

Beispiele:

```text
chunk project
chunk universe
chunk world
2D plan
LV resource
OpenLayer dataset
file/blob
version artifact
```

Beispiel:

```json
{
  "project_id": 1,
  "service": "chunk",
  "resource_type": "chunk_world",
  "resource_id": "world_spawn",
  "external_project_id": "chk_prj_prj_979eb0a4d8894086a5b2a74b_2653d3872366",
  "is_primary": true,
  "status": "active"
}
```

---

### 10.8 `models/project_versions.py`

Zweck:

```text
zentrale Version-/Snapshot-/Artefakt-Referenzen pro Projekt
```

Model:

```text
ProjectVersion
```

Speichert:

```text
Version-ID
Projektbezug
Typ
Status
Label
Beschreibung
Service-Referenzen
Artefakt-Referenzen
Metriken
Payload-Referenzen
```

Beispiel:

```json
{
  "version_id": "ver_abc",
  "project_id": 1,
  "version_no": 3,
  "kind": "metadata",
  "status": "stored",
  "label": "Projektstand 3",
  "artifact_refs": {
    "project_metadata": true
  }
}
```

---

### 10.9 `models/project_audit.py`

Zweck:

```text
Audit-Events für Projektaktionen
```

Model:

```text
ProjectAuditEvent
```

Beispiele für Actions:

```text
created
updated
deleted
archived
transferred
permission_changed
embed_changed
linked
version_created
chunk_provisioned
error
```

Beispiel:

```json
{
  "event_id": "aud_123",
  "project_id": 1,
  "category": "project",
  "action": "updated",
  "actor_user_id": 1,
  "message": "Project metadata updated"
}
```

---

### 10.10 `models/core.py`

Zweck:

```text
Kompatibilitäts-Export
```

Wichtig:

```text
core.py definiert keine eigenen db.Model-Klassen mehr.
core.py re-exportiert die modularen Models.
```

Damit bleiben Imports möglich wie:

```python
from models.core import Project
from models.core import Conversation
```

---

### 10.11 `models/__init__.py`

Zweck:

```text
zentraler Import-Hub
```

Exportiert:

```text
AppUser
Client
IdempotencyKey
Job
Blob
Conversation
MessageTemplate
ConversationState
Project
ProjectMembership
ProjectEmbedPolicy
ProjectServiceLink
ProjectVersion
ProjectAuditEvent
```

Beispiel:

```python
from models import Project, ProjectMembership, ProjectVersion
```

---

## 11. Aktuelle Service-Schicht

```text
services/vectoplan-app/services/
  current_user.py
  project_permissions.py
  project_service.py
  chunk_client.py
```

---

### 11.1 `services/current_user.py`

Zweck:

```text
zentraler Platzhalter für aktuellen User
```

Aktuell:

```text
immer User id=1
kein Login
keine Session-Pflicht
keine externe Auth
```

Wichtige Funktionen:

```python
get_current_user_id()
get_current_user_id_from_g_or_default()
ensure_default_user()
get_current_user()
require_current_user()
get_current_user_context()
serialize_current_user()
```

Beispiel-Kontext:

```json
{
  "user_id": 1,
  "id": 1,
  "public_id": "u_demo_1",
  "handle": "demo",
  "display_name": "Demo User",
  "role": "admin",
  "is_placeholder": true,
  "source": "db"
}
```

---

### 11.2 `services/project_permissions.py`

Zweck:

```text
zentrale Rechteprüfung
```

Rollen:

```text
owner
admin
editor
viewer
```

Berechtigungen:

```text
view
edit
manage
delete
transfer
embed
```

Wichtige Funktionen:

```python
get_project_permission_result(project, user_id)
require_project_permission(project, "edit", user_id)
can_view_project(project, user_id)
can_edit_project(project, user_id)
can_manage_project(project, user_id)
can_delete_project(project, user_id)
can_transfer_project(project, user_id)
can_embed_project(project, user_id)
ensure_owner_membership(project_id=..., owner_user_id=...)
grant_project_role(project, user_id=..., role=...)
revoke_project_membership(project, user_id=...)
transfer_project_ownership(project, new_owner_user_id=...)
```

Beispiel:

```python
require_project_permission(project, "manage", user_id=1)
```

Wenn die Berechtigung fehlt:

```json
{
  "ok": false,
  "code": "project_permission_denied",
  "permission": "manage"
}
```

---

### 11.3 `services/project_service.py`

Zweck:

```text
fachliche Projektlogik
```

Verantwortlich für:

```text
Projekt erstellen
Projekt aktualisieren
Projekt löschen/archivieren
Projekt übertragen
Projekt serialisieren
Projektliste
Sidebar-Items
Conversation technisch erzeugen
Owner-Membership erzeugen
Embed-Policy erzeugen
Service-Links verwalten
Version-Links verwalten
Audit-Events schreiben
Chunk-Referenzen speichern/aktualisieren
```

Wichtige Funktionen:

```python
create_project(data, user_id=1)
update_project(project, data, user_id=1)
create_or_update_project(...)
delete_project(project, user_id=1)
archive_project(project, user_id=1)
transfer_project_owner(project, new_owner_user_id=...)
list_projects_for_user(user_id=1)
list_project_sidebar_items(user_id=1)
serialize_project(project)
serialize_project_sidebar_item(project)
get_or_create_project_conversation(project)
get_or_create_embed_policy(project)
upsert_project_service_link(...)
create_project_version_link(...)
update_project_embed_policy(...)
```

---

### 11.4 `services/chunk_client.py`

Zweck:

```text
serverseitige Kommunikation von vectoplan-app zu vectoplan-chunk
```

Verantwortlich für:

```text
Chunk-Service-Status abfragen
Chunk-Projekt für App-Projekt sicherstellen
Provisioning-Antwort normalisieren
chunk_project_id extrahieren
chunk_universe_id extrahieren
chunk_world_id extrahieren
Fehler kontrolliert melden
Timeouts/HTTP-Fehler abfangen
```

Kommuniziert über:

```text
VECTOPLAN_CHUNK_INTERNAL_URL=http://vectoplan-chunk:5000
```

Nicht über:

```text
localhost
Browser-Public-URL
```

Primäre Chunk-Route:

```text
PUT /projects/by-app/<app_project_public_id>
```

---

## 12. Aktuelle Route-Struktur

```text
services/vectoplan-app/routes/
  projects_api.py
  viewer_selection.py

  ui/
    projects.py
    chat.py
    editor.py
    map.py
    viewer2d.py
```

Zusätzlich können ältere/weitere Routen noch im Codebestand existieren, sind aber nicht mehr der primäre projektgeführte Zielpfad.

---

### 12.1 `routes/projects_api.py`

Zweck:

```text
JSON-API für Projektverwaltung
```

Wichtige Routen:

```text
GET    /v1/projects/_status
GET    /v1/projects/current-user
GET    /v1/projects
POST   /v1/projects
GET    /v1/projects/sidebar
GET    /v1/projects/<project_id>
PATCH  /v1/projects/<project_id>
PUT    /v1/projects/<project_id>
DELETE /v1/projects/<project_id>
GET    /v1/projects/<project_id>/access
GET    /v1/projects/<project_id>/members
PUT    /v1/projects/<project_id>/members/<user_id>
PATCH  /v1/projects/<project_id>/members/<user_id>
DELETE /v1/projects/<project_id>/members/<user_id>
POST   /v1/projects/<project_id>/transfer
GET    /v1/projects/<project_id>/versions
POST   /v1/projects/<project_id>/versions
GET    /v1/projects/<project_id>/service-links
POST   /v1/projects/<project_id>/service-links
GET    /v1/projects/<project_id>/embed-policy
PUT    /v1/projects/<project_id>/embed-policy
PATCH  /v1/projects/<project_id>/embed-policy
GET    /v1/projects/<project_id>/sidebar-item
```

---

### 12.2 `routes/ui/projects.py`

Zweck:

```text
projektgeführte UI-Shell
```

Wichtige Routen:

```text
GET /                       → Projekt-Shell, neues Projekt
GET /project=new            → Projekt-Shell, neues Projekt
GET /project=<project_id>   → Projekt-Shell, bestehendes Projekt
GET /project/<project_id>   → Redirect auf /project=<project_id>
GET /projects               → Projekt-Shell
GET /ui/project/new         → Projektformular im iframe
GET /ui/project/<id>/project
GET /ui/project/<id>/admin
GET /ui/project/<id>/lv
GET /ui/project/<id>/context.json
GET /ui/projects/sidebar.json
```

---

### 12.3 `routes/ui/editor.py`

Zweck:

```text
3D-Editor-Gateway
```

Wichtige Routen:

```text
GET /ui/project/<project_id>/editor
GET /ui/chat/<chat_id>/editor
GET /ui/editor
```

Regel:

```text
Browser bekommt nur VECTOPLAN_EDITOR_PUBLIC_URL
```

Nie:

```text
http://vectoplan-editor:5000
```

---

### 12.4 `routes/ui/map.py`

Zweck:

```text
Map-Gateway
```

Wichtige Routen:

```text
GET /ui/project/<project_id>/map
GET /ui/chat/<chat_id>/map
GET /ui/map
GET /ui/project/<project_id>/map.json
GET /ui/chat/<chat_id>/map.json
```

Regel:

```text
Browser bekommt nur OPENLAYER_PUBLIC_URL
```

Nie:

```text
http://openlayer:8090
```

---

### 12.5 `routes/ui/viewer2d.py`

Zweck:

```text
2D-/CAD-Gateway
```

Wichtige Routen:

```text
GET /ui/project/<project_id>/cad2d
GET /ui/chat/<chat_id>/cad2d
GET /ui/project/<project_id>/plan2d.json
GET /ui/chat/<chat_id>/plan2d.json
GET /ui/project/<project_id>/cad-embed.json
GET /ui/chat/<chat_id>/cad-embed.json
```

---

### 12.6 `routes/ui/chat.py`

Zweck im neuen Stand:

```text
Legacy-/Kompatibilitätsroute
```

Nicht mehr primärer Zielpfad.

Kann noch genutzt werden für:

```text
alte /ui/chat-3d Redirects
bestehende Chat-IDs
Legacy-Shell-Kompatibilität
Upload-/Version-Kompatibilität
```

Ziel später:

```text
entweder entfernen
oder hart auf Projekt-Shell redirecten
oder hinter Feature-Flag legen
```

---

### 12.7 `routes/viewer_selection.py`

Zweck:

```text
technischer Workspace-/Viewer-State-Kompatibilitätspfad
```

Route:

```text
/v1/chats/<chat_id>/viewer/selection
```

Trotz Name speichert diese Route keinen alten 3D-/Speckle-Backend-State mehr, sondern neutralen Workspace-State:

```text
mode
workspace_mode
viewer_selection
workspace_selection
last_2d_selection
last_editor_selection
last_map_selection
last_workspace_error
```

Aktueller Fix:

```text
Die Route funktioniert wieder, weil ConversationState nun get_or_create und merge_patch besitzt.
```

---

## 13. Aktuelle Template-Struktur

```text
services/vectoplan-app/templates/
  chat_viewer.html

  partials/
    project_sidebar.html

  viewer/
    project.html
```

Weitere alte Templates können noch existieren:

```text
chat.html
viewer/admin.html
viewer/cad2d.html
viewer/lv.html
viewer/map.html
```

---

### 13.1 `templates/chat_viewer.html`

Trotz Name ist diese Datei jetzt die Projekt-/Workspace-Shell.

Zweck:

```text
Hauptlayout der App
Projekt-Sidebar einbinden
Workspace-Toolbar rendern
iframe rendern
APP_CONFIG erzeugen
```

Aktuelles Layout:

```text
Projektliste | Workspace
```

Nicht mehr enthalten:

```text
sichtbarer Chat
VectoAI-Header
Chat-Close-Button
Composer
Attach-Button
Send-Button
Disclaimer
Chat-Öffnen-Button neben Projekt
```

Wichtige globale Config:

```js
window.APP_CONFIG = {
  chatUiEnabled: false,
  project: currentProject,
  projectPublicId: "...",
  projectConfigured: true,
  workspacePaths: {
    projectPagePath: "...",
    editorPagePath: "...",
    mapPagePath: "...",
    cad2dPagePath: "...",
    lvPagePath: "..."
  }
}
```

---

### 13.2 `templates/partials/project_sidebar.html`

Zweck:

```text
linke Projektliste
aktuelles Projekt
Projekt suchen
neues Projekt
Projektliste
Sidebar-Footer
```

Wichtige Funktionen:

```text
zeigt aktuelle Projektkarte
lädt Items aus /v1/projects/sidebar
markiert aktives Projekt
navigiert auf /project=<public_id>
```

---

### 13.3 `templates/viewer/project.html`

Zweck:

```text
Projektformular im Workspace-iframe
```

Enthält Felder für:

```text
Projektname
Beschreibung
Adresse
Straße
Hausnummer
PLZ
Ort
Region
Land
Latitude
Longitude
SRID
Sichtbarkeit
Systemreferenzen
```

Beim Speichern:

```text
POST /v1/projects
PATCH /v1/projects/<public_id>
```

Danach wird der Parent aktualisiert.

---

## 14. Aktuelle Static-Struktur

```text
services/vectoplan-app/static/
  css/
    chat.css
    project_sidebar.css
    project_workspace.css
    cards.css

  js/
    chat/
      main.js
      project_sidebar_data.js
      project_sidebar_resize.js
      project_sidebar.js

    project/
      project_form.js
```

---

### 14.1 `static/css/chat.css`

Zweck:

```text
Layout und Theme der Projekt-/Workspace-Shell
```

Aktuell:

```text
kein sichtbares Chat-Layout mehr
Grid: Projektliste | Workspace
Toolbar-Styling
iframe-Styling
Versionen-Dropdown
Gating-Zustände
Light/Dark Theme
```

Explizit deaktiviert:

```text
.chat-wrap
.chat-backdrop
#composer
#message
#attachBtn
#sendBtn
#chatToggleBtn
#msgs
```

---

### 14.2 `static/css/project_sidebar.css`

Zweck:

```text
Styling der linken Projekt-Sidebar
```

Steuert:

```text
Breite
Collapsed-Zustand
Projektkarten
Aktive Markierung
Suchfeld
Neues-Projekt-Button
Resize
Footer
Mobile-Verhalten
```

---

### 14.3 `static/css/project_workspace.css`

Zweck:

```text
Styling des Projektformulars im iframe
```

Steuert:

```text
Form-Layout
Sektionen
Input-Felder
Sticky Save-Bar
Status-Chip
Projekt-Konfigurationszustand
```

---

### 14.4 `static/js/chat/main.js`

Trotz Pfadname ist diese Datei jetzt der Workspace-Orchestrator.

Zweck:

```text
Workspace-Modi steuern
iframe wechseln
Projekt-Gating anwenden
Projekt-Sidebar initialisieren
Theme toggeln
Versionen-Dropdown steuern
Projekt-Speicher-Events verarbeiten
Debug-API bereitstellen
```

Nicht mehr enthalten:

```text
Chat-Imports
Composer
Transcript
Attach
Send
Chat-Drawer
Chat-Hotkeys
Chat-Refresh
```

Wichtiger Debug-Hook:

```js
window.__VECTOPLAN_WORKSPACE_DEBUG__
```

Beispiele:

```js
window.__VECTOPLAN_WORKSPACE_DEBUG__.project.current()
window.__VECTOPLAN_WORKSPACE_DEBUG__.project.configured()
window.__VECTOPLAN_WORKSPACE_DEBUG__.setWorkspaceMode("map")
```

---

### 14.5 `static/js/chat/project_sidebar_data.js`

Zweck:

```text
Datenquelle der Projekt-Sidebar
```

Primär:

```text
GET /v1/projects/sidebar
```

Fallback:

```text
lokaler Cache/localStorage
```

---

### 14.6 `static/js/chat/project_sidebar_resize.js`

Zweck:

```text
Resize-Logik der Projekt-Sidebar
```

Steuert:

```text
Maus-/Touch-Resize
min/max width
collapsed width
localStorage-Persistenz
Resize-Events an Shell
```

---

### 14.7 `static/js/chat/project_sidebar.js`

Zweck:

```text
Controller der Projekt-Sidebar
```

Steuert:

```text
Initialisierung
Item-Rendering
Suche
Aktives Projekt
Neues Projekt
Refresh
Collapse
Navigation
```

---

### 14.8 `static/js/project/project_form.js`

Zweck:

```text
Projektformular-Controller
```

Steuert:

```text
Form lesen
Payload bauen
POST /v1/projects
PATCH /v1/projects/<public_id>
Parent-Events senden
Parent-Redirect auf /project=<public_id>
Dirty-State
Fehleranzeige
```

Wichtige Events:

```text
vectoplan:project:saved
vectoplan:project:created
vectoplan:project:updated
vectoplan:project:configured
vectoplan:project:dirty
vectoplan:project:error
```

---

## 15. Detaillierte aktuelle Ordner-/Filestruktur

```text
services/vectoplan-app/
  app.py
  config.py
  extensions.py
  auth.py
  versioning.py
  seed_templates.py

  models/
    __init__.py
    base.py
    users.py
    legacy.py
    projects.py
    project_access.py
    project_embed.py
    project_links.py
    project_versions.py
    project_audit.py
    core.py

  services/
    current_user.py
    project_permissions.py
    project_service.py
    chunk_client.py

  routes/
    projects_api.py

    ui/
      projects.py
      chat.py
      editor.py
      map.py
      viewer2d.py
      crawlab.py
      superset.py

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
    chat_viewer.html

    partials/
      project_sidebar.html

    viewer/
      project.html
      admin.html
      cad2d.html
      lv.html
      map.html

  static/
    css/
      chat.css
      project_sidebar.css
      project_workspace.css
      cards.css

    js/
      chat/
        main.js
        project_sidebar_data.js
        project_sidebar_resize.js
        project_sidebar.js

      project/
        project_form.js
```

Hinweis:

Einige ältere Dateien können noch vorhanden sein, obwohl sie nicht mehr zum neuen primären Projektfluss gehören. Sie werden später geprüft, deaktiviert oder entfernt.

---

## 16. Wichtigste Endpoints – Teilübersicht

### 16.1 UI

```text
GET /
GET /project=new
GET /project=<project_public_id>
GET /ui/project/new
GET /ui/project/<project_public_id>/project
GET /ui/project/<project_public_id>/editor
GET /ui/project/<project_public_id>/map
GET /ui/project/<project_public_id>/cad2d
GET /ui/project/<project_public_id>/lv
GET /ui/project/<project_public_id>/admin
GET /ui/project/<project_public_id>/context.json
GET /ui/projects/sidebar.json
```

---

### 16.2 API

```text
GET    /v1/projects/_status
GET    /v1/projects/current-user
GET    /v1/projects
POST   /v1/projects
GET    /v1/projects/sidebar
GET    /v1/projects/<project_id>
PATCH  /v1/projects/<project_id>
PUT    /v1/projects/<project_id>
DELETE /v1/projects/<project_id>
GET    /v1/projects/<project_id>/access
GET    /v1/projects/<project_id>/members
PUT    /v1/projects/<project_id>/members/<user_id>
DELETE /v1/projects/<project_id>/members/<user_id>
POST   /v1/projects/<project_id>/transfer
GET    /v1/projects/<project_id>/versions
POST   /v1/projects/<project_id>/versions
GET    /v1/projects/<project_id>/service-links
POST   /v1/projects/<project_id>/service-links
GET    /v1/projects/<project_id>/embed-policy
PUT    /v1/projects/<project_id>/embed-policy
GET    /v1/projects/<project_id>/sidebar-item
```

---

### 16.3 Technischer State-Kompatibilitätspfad

```text
GET   /v1/chats/<chat_id>/viewer/selection
HEAD  /v1/chats/<chat_id>/viewer/selection
PUT   /v1/chats/<chat_id>/viewer/selection
PATCH /v1/chats/<chat_id>/viewer/selection
```

Aktuelle Rolle:

```text
neutraler Workspace-State
kein sichtbarer Chat-Fokus
kein Speckle-State
kein alter Viewer-Backend-State
```

---

## 17. Beispiel: Projekt erstellen

Request:

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

Erwartetes Ergebnis:

```json
{
  "ok": true,
  "code": "project_created",
  "project": {
    "public_id": "prj_...",
    "name": "Testprojekt",
    "setup_status": "configured",
    "is_configured": true,
    "chunk_project_id": "chk_prj_...",
    "chunk_universe_id": "dev-universe",
    "chunk_world_id": "world_spawn"
  },
  "redirect_url": "/project=prj_..."
}
```

---

## 18. Beispiel: Projekt bearbeiten

Request:

```bash
curl -X PATCH http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Testprojekt aktualisiert",
    "description": "Neue Beschreibung",
    "address_text": "Innenried 31",
    "city": "Beispielstadt"
  }'
```

Erwartetes Ergebnis:

```json
{
  "ok": true,
  "code": "project_updated",
  "project": {
    "public_id": "prj_979eb0a4d8894086a5b2a74b",
    "name": "Testprojekt aktualisiert",
    "setup_status": "configured",
    "is_configured": true
  }
}
```

---

Fortsetzung in Teil 2:

```text
19. Sidebar laden
20. Service-Link setzen
21. Version anlegen
22. Embed-Policy
23. Frontend-Eventfluss
24. Workspace-Flows
25. Datenbank-/Runtime-Hinweise
26. Bekannte Altlasten
27. Was behalten werden sollte
28. Was später umbenannt werden sollte
29. Was später entfernt/deaktiviert werden sollte
30. Offene Punkte
31. Gesamtfazit
32. Ordner-/Architekturübersicht
```
## 19. Beispiel: Sidebar laden

Request:

```bash
curl http://localhost:5103/v1/projects/sidebar
```

Beispielantwort:

```json
{
  "ok": true,
  "items": [
    {
      "id": "prj_979eb0a4d8894086a5b2a74b",
      "projectId": "prj_979eb0a4d8894086a5b2a74b",
      "public_id": "prj_979eb0a4d8894086a5b2a74b",
      "title": "Testprojekt",
      "subtitle": "Innenried 31",
      "href": "/project=prj_979eb0a4d8894086a5b2a74b",
      "isConfigured": true
    }
  ],
  "total": 1
}
```

Die Sidebar ist die primäre Navigation der App. Sie lädt ihre Daten serverseitig aus der Projekt-API, nicht aus dem Chunk-Service.

---

## 20. Beispiel: Service-Link setzen

Ein Projekt mit einer Chunk-Ressource verknüpfen:

```bash
curl -X POST http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b/service-links \
  -H "Content-Type: application/json" \
  -d '{
    "service": "chunk",
    "resource_type": "chunk_world",
    "resource_id": "world_spawn",
    "external_project_id": "chk_prj_prj_979eb0a4d8894086a5b2a74b_2653d3872366",
    "status": "active",
    "metadata": {
      "source": "chunk-provisioning",
      "chunkProjectId": "chk_prj_prj_979eb0a4d8894086a5b2a74b_2653d3872366",
      "chunkUniverseId": "dev-universe",
      "chunkWorldId": "world_spawn",
      "templateId": "flat",
      "providerWorldId": "flat"
    }
  }'
```

Wichtig:

```text
resource_id = world_spawn
templateId = flat
providerWorldId = flat
```

`flat` darf nicht als konkrete App-/Chunk-Welt gespeichert werden.

---

## 21. Beispiel: Version anlegen

```bash
curl -X POST http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b/versions \
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

Aktueller Stand:

```text
ProjectVersion existiert als zentrale App-Tabelle.
Alte transcript-basierte Versionierung ist noch nicht vollständig bereinigt.
Service-Artefakte sollen später konsequent als Referenzen gespeichert werden.
```

---

## 22. Beispiel: Embed-Policy lesen

```bash
curl http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b/embed-policy
```

Die Embed-Policy beschreibt, ob und wie ein Projekt in iframes oder externe Kontexte eingebettet werden darf.

Beispiel:

```json
{
  "ok": true,
  "policy": {
    "enabled": true,
    "allow_iframe": true,
    "allow_map": true,
    "allow_editor3d": true,
    "allow_2d": true,
    "allow_lv": true,
    "require_auth": true,
    "require_project_permission": true
  }
}
```

---

## 23. Beispiel: Workspace-State speichern

Die App hat weiterhin technische State-Routen für Workspace-/Viewer-Auswahl.

Beispiel:

```bash
curl -X PUT http://localhost:5103/v1/chats/<chat_id>/viewer/selection \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "editor"
  }'
```

Die Route speichert neutralen UI-State:

```json
{
  "viewer_selection": {
    "ok": true,
    "mode": "editor",
    "workspace_mode": "3d",
    "legacy_3d_backend": false,
    "legacy_speckle": false
  },
  "workspace_selection": {
    "ok": true,
    "mode": "editor",
    "workspace_mode": "3d",
    "legacy_3d_backend": false,
    "legacy_speckle": false
  },
  "workspace_mode": "3d",
  "mode": "editor"
}
```

Dieser Pfad war zuletzt defekt, weil `ConversationState.merge_patch()` und `ConversationState.get_or_create()` fehlten.

Reparierter Stand:

```text
ConversationState.get_or_create(...) existiert.
ConversationState.merge_patch(...) existiert.
ConversationState.state_json ist Alias auf state.
viewer_selection.py kann ohne Änderung weiter funktionieren.
```

---

## 24. Frontend-Eventfluss nach Projektspeicherung

Wenn das Projektformular speichert, sendet `project_form.js` ein Event an den Parent:

```js
window.parent.dispatchEvent(new CustomEvent("vectoplan:project:saved", {
  detail: {
    project: savedProject,
    sidebar_item: sidebarItem,
    redirect_url: "/project=prj_..."
  }
}));
```

`static/js/chat/main.js` reagiert darauf:

```text
Projekt in APP_CONFIG aktualisieren
Root data-Attribute aktualisieren
Workspace-Gating neu berechnen
Projekt-Sidebar refreshen
Versionen aktualisieren
ggf. Parent auf /project=<project_public_id> navigieren
```

Der sichtbare Chat ist daran nicht beteiligt.

---

## 25. Aktueller UI-Zielzustand

Aktuelle Shell:

```text
┌────────────────────────┬────────────────────────────────────────────┐
│ Projekt-Sidebar        │ Workspace                                  │
│                        │                                            │
│ VECTOPLAN              │ Toolbar: Projekt | Map | 3D | 2D | LV ...  │
│ Aktuelles Projekt      │                                            │
│ Neues Projekt          │ iframe: /ui/project/...                    │
│ Projekt suchen         │                                            │
│ Projektliste           │                                            │
└────────────────────────┴────────────────────────────────────────────┘
```

Nicht mehr sichtbar:

```text
VectoAI - Gebäudegenerierung & Datenanalyse
Chat-Schließen-X
Nachrichtenliste
Attach-Button
Nachricht eingeben…
Send-Button
Vectoplan kann Fehler machen...
Chat-Öffnen-Icon neben Projekt
```

---

## 26. Aktueller Datenbank- und Runtime-Hinweis

Da sich das Datenmodell stark geändert hat, gibt es weiterhin keine harte Pflicht zur Abwärtskompatibilität mit alten lokalen Entwicklungsdatenbanken.

Wichtig:

```text
db.create_all() ergänzt keine fehlenden Spalten in bestehenden Tabellen.
```

Wenn Models geändert wurden und lokale Postgres-Tabellen noch alt sind:

```bash
docker compose down -v
docker compose up --build
```

Typische alte Schemafehler:

```text
column app_users.handle does not exist
column conversations.owner_user_id does not exist
address_street is an invalid keyword argument for Project
chunk_universe_id column missing
ConversationState has no attribute merge_patch
ConversationState has no attribute get_or_create
```

Aktueller Stand nach letzter Reparatur:

```text
ConversationState-Kompatibilitätsfehler ist behoben.
Chunk-Service läuft read-only in Runtime.
Chunk-Seed/Schema sind ready.
App kann Chunk-Projekt für App-Projekt erzeugen.
Editor kann world_spawn laden.
```

---

## 27. Bekannte technische Altlasten

### 27.1 Dateinamen mit altem Chat-Begriff

Einige neue Shell-Dateien liegen noch unter `chat/`, obwohl der sichtbare Chat entfernt wurde:

```text
templates/chat_viewer.html
static/js/chat/main.js
static/css/chat.css
static/js/chat/project_sidebar*.js
```

Aktuell bewusst akzeptiert, um Umbauaufwand zu reduzieren.

Später möglich:

```text
templates/app_shell.html
static/js/shell/main.js
static/css/shell.css
static/js/project_sidebar/
```

---

### 27.2 Conversation bleibt technisch vorhanden

`Conversation` bleibt im Datenmodell, obwohl der sichtbare Chat entfernt wurde.

Aktuelle Gründe:

```text
technischer State-Kontext
Kompatibilität zu alten Workspace-State-Routen
Version-/Upload-Historie in Altpfaden
mögliche spätere Projektkommunikation
```

Später prüfen:

```text
Conversation vollständig entfernen
oder als unsichtbare Projekt-Historie behalten
oder nur noch für technische Workspace-State-Kompatibilität nutzen
```

---

### 27.3 ConversationState ist jetzt Kompatibilitätsschicht

`ConversationState` ist aktuell wichtig für alte und neutrale UI-State-Routen.

Aktueller Zustand:

```text
ConversationState.state      = echte DB-Spalte
ConversationState.state_json = Kompatibilitätsalias
ConversationState.selection  = optionale Auswahl-/Selection-Spalte
```

Neue/ergänzte API:

```text
ConversationState.get_or_create(...)
ConversationState.merge_patch(...)
ConversationState.replace_state(...)
```

Damit bleiben ältere Routen stabil, ohne überall auf freie Helper-Funktionen umgebaut werden zu müssen.

---

### 27.4 `state_api_bp unavailable`

Beim App-Start kann weiterhin erscheinen:

```text
blueprint state_api_bp unavailable; skipped
```

Einordnung:

```text
kein aktueller Blocker
nicht derselbe Fehler wie ConversationState.merge_patch
App startet trotzdem
Gunicorn-Worker booten
Projekt-/Chunk-/Editor-Flow funktioniert
```

Später prüfen:

```text
alte state_api_bp-Registrierung entfernen
oder Blueprint korrekt hinter Feature-Flag legen
oder vorhandene State-Routen konsolidieren
```

---

### 27.5 Alte Chat-Routen existieren teilweise noch

Diese können noch vorhanden sein:

```text
routes/ui/chat.py
routes/chat/
routes/state.py
routes/viewer_selection.py
```

Sie sind nicht mehr primärer UI-Zielpfad.

---

### 27.6 Speckle-/Altviewer-Altlasten

Noch zu prüfen:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
versioning.py
seed_templates.py
routes/chat/helpers.py
routes/chat/sync.py
routes/chat/stream.py
```

Ziel:

```text
Speckle-Altlasten entfernen oder hart deaktivieren
keine Speckle-Karten
kein Auto-Publish
keine alten Viewer-URLs
```

---

## 28. Was behalten werden sollte

Behalten:

```text
app.py
config.py
extensions.py

models/
  __init__.py
  base.py
  users.py
  legacy.py
  projects.py
  project_access.py
  project_embed.py
  project_links.py
  project_versions.py
  project_audit.py
  core.py

services/
  current_user.py
  project_permissions.py
  project_service.py
  chunk_client.py

routes/
  projects_api.py
  viewer_selection.py

routes/ui/
  projects.py
  editor.py
  map.py
  viewer2d.py

templates/
  chat_viewer.html
  partials/project_sidebar.html
  viewer/project.html

static/css/
  chat.css
  project_sidebar.css
  project_workspace.css

static/js/chat/
  main.js
  project_sidebar_data.js
  project_sidebar_resize.js
  project_sidebar.js

static/js/project/
  project_form.js
```

`viewer_selection.py` bleibt vorerst als technischer State-Kompatibilitätspfad erhalten.

---

## 29. Was später umbenannt werden sollte

Aktuell funktionieren die Dateien, aber die Namen sind historisch:

```text
templates/chat_viewer.html      → templates/app_shell.html
static/css/chat.css             → static/css/app_shell.css
static/js/chat/main.js          → static/js/shell/main.js
static/js/chat/project_sidebar* → static/js/project_sidebar/*
```

Nicht sofort nötig, aber für Lesbarkeit sinnvoll.

Wichtig:

```text
Erst umbenennen, wenn Projektfluss, Chunk-Provisioning und Editor-Embed stabil sind.
Keine gleichzeitige Umbenennung während Fehleranalyse.
```

---

## 30. Was später entfernt oder deaktiviert werden sollte

Prüfen und ggf. entfernen:

```text
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
```

Zusätzlich neutralisieren:

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

Prüfen:

```text
routes/state.py
state_api_bp Registrierung
alte Chat-State-APIs
alte Transcript-Versionierung
```

Nicht entfernen, solange noch UI oder Legacy-Pfade darauf zugreifen.

---

## 31. Aktueller primärer Projektfluss

```text
Browser
  ↓
http://localhost:5103/
  ↓
routes/ui/projects.py
  ↓
chat_viewer.html als App-Shell
  ↓
Projekt-Sidebar + Workspace
  ↓
/ui/project/new oder /ui/project/<project_public_id>/project
  ↓
viewer/project.html
  ↓
project_form.js
  ↓
POST/PATCH /v1/projects
  ↓
project_service.py
  ↓
Project + Conversation + Owner-Membership + Embed-Policy + Audit
  ↓
Chunk-Provisioning / Chunk-Referenz-Sync
  ↓
Sidebar Refresh
  ↓
/project=<project_public_id>
```

---

## 32. Aktueller primärer App↔Chunk-Provisioning-Flow

```text
App-Projekt wird erstellt oder geöffnet
  ↓
vectoplan-app services/chunk_client.py
  ↓
PUT http://vectoplan-chunk:5000/projects/by-app/<app_project_public_id>
  ↓
vectoplan-chunk routes/projects.py
  ↓
Chunk Project wird erstellt oder gefunden
  ↓
Universe wird erstellt oder gefunden
  ↓
WorldInstance world_spawn wird erstellt oder gefunden
  ↓
Response an vectoplan-app
  ↓
vectoplan-app speichert:
    chunk_project_id
    chunk_universe_id
    chunk_world_id = world_spawn
  ↓
Editor kann Chunks laden
```

Getesteter Chunk-Aufruf aus Editor-Kontext:

```text
POST /projects/<chunk_project_id>/worlds/world_spawn/chunks/batch
```

Ergebnis:

```text
HTTP 200
```

---

## 33. Aktueller primärer 3D-Flow

```text
Browser
  ↓
http://localhost:5103/project=<project_public_id>
  ↓
Projekt ist konfiguriert
  ↓
User klickt 3D
  ↓
static/js/chat/main.js
  ↓
iframe src = /ui/project/<project_public_id>/editor
  ↓
routes/ui/editor.py
  ↓
vectoplan-editor Public URL
  ↓
http://localhost:5100/editor?embed=1&project_id=...
  ↓
vectoplan-editor rendert 3D-Editor
  ↓
vectoplan-editor fragt vectoplan-chunk
  ↓
world_spawn / chunks/batch
```

---

## 34. Aktueller primärer Map-Flow

```text
Browser
  ↓
http://localhost:5103/project=<project_public_id>
  ↓
Projekt ist konfiguriert
  ↓
User klickt Map
  ↓
static/js/chat/main.js
  ↓
iframe src = /ui/project/<project_public_id>/map
  ↓
routes/ui/map.py
  ↓
OpenLayer Public URL
  ↓
http://localhost:5190/map?embed=1&project_id=...
```

---

## 35. Aktueller primärer 2D-Flow

```text
Browser
  ↓
http://localhost:5103/project=<project_public_id>
  ↓
Projekt ist konfiguriert
  ↓
User klickt 2D
  ↓
iframe src = /ui/project/<project_public_id>/cad2d
  ↓
routes/ui/viewer2d.py
  ↓
2D-/CAD-Workspace oder locked response
```

---

## 36. Aktueller primärer LV-Flow

```text
Browser
  ↓
http://localhost:5103/project=<project_public_id>
  ↓
Projekt ist konfiguriert
  ↓
User klickt LV
  ↓
iframe src = /ui/project/<project_public_id>/lv
  ↓
routes/ui/projects.py
  ↓
aktuell Platzhalter / später LV-Service
```

---

## 37. Aktueller Workspace-State-Flow

```text
User wechselt Workspace-Modus
  ↓
static/js/chat/main.js
  ↓
optional PUT/PATCH /v1/chats/<chat_id>/viewer/selection
  ↓
routes/viewer_selection.py
  ↓
ConversationState.merge_patch(...)
  ↓
conversation_states.state wird aktualisiert
```

Gespeicherte Werte sind neutral:

```text
mode
workspace_mode
viewer_selection
workspace_selection
last_*_selection
last_*_hover
last_workspace_error
```

Nicht gespeichert werden sollen:

```text
speckle_*
legacy_viewer*
viewer_url
model_id
version_id
chunk snapshot internals
```

---

## 38. Aktuelle Tests / Smoke-Checks

### 38.1 App-Start

Erwartet:

```text
Gunicorn startet.
Worker booten.
Keine ConversationState AttributeError mehr.
```

Noch möglich:

```text
blueprint state_api_bp unavailable; skipped
```

Diese Warnung ist aktuell kein Blocker.

---

### 38.2 Chunk-Status

Erwartet:

```text
GET /projects/_status → 200
GET /chunks/_status   → 200
```

Chunk-Readiness:

```text
schemaReady = true
seedReady = true
defaultProjectReady = true
defaultUniverseReady = true
defaultWorldReady = true
```

---

### 38.3 App↔Chunk-Provisioning

Erwartet:

```text
PUT /projects/by-app/<app_project_public_id> → 201 oder 200
```

Je nach Zustand:

```text
201 = neu erstellt
200 = bereits vorhanden / idempotent zurückgegeben
```

---

### 38.4 Editor↔Chunk

Erwartet:

```text
POST /projects/<chunk_project_id>/worlds/world_spawn/chunks/batch → 200
```

---

### 38.5 Viewer-Selection-State

Erwartet:

```text
PUT /v1/chats/<chat_id>/viewer/selection → 200
```

Nicht mehr erwartet:

```text
ConversationState.merge_patch failed
ConversationState fallback merge failed
AttributeError: ConversationState has no attribute merge_patch
AttributeError: ConversationState has no attribute get_or_create
```

---

## 39. Offene Punkte

### 39.1 `app.py` weiter prüfen

Noch sinnvoll:

```text
Blueprint-Registrierung final prüfen
state_api_bp Warnung entfernen oder sauber hinter Feature-Flag legen
alte Chat-/Speckle-Routen hinter Feature-Flags legen
globale CSP final abstimmen
Dev-Reset-Option prüfen
```

---

### 39.2 Route-Namen vereinheitlichen

Aktuell existieren noch Chat-Begriffe in Pfaden und Dateien.

Später:

```text
chat_viewer.html → app_shell.html
static/js/chat/main.js → static/js/shell/main.js
routes/ui/chat.py → entfernen oder legacy_redirects.py
```

---

### 39.3 Conversation-Abhängigkeit reduzieren

Aktuell existiert `conversation_id` noch in mehreren Kontexten.

Später prüfen:

```text
Projekt-State direkt an Project binden
Chat-State-Routen entfernen
Conversation nur behalten, wenn fachlich benötigt
viewer_selection.py langfristig in workspace_state.py überführen
```

---

### 39.4 Alte Chat-Module entfernen

Prüfen:

```text
routes/chat/
static/js/chat/composer.js
static/js/chat/transcript.js
static/js/chat/layout.js
static/js/chat/uploads.js
```

Wenn sie nicht mehr geladen werden, können sie später entfernt oder archiviert werden.

---

### 39.5 Versionierung finalisieren

Aktuell gibt es `ProjectVersion` als neue zentrale Tabelle.

Noch zu tun:

```text
alte transcript-basierte Versionierung entfernen
Versionen vollständig auf ProjectVersion umstellen
Service-Artefakte sauber referenzieren
Chunk-/2D-/LV-Snapshots als Referenzen einbinden
```

---

### 39.6 Service-Links produktiv anbinden

Aktuell sind `ProjectServiceLink` und Referenzfelder vorbereitet.

Teilweise erreicht:

```text
chunk_project_id automatisch aus Chunk-Service
chunk_universe_id automatisch aus Chunk-Service
chunk_world_id automatisch aus Chunk-Service
```

Noch zu tun:

```text
plan2d_id aus 2D-Service setzen
lv_id aus LV-Service setzen
OpenLayer-Layer/Dataset verknüpfen
Library-Asset-Referenzen verknüpfen
Service-Link-Status regelmäßig validieren
```

---

### 39.7 App-Schema sauber stabilisieren

Bei lokalen DB-Altständen können Schemafehler auftreten.

Noch sinnvoll:

```text
App-DB-Initialisierung prüfen
fehlende App-Spalten erkennen
Entscheidung: Dev-Repair-Script oder weiterhin docker compose down -v
keine stillen Runtime-Mutationen in produktiver Runtime
```

Wichtig:

```text
Der zuvor angedachte schema_contract.py-Pfad wurde nicht umgesetzt.
```

---

## 40. Was aktuell als stabil gilt

Stabil genug für nächsten Entwicklungsstand:

```text
Projekt-Shell
Projekt-Sidebar
Projektformular
Projekt-API
modulare App-Models
Current-User-Platzhalter
Projekt-Berechtigungen
Embed-Policy-Grundlage
ProjectServiceLink-Grundlage
ProjectVersion-Grundlage
ProjectAuditEvent-Grundlage
App↔Chunk-Provisioning-Grundlage
Editor↔Chunk world_spawn Flow
ConversationState-Kompatibilität
```

Nicht final, aber funktionsfähig:

```text
Legacy-State-Routen
alte Chat-Kompatibilität
alte Versionierungsrouten
LV-Platzhalter
2D-Gateway
OpenLayer-Gateway
```

---

## 41. Gesamtfazit

Die `vectoplan-app` ist jetzt deutlich näher am gewünschten Architekturziel.

Vorher:

```text
Chat-Shell mit Workspace
sichtbarer VectoAI-Chat
3D/Map stark aus Chat-Kontext gedacht
Models noch zentral/monolithisch
keine stabile App↔Chunk-Provisioning-Kette
ConversationState API-Mismatch
```

Jetzt:

```text
Projekt-Shell mit Workspace
sichtbarer Chat entfernt
Projektliste links
Workspace rechts
Projekt-API vorhanden
modulare Models vorhanden
Projekt-Berechtigungen vorhanden
Service-Link-/Version-/Audit-Struktur vorhanden
Map/3D/2D/LV projektgeführt
App↔Chunk-Provisioning funktioniert
Chunk world_spawn ist korrekt angebunden
Editor lädt Chunks aus world_spawn
ConversationState-Kompatibilität repariert
```

Der wichtigste aktuelle Einstieg ist:

```text
http://localhost:5103/
```

und für ein Projekt:

```text
http://localhost:5103/project=<project_public_id>
```

Der neue Kern der App ist nicht mehr Chat, sondern:

```text
Project
ProjectMembership
ProjectEmbedPolicy
ProjectServiceLink
ProjectVersion
ProjectAuditEvent
Chunk-Referenzen
Workspace-State
```

Nächster sinnvoller technischer Schritt:

```text
app.py und verbleibende Legacy-Routen prüfen
state_api_bp-Warnung bereinigen
```

Danach:

```text
alte Chat-/Speckle-/Transcript-Pfade systematisch entfernen oder hinter Feature-Flags legen
```

---

## 42. Ordner- und Filestruktur als Übersicht

Die `vectoplan-app` ist aktuell in Schichten aufgebaut. Die App selbst ist die Projekt-, Portal- und Workspace-Shell. Fachliche Daten wie 3D-Welt, Chunk-State, CAD-Geometrie oder LV-Inhalte liegen nicht in der App, sondern in den jeweiligen Microservices.

---

### 42.1 Architektur-Schema

```text
Browser
  │
  │  http://localhost:5103/
  │  http://localhost:5103/project=new
  │  http://localhost:5103/project=<project_public_id>
  ▼
vectoplan-app
  │
  ├─ UI-Schicht
  │    ├─ routes/ui/projects.py
  │    ├─ templates/chat_viewer.html
  │    ├─ templates/partials/project_sidebar.html
  │    ├─ templates/viewer/project.html
  │    ├─ static/css/chat.css
  │    ├─ static/css/project_sidebar.css
  │    ├─ static/css/project_workspace.css
  │    ├─ static/js/chat/main.js
  │    ├─ static/js/chat/project_sidebar_data.js
  │    ├─ static/js/chat/project_sidebar_resize.js
  │    ├─ static/js/chat/project_sidebar.js
  │    └─ static/js/project/project_form.js
  │
  ├─ API-Schicht
  │    ├─ routes/projects_api.py
  │    └─ routes/viewer_selection.py
  │
  ├─ Service-Schicht
  │    ├─ services/current_user.py
  │    ├─ services/project_permissions.py
  │    ├─ services/project_service.py
  │    └─ services/chunk_client.py
  │
  ├─ Model-Schicht
  │    ├─ models/base.py
  │    ├─ models/users.py
  │    ├─ models/legacy.py
  │    ├─ models/projects.py
  │    ├─ models/project_access.py
  │    ├─ models/project_embed.py
  │    ├─ models/project_links.py
  │    ├─ models/project_versions.py
  │    ├─ models/project_audit.py
  │    ├─ models/core.py
  │    └─ models/__init__.py
  │
  └─ externe Workspaces / Microservices
       ├─ vectoplan-editor      → 3D
       ├─ vectoplan-openLayer   → Map
       ├─ vectoplan-2d          → CAD/2D
       ├─ vectoplan-lv          → Leistungsverzeichnis
       ├─ vectoplan-chunk       → Chunk-Welt
       └─ vectoplan-library     → Assets/Inventory
```

---

### 42.2 Vereinfachter Runtime-Flow

```text
Browser
  ↓
GET /
  ↓
routes/ui/projects.py
  ↓
templates/chat_viewer.html
  ↓
Projekt-Sidebar + Workspace-Shell
  ↓
static/js/chat/main.js
  ↓
iframe src = /ui/project/new
        oder /ui/project/<project_public_id>/project
        oder /ui/project/<project_public_id>/editor
        oder /ui/project/<project_public_id>/map
        oder /ui/project/<project_public_id>/cad2d
        oder /ui/project/<project_public_id>/lv
```

---

### 42.3 Ordnerstruktur – aktueller Kern

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
│   ├── project_service.py
│   └── chunk_client.py
│
├── routes/
│   ├── projects_api.py
│   ├── viewer_selection.py
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

### 42.4 Dateibeschreibung – App-Wurzel

```text
services/vectoplan-app/
```

| Datei               | Aufgabe                                                                                                               | Status                                        |
| ------------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `app.py`            | Flask-App-Factory, Blueprint-Registrierung, Startup-Checks, DB-Initialisierung, Health/Ready-Routen, Header/CSP-Basis | aktiv, später State-/Legacy-Blueprints prüfen |
| `config.py`         | ENV-/Konfigurationswerte, Public/Internal-URL-Trennung, Editor/OpenLayer/Chunk/Library-Konfig                         | aktiv, um Chunk-Konfig erweitert              |
| `extensions.py`     | zentrale Flask-Erweiterungen, insbesondere `db`                                                                       | aktiv                                         |
| `auth.py`           | vorhandene Auth-/Kompatibilitätsbasis                                                                                 | vorhanden                                     |
| `versioning.py`     | ältere Versionierungslogik, teilweise noch Übergang/Legacy                                                            | später bereinigen                             |
| `seed_templates.py` | Template-Seeds, noch auf Altlasten prüfen                                                                             | später bereinigen                             |

---

### 42.5 Dateibeschreibung – Models

| Datei                 | Aufgabe                                | Wichtige Inhalte                                             |
| --------------------- | -------------------------------------- | ------------------------------------------------------------ |
| `__init__.py`         | zentraler Model-Import-Hub             | exportiert alle produktiven Models                           |
| `base.py`             | gemeinsame Model-Basis                 | `db`, JSON-Typ, Mixins, `safe_*`, ID-Generatoren             |
| `users.py`            | App-User / Platzhalter-User            | `AppUser`, `ensure_default_user()`                           |
| `legacy.py`           | technische Basis-/Altmodelle           | `Client`, `Blob`, `Conversation`, `Job`, `ConversationState` |
| `projects.py`         | zentrales Projektmodell                | `Project`, inkl. Chunk-Referenzfelder                        |
| `project_access.py`   | Projektrechte und Mitgliedschaften     | `ProjectMembership`                                          |
| `project_embed.py`    | iframe-/Embed-Policy                   | `ProjectEmbedPolicy`                                         |
| `project_links.py`    | Referenzen auf Microservices           | `ProjectServiceLink`                                         |
| `project_versions.py` | Version-/Snapshot-/Artefakt-Referenzen | `ProjectVersion`                                             |
| `project_audit.py`    | Audit-Events                           | `ProjectAuditEvent`                                          |
| `core.py`             | Kompatibilitäts-Export                 | re-exportiert modulare Models                                |

---

### 42.6 Model-Beziehungsübersicht

```text
AppUser
  │
  ├─ owns
  ▼
Project
  │
  ├─ has many ───────► ProjectMembership
  │                    └─ user_id → AppUser.id
  │
  ├─ has one/maybe ───► ProjectEmbedPolicy
  │
  ├─ has many ───────► ProjectServiceLink
  │                    ├─ chunk_project_id
  │                    ├─ chunk_universe_id
  │                    ├─ chunk_world_id
  │                    ├─ plan2d_id
  │                    ├─ lv_id
  │                    └─ externe Service-Referenzen
  │
  ├─ has many ───────► ProjectVersion
  │                    └─ Version-/Snapshot-/Artefakt-Referenzen
  │
  ├─ has many ───────► ProjectAuditEvent
  │                    └─ created / updated / linked / permission_changed / ...
  │
  └─ optional ───────► Conversation
                       └─ technischer State-/Historien-Kontext
                            └─ ConversationState
```

---

### 42.7 Service-Schicht als Schema

```text
routes/projects_api.py
routes/ui/projects.py
        │
        ▼
services/project_service.py
        │
        ├─ nutzt current_user.py
        ├─ nutzt project_permissions.py
        ├─ nutzt chunk_client.py
        └─ nutzt models/
              ├─ Project
              ├─ ProjectMembership
              ├─ ProjectEmbedPolicy
              ├─ ProjectServiceLink
              ├─ ProjectVersion
              ├─ ProjectAuditEvent
              └─ Conversation / ConversationState
```

---

### 42.8 Frontend-Schema

```text
chat_viewer.html
  │
  ├─ lädt APP_CONFIG
  │
  ├─ lädt CSS
  │    ├─ chat.css
  │    └─ project_sidebar.css
  │
  ├─ lädt Sidebar-JS
  │    ├─ project_sidebar_data.js
  │    ├─ project_sidebar_resize.js
  │    └─ project_sidebar.js
  │
  └─ lädt Workspace-Orchestrator
       └─ main.js
            ├─ initProjectSidebar()
            ├─ wireWorkspaceToolbar()
            ├─ setWorkspaceMode("project" | "map" | "3d" | "2d" | "lv" | "admin")
            ├─ syncWorkspaceGating()
            ├─ wireProjectEventBridge()
            ├─ wireVersionsDropdown()
            └─ wireThemeToggle()
```

---

### 42.9 Projektformular-Schema

```text
viewer/project.html
  │
  └─ project/project_form.js
       │
       ├─ liest Formularfelder
       ├─ baut JSON-Payload
       ├─ POST /v1/projects
       ├─ PATCH /v1/projects/<public_id>
       ├─ sendet vectoplan:project:saved an Parent
       ├─ aktualisiert Sidebar
       └─ navigiert Parent auf /project=<public_id>
```

---

### 42.10 Projekt-Erstellungsfluss als Textdiagramm

```text
User klickt "Neues Projekt"
  ↓
/project=new
  ↓
routes/ui/projects.py
  ↓
chat_viewer.html
  ↓
iframe: /ui/project/new
  ↓
viewer/project.html
  ↓
project_form.js
  ↓
POST /v1/projects
  ↓
routes/projects_api.py
  ↓
services/project_service.py
  ↓
Project wird erstellt
  ↓
Conversation wird technisch erstellt
  ↓
Owner ProjectMembership wird erstellt
  ↓
ProjectEmbedPolicy wird erstellt
  ↓
ProjectAuditEvent wird geschrieben
  ↓
Chunk-Provisioning erzeugt oder findet Chunk-Projektgraph
  ↓
Project.chunk_project_id / chunk_universe_id / chunk_world_id werden gesetzt
  ↓
API antwortet mit project + redirect_url
  ↓
project_form.js sendet Event an Parent
  ↓
main.js aktualisiert APP_CONFIG + Gating + Sidebar
  ↓
Browser navigiert auf /project=<project_public_id>
```

---

### 42.11 Workspace-Gating als Schema

```text
Projekt neu / nicht gespeichert
  ├─ Projekt: aktiv
  ├─ Admin: deaktiviert
  ├─ Versionen: deaktiviert
  ├─ Map: deaktiviert
  ├─ 3D: deaktiviert
  ├─ 2D: deaktiviert
  └─ LV: deaktiviert

Projekt gespeichert, aber nicht konfiguriert
  ├─ Projekt: aktiv
  ├─ Admin: aktiv
  ├─ Versionen: aktiv
  ├─ Map: deaktiviert
  ├─ 3D: deaktiviert
  ├─ 2D: deaktiviert
  └─ LV: deaktiviert

Projekt konfiguriert
  ├─ Projekt: aktiv
  ├─ Admin: aktiv
  ├─ Versionen: aktiv
  ├─ Map: aktiv
  ├─ 3D: aktiv
  ├─ 2D: aktiv
  └─ LV: aktiv
```

---

### 42.12 Workspace-Routing als Schema

```text
Toolbar "Projekt"
  ↓
/ui/project/<project_public_id>/project

Toolbar "Map"
  ↓
/ui/project/<project_public_id>/map
  ↓
vectoplan-openLayer Public URL

Toolbar "3D"
  ↓
/ui/project/<project_public_id>/editor
  ↓
vectoplan-editor Public URL
  ↓
vectoplan-editor nutzt Chunk-Referenzen

Toolbar "2D"
  ↓
/ui/project/<project_public_id>/cad2d

Toolbar "LV"
  ↓
/ui/project/<project_public_id>/lv

Toolbar "Admin"
  ↓
/ui/project/<project_public_id>/admin
```

---

### 42.13 API-/Service-/Model-Fluss

```text
POST /v1/projects
  ↓
routes/projects_api.py
  ↓
create_project_result()
  ↓
services/project_service.py
  ↓
create_project()
  ↓
models.Project
models.Conversation
models.ProjectMembership
models.ProjectEmbedPolicy
models.ProjectAuditEvent
  ↓
services/chunk_client.py
  ↓
vectoplan-chunk /projects/by-app/<app_project_public_id>
  ↓
Project wird mit Chunk-Referenzen aktualisiert
  ↓
db.session.commit()
```

---

### 42.14 Berechtigungsfluss

```text
Route braucht Zugriff
  ↓
require_project_permission(project, "edit", user_id)
  ↓
services/project_permissions.py
  ↓
1. Ist Projekt gelöscht?
2. Ist User owner_user_id?
3. Hat User ProjectMembership?
4. Ist Projekt public und view erlaubt?
  ↓
PermissionResult
  ↓
Route erlaubt oder wirft PermissionDenied
```

---

### 42.15 Datenfluss zwischen Projektformular und Shell

```text
project_form.js speichert Projekt
  ↓
API antwortet mit:
  ├─ project
  ├─ sidebar_item
  └─ redirect_url
  ↓
project_form.js sendet:
  vectoplan:project:saved
  ↓
main.js empfängt Event
  ↓
updateAppConfigProject()
setProjectDataset()
syncWorkspaceGating()
refreshProjectSidebar()
refreshVersionsUI()
```

---

### 42.16 Externe Services im Verhältnis zur App

```text
vectoplan-app
  │
  ├─ speichert Project.chunk_project_id
  │      └─ verweist auf vectoplan-chunk
  │
  ├─ speichert Project.chunk_universe_id
  │      └─ verweist auf vectoplan-chunk
  │
  ├─ speichert Project.chunk_world_id = world_spawn
  │      └─ verweist auf vectoplan-chunk
  │
  ├─ speichert Project.plan2d_id
  │      └─ verweist auf vectoplan-2d
  │
  ├─ speichert Project.lv_id
  │      └─ verweist auf vectoplan-lv
  │
  ├─ öffnet /ui/project/<id>/editor
  │      └─ Browser-Public-Route zu vectoplan-editor
  │
  └─ öffnet /ui/project/<id>/map
         └─ Browser-Public-Route zu vectoplan-openLayer
```

---

### 42.17 Was im aktuellen Projektfluss primär ist

Primär:

```text
routes/ui/projects.py
routes/projects_api.py
routes/viewer_selection.py
services/project_service.py
services/project_permissions.py
services/current_user.py
services/chunk_client.py
models/
templates/chat_viewer.html
templates/partials/project_sidebar.html
templates/viewer/project.html
static/js/chat/main.js
static/js/chat/project_sidebar*.js
static/js/project/project_form.js
static/css/chat.css
static/css/project_sidebar.css
static/css/project_workspace.css
```

Nicht mehr primär:

```text
routes/ui/chat.py
routes/chat/
alte Chat-Composer-Module
alte Transcript-Module
Speckle-Routen
alte Viewer-Routen
```

---

### 42.18 Spätere Umbenennung zur besseren Lesbarkeit

Aktuell funktionieren diese Namen, sind aber historisch:

```text
templates/chat_viewer.html
static/css/chat.css
static/js/chat/main.js
static/js/chat/project_sidebar_data.js
static/js/chat/project_sidebar_resize.js
static/js/chat/project_sidebar.js
```

Später lesbarer:

```text
templates/app_shell.html
static/css/app_shell.css
static/js/shell/main.js
static/js/project_sidebar/data.js
static/js/project_sidebar/resize.js
static/js/project_sidebar/sidebar.js
```

Die Umbenennung ist nicht dringend, aber sinnvoll, sobald der Projektfluss stabil ist.

---

## 43. Abschlussstand

Aktueller Status:

```text
vectoplan-app startet.
Projekt-Shell ist aktiv.
Chunk-Provisioning funktioniert.
Editor lädt world_spawn aus Chunk.
ConversationState-Fehler ist repariert.
```

Aktuell noch nicht final:

```text
state_api_bp Warnung
Legacy-Chat-Routen
Speckle-/Altviewer-Routen
alte Versionierungsreste
Namensbereinigung chat_* → shell_*
```

Empfohlener nächster Schritt:

```text
services/vectoplan-app/app.py prüfen
```

Ziel:

```text
Blueprint-Registrierung final verstehen
state_api_bp Warnung entfernen oder bewusst dokumentieren
Legacy-Routen sauber hinter Feature-Flags legen
```
****