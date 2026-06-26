<!-- /vectoplan-website/services/vectoplan-server/docs/IST-Zustand-vectoplan-app.md -->

# IST-Zustand – `vectoplan-app`

Stand: 2026-06-25
Status: Projektgeführte Portal-App mit Demo-/Auth-Kontext, Einladungslogik, Veröffentlichungssteuerung, zentralem Workspace-Gateway und repariertem 3D-Editor-Embed

> Teil 1 von 3

---

## 1. Zweck dieses Dokuments

Diese Datei beschreibt den aktuellen IST-Zustand der `vectoplan-app` nach dem Umbau von einer chatgeführten Shell zu einer projektgeführten Portal-, Projekt- und Workspace-App.

Die Datei ist eine Bestandsaufnahme des aktuellen Entwicklungsstands. Sie beschreibt:

```text
- welche Rolle vectoplan-app im Gesamtsystem hat,
- welche Dateien neu entstanden oder wesentlich angepasst wurden,
- wie Projektverwaltung, Workspace-Shell und Service-Referenzen funktionieren,
- wie Demo-/Auth-Kontext aktuell behandelt wird,
- wie Einladungen und lokale App-Mitgliedschaften gedacht sind,
- wie Projekt-Sichtbarkeit und Workspace-Veröffentlichung funktionieren,
- wie vectoplan-app mit vectoplan-chunk zusammenarbeitet,
- wie vectoplan-app externe Workspaces wie 3D und Map öffnet,
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
  = lokale Projekt-Mitgliedschaftsverwaltung
  = lokale AppUser-Verknüpfungs-/Platzhalterverwaltung
  = Einladungs-Orchestrator
  = Projekt-Sichtbarkeitsverwaltung
  = Workspace-Veröffentlichungsverwaltung
  = Workspace-Shell
  = iframe-Orchestrator
  = zentraler Browser-Gateway für externe Workspace-Services
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
  ≠ Registrierungsservice
  ≠ Login-/Auth-Service
  ≠ Account-/Abo-Service
  ≠ Bigdata-Service
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

Der eigentliche Projekt-Workspace wird im iframe über App-interne Gateway-Routen geladen:

```text
/ui/project/new/project
/ui/project/<project_public_id>/project
/ui/project/<project_public_id>/editor3d
/ui/project/<project_public_id>/map
/ui/project/<project_public_id>/cad2d
/ui/project/<project_public_id>/lv
/ui/project/<project_public_id>/versions
/ui/project/<project_public_id>/admin
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
8. Das Projektformular nutzt sichtbar nur noch eine Adressbox: address_text.
9. Strukturierte Adress-/Koordinatenfelder bleiben serverseitig vorbereitet, sind aber nicht mehr UI-Fokus.
10. Sichtbarkeit ist als private / unlisted / public modelliert.
11. Workspace-Veröffentlichung ist getrennt von Projekt-Sichtbarkeit.
12. Admin/System/Team/Permissions/Settings sind nie öffentlich sichtbar.
13. Team- und Einladungsbereiche sind nur für Projektverwalter sichtbar.
14. Einladungen erzeugen keine echten Useraccounts.
15. AppUser bleibt lokale Verknüpfung/Platzhalterstruktur, nicht Auth-Wahrheit.
16. Demo-Modus ist nicht persistent.
17. Map, 3D, 2D und LV werden erst nach Projekt-Konfiguration freigeschaltet.
18. 3D öffnet nun über /ui/project/<id>/editor3d.
19. /ui/project/<id>/editor3d prüft Projektzugriff und leitet dann auf vectoplan-editor PUBLIC_URL weiter.
20. Browser-Public-URLs und Docker-Internal-URLs bleiben getrennt.
21. vectoplan-app speichert nur App-Projekt-/Referenz-/Berechtigungsdaten.
22. vectoplan-app kann über chunk_client.py einen Chunk-Projektgraphen im Chunk-Service sicherstellen.
23. vectoplan-chunk liefert für App-Projekte eine konkrete world_spawn.
24. Editor kann über App-Projektkontext Chunks aus world_spawn laden.
25. ConversationState-Kompatibilität ist repariert.
26. routes/viewer.py ist der neue zentrale App-Gateway für /ui/project/...
27. services/workspace_embed_service.py baut browserfähige Public-Embed-Ziele.
28. static/js/chat/main.js ist weiterhin der Workspace-Orchestrator, obwohl der Pfad historisch noch chat enthält.
```

Wichtigster aktueller Testbefund:

```text
vectoplan-app
  → erzeugt/öffnet App-Projekt

vectoplan-app
  → PUT /projects/by-app/<app_project_public_id>
  → vectoplan-chunk legt/holt Chunk-Projekt, Universe und world_spawn

Browser
  → klickt 3D

vectoplan-app
  → /ui/project/<project_public_id>/editor3d
  → prüft Projektzugriff
  → baut Public-Editor-Embed-URL
  → redirect auf http://localhost:5100/editor?embed=1&...

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

### 4.3 3D-Reiter zeigte Platzhalter statt Editor

Aufgetretener Zustand:

```text
User klickt 3D
  ↓
iframe lädt /ui/project/<project_public_id>/editor oder /editor3d
  ↓
routes.viewer rendert generischen App-Platzhalter
```

Ursache:

```text
routes.viewer hatte für Nicht-Projekt-Workspaces einen generischen Platzhalter.
Der 3D-Workspace wurde noch nicht als externer Editor-Gateway behandelt.
static/js/chat/main.js hatte zusätzlich alte editor-Fallbacks.
```

Reparatur:

```text
services/vectoplan-app/services/workspace_embed_service.py
services/vectoplan-app/routes/viewer.py
services/vectoplan-app/templates/chat_viewer.html
services/vectoplan-app/static/js/chat/main.js
```

Aktueller Zielzustand:

```text
User klickt 3D
  ↓
static/js/chat/main.js
  ↓
iframe src = /ui/project/<project_public_id>/editor3d
  ↓
routes.viewer
  ↓
Projekt laden
  ↓
Berechtigung prüfen
  ↓
Workspace-Zugriff prüfen
  ↓
workspace_embed_service.py baut Browser-Public-URL
  ↓
302 Redirect auf vectoplan-editor Public URL
  ↓
http://localhost:5100/editor?embed=1&app_project_public_id=...
```

Wichtig:

```text
Der Browser bekommt niemals http://vectoplan-editor:5000.
Der Browser bekommt http://localhost:5100.
```

---

### 4.4 Projektformular wurde vereinfacht

Vorher war das Projektformular stärker technisch und enthielt mehrere sichtbare Felder für Adresse, Koordinaten und Systemreferenzen.

Aktueller UI-Zustand:

```text
sichtbar:
  name
  description
  address_text
  visibility

separat:
  publication
  team
  invitations

nicht mehr normal sichtbar:
  street
  house_number
  postal_code
  city
  region
  country
  latitude
  longitude
  coordinate_srid
  service_refs
  artifact_refs
  system_refs
```

Regel:

```text
Die sichtbare Adresse ist eine einzelne Box: address_text.
Strukturierte Adresse und Koordinaten bleiben für späteren Geocoder vorbereitet.
```

---

### 4.5 Sichtbarkeit und Veröffentlichung wurden getrennt

Projekt-Sichtbarkeit:

```text
private
unlisted
public
```

Workspace-Veröffentlichung:

```text
project
map
editor3d
cad2d
lv
versions
```

Nie öffentlich:

```text
admin
team
settings
permissions
system
system_refs
```

Wichtig:

```text
public bedeutet nicht automatisch, dass alle Workspaces öffentlich sind.
Workspaces müssen separat veröffentlicht werden.
```

---

### 4.6 Demo-/Auth-Kontext wurde vorbereitet

Aktueller Stand:

```text
dev mode:
  lokaler Platzhalter-User id=1

demo mode:
  kein persistenter User
  keine dauerhafte Speicherung
  keine echten Einladungen
  kein Bigdata-/Abo-Zugriff
  Reset nach Refresh oder Ablauf der Demo-Sitzung möglich

external auth mode:
  vorbereitet über Trusted Headers / zukünftigen Auth-Service
  lokale AppUser-Verknüpfung nur bei bekannter Identität
  keine automatische echte User-Erstellung in vectoplan-app
```

Wichtig:

```text
vectoplan-app ist nicht der Auth-Service.
vectoplan-app ist nicht der Registrierungsservice.
vectoplan-app erzeugt keine echten Benutzeraccounts.
```

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
Projekt-Sichtbarkeit
Workspace-Veröffentlichung
Projekt-Berechtigungen
Projekt-Mitgliedschaften
Projekt-Einladungen
Embed-Policy
Service-Links
Version-Referenzen
Audit-Events
App↔Chunk-Provisioning-Orchestrierung
technischer Conversation-/Workspace-State
Browser-Gateway zu externen Workspaces
```

Nicht Rolle:

```text
3D-Modellwahrheit
Chunk-Welt-Wahrheit
OpenLayer-Datenwahrheit
2D-CAD-Geometriewahrheit
LV-Fachdatenwahrheit
sichtbarer Chat-Client
Auth-Wahrheit
Registrierungs-Wahrheit
Abo-/Billing-Wahrheit
Bigdata-Wahrheit
```

Die App verwaltet zentrale App-Objekte wie:

```text
Project
ProjectMembership
ProjectInvitation
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
/ui/project/<project_public_id>/editor3d
```

Die App leitet nach Berechtigungsprüfung weiter auf:

```text
VECTOPLAN_EDITOR_PUBLIC_URL=http://localhost:5100
VECTOPLAN_EDITOR_ROUTE=/editor
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

Der aktuelle App-Gateway-Pfad lautet:

```text
/ui/project/<project_public_id>/cad2d
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

Der aktuelle App-Gateway-Pfad lautet:

```text
/ui/project/<project_public_id>/lv
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

### 5.8 Zukünftiger Auth-/Registrierungsservice

Rolle:

```text
Login
Registrierung
Account-Identität
E-Mail-Verifikation
Registrierte User prüfen
Einladungszustellung
```

Aktuelle App-Anbindung:

```text
services/auth_identity_client.py
```

Die App fragt zukünftig dort ab:

```text
Ist diese E-Mail registriert?
Darf an diese E-Mail eine Projekt-Einladung erstellt werden?
Soll eine Einladung zugestellt werden?
```

Wichtig:

```text
vectoplan-app erstellt keinen echten User.
vectoplan-app erstellt nur lokale Projekt-Einladungen und lokale App-Bezüge.
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

### 6.3 Aktueller Schutz im Frontend

`static/js/chat/main.js` blockiert bekannte interne/alte Browser-Ziele wie:

```text
http://vectoplan-editor:5000
http://editor:5000
http://openlayer:8090
http://vectoplan-openlayer:8090
```

Wenn ein unsicheres Ziel erkannt wird, fällt der Workspace-Orchestrator auf den App-Gateway-Pfad zurück.

### 6.4 Aktueller Schutz im Backend

`services/workspace_embed_service.py` baut externe Workspace-Ziele ausschließlich aus Public-URL-Konfiguration.

Beispiel für 3D:

```text
VECTOPLAN_EDITOR_PUBLIC_URL
+
VECTOPLAN_EDITOR_ROUTE
+
sichere Query-Parameter
```

Nicht:

```text
VECTOPLAN_EDITOR_INTERNAL_URL
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
iframe src = /ui/project/new/project
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
iframe src = /ui/project/new/project
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
routes/ui/projects.py
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
Versionen
Admin
```

Regel:

```text
Projekt: immer verfügbar
Admin: verfügbar, wenn Projekt existiert und User verwalten darf
Versionen: verfügbar, wenn Projekt existiert
Map/3D/2D/LV: verfügbar, wenn Projekt konfiguriert ist und Zugriff erlaubt ist
```

Für öffentliche oder ungelistete Projektlinks gilt zusätzlich:

```text
Workspace muss explizit veröffentlicht sein.
Admin/Team/Settings/Permissions/System sind nie öffentlich.
```

---

## 8. Projekt-Konfiguration und Projektformular

Ein Projekt gilt als konfiguriert, wenn die Minimaldefinition vorhanden ist.

Aktuell relevant:

```text
name
address_text
```

Nach erfolgreicher Konfiguration:

```text
setup_status = configured
is_configured = true
```

Dann können freigeschaltet werden:

```text
Map
3D
2D
LV
```

### 8.1 Sichtbare Projektformular-Felder

Aktueller UI-Fokus:

```text
name
description
address_text
visibility
```

### 8.2 Nicht mehr sichtbarer UI-Fokus

Diese Felder bleiben modellseitig bzw. für spätere Geocoder/Service-Integrationen vorbereitet, sind aber nicht mehr normale sichtbare Projektformular-Felder:

```text
street
house_number
postal_code
city
region
country
latitude
longitude
coordinate_srid
service_refs
artifact_refs
system_refs
```

### 8.3 Sichtbarkeit

Sichtbarkeit wird über Karten gesteuert:

```text
private
unlisted
public
```

Kein separater sichtbarer `is_public`-Schalter mehr.

`is_public` wird serverseitig aus `visibility` abgeleitet.

### 8.4 Workspace-Veröffentlichung

Separater Bereich:

```text
project
map
editor3d
cad2d
lv
versions
```

Nie veröffentlichbar:

```text
admin
team
settings
permissions
system
```

---

## 9. Aktueller primärer 3D-Flow

Der alte 3D-Pfad wurde aktualisiert.

Nicht mehr primär:

```text
/ui/project/<project_public_id>/editor
```

Aktuell primär:

```text
/ui/project/<project_public_id>/editor3d
```

Flow:

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
iframe src = /ui/project/<project_public_id>/editor3d
  ↓
routes/viewer.py
  ↓
Projekt laden
  ↓
Berechtigung prüfen
  ↓
Workspace-Zugriff prüfen
  ↓
services/workspace_embed_service.py
  ↓
Public-Editor-URL bauen
  ↓
302 Redirect
  ↓
http://localhost:5100/editor?embed=1&app_project_public_id=...
  ↓
vectoplan-editor rendert 3D-Editor
  ↓
vectoplan-editor fragt vectoplan-chunk
  ↓
world_spawn / chunks/batch
```

Wichtige Query-Parameter, die vorbereitet werden können:

```text
embed=1
source=vectoplan-app
workspace=editor3d
app_project_public_id=<project_public_id>
project_public_id=<project_public_id>
context_url=http://localhost:5103/ui/project/<project_public_id>/context.json
return_url=http://localhost:5103/project=<project_public_id>
read_only=0|1
chunk_project_id=<chunk_project_id>
chunk_universe_id=<chunk_universe_id>
chunk_world_id=world_spawn
```

Wichtig:

```text
context_url zeigt zurück auf vectoplan-app.
Der Editor kann daraus App-Projektkontext und Chunk-Referenzen lesen.
```

---

## 10. Aktuelle App↔Chunk-Integration

### 10.1 Ziel

Die App soll beim Anlegen oder Öffnen eines App-Projekts sicherstellen können, dass im Chunk-Service ein passender Chunk-Projektgraph existiert.

Zielgraph im Chunk-Service:

```text
Chunk Project
  └─ Universe
      └─ WorldInstance world_spawn
```

Die App speichert danach nur Referenzen.

---

### 10.2 App-Client

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
201 Created oder 200 OK
chunk_project_id vorhanden
chunk_universe_id vorhanden
chunk_world_id = world_spawn
```

---

### 10.3 App-seitig gespeicherte Chunk-Referenzen

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

### 10.4 Aktuell getesteter Flow

```text
vectoplan-app
  ↓
PUT /projects/by-app/<app_project_public_id>
  ↓
vectoplan-chunk
  ↓
201 Created oder 200 OK
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

## 11. Aktuelle Datenmodell-Architektur

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
  project_invitations.py
  project_embed.py
  project_links.py
  project_versions.py
  project_audit.py
  core.py
```

---

### 11.1 `models/base.py`

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

### 11.2 `models/users.py`

Zweck:

```text
App-lokaler User-Platzhalter und spätere Auth-Anbindung
```

Zentrales Model:

```text
AppUser
```

Aktueller Dev-Standard-User:

```text
id = 1
public_id = u_demo_1 oder lokaler Placeholder
handle = demo
display_name = Demo User
role = admin
is_placeholder = true
is_system = true
```

Wichtig:

```text
AppUser ist nicht die Auth-Wahrheit.
AppUser ist eine lokale App-Verknüpfung bzw. Platzhalterstruktur.
```

---

### 11.3 `models/legacy.py`

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

### 11.4 `models/projects.py`

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
Adressbox address_text
strukturierte Adresse optional
Koordinaten optional
Owner
Sichtbarkeit
Setup-Status
Chunk-Referenzen
2D-/LV-Referenzen
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
  "visibility": "private",
  "setup_status": "configured",
  "is_configured": true,
  "chunk_project_id": "chk_prj_prj_979eb0a4d8894086a5b2a74b_2653d3872366",
  "chunk_universe_id": "dev-universe",
  "chunk_world_id": "world_spawn"
}
```

---

### 11.5 `models/project_access.py`

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
view_settings
manage_settings
view_team
manage_team
view_admin
```

Wichtige Regel:

```text
Public-/Unlisted-Viewer erhalten keine Admin-/Settings-/Team-Rechte.
Demo-Kontext darf keine persistenten Team-/Admin-Aktionen ausführen.
```

---

### 11.6 `models/project_invitations.py`

Zweck:

```text
Projekt-Einladungen an bereits registrierte E-Mail-Adressen
```

Model:

```text
ProjectInvitation
```

Speichert:

```text
project_id
email
role
status
token_hash
expires_at
invited_by_user_id
accepted_by_user_id
dispatch_status
identity_status
audit-/metadata-Felder
```

Wichtige Statuswerte:

```text
pending
accepted
rejected
revoked
expired
failed
```

Wichtige Regel:

```text
Eine Einladung erzeugt keinen echten Useraccount.
Die E-Mail muss im externen Auth-/Registrierungsdienst bereits bekannt sein.
```

---

### 11.7 `models/project_embed.py`

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

Zusätzlich wird Veröffentlichung über den Publication-Service gelesen/geschrieben.

---

### 11.8 `models/project_links.py`

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

---

### 11.9 `models/project_versions.py`

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

---

### 11.10 `models/project_audit.py`

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
invitation_created
invitation_accepted
invitation_revoked
publication_changed
error
```

---

### 11.11 `models/core.py`

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

### 11.12 `models/__init__.py`

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
ProjectInvitation
ProjectEmbedPolicy
ProjectServiceLink
ProjectVersion
ProjectAuditEvent
```

Beispiel:

```python
from models import Project, ProjectMembership, ProjectInvitation, ProjectVersion
```

---

## 12. Aktuelle Service-Schicht

```text
services/vectoplan-app/services/
  current_user.py
  project_permissions.py
  project_service.py
  project_invitation_service.py
  project_publication_service.py
  auth_identity_client.py
  chunk_client.py
  workspace_embed_service.py
```

---

### 12.1 `services/current_user.py`

Zweck:

```text
zentraler Current-User-/Auth-/Demo-Kontext
```

Aktuelle Modi:

```text
dev
external
demo
```

Wichtig:

```text
dev:
  Platzhalter-User id=1 kann sichergestellt werden.

external:
  spätere Auth-Header-/Auth-Service-Verknüpfung.
  keine automatische User-Erstellung ohne bekannte lokale Verknüpfung.

demo:
  user_id = None
  persistent = false
  ttl = 1800 Sekunden
  keine echten Einladungen
  keine dauerhafte Speicherung
  kein Bigdata-/Abo-Zugriff
```

Wichtige Funktionen:

```python
get_current_user_context()
get_current_user_id_optional()
is_current_user_demo()
is_current_user_authenticated()
current_user_can_persist()
require_persistent_current_user()
ensure_default_user()
```

---

### 12.2 `services/project_permissions.py`

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
view_settings
manage_settings
view_team
manage_team
view_admin
```

Wichtige Regel:

```text
Demo-/nicht-persistente Kontexte dürfen keine Mutationen ausführen.
Öffentliche Betrachter sehen keine Settings-/Team-/Adminbereiche.
Admin/System/Permissions bleiben geschützt.
```

Wichtige Funktionen:

```python
get_project_permission_result(project, user_id)
require_project_permission(project, "edit", user_id)
can_view_project(project, user_id)
can_edit_project(project, user_id)
can_manage_project(project, user_id)
can_view_project_settings(...)
can_manage_project_team(...)
```

---

### 12.3 `services/project_service.py`

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

Aktuelle UI-Payload-Regel:

```text
Normales Projektformular akzeptiert:
  name
  description
  address_text
  visibility

System-/Service-Refs nur über explizit erlaubte Backendpfade.
```

---

### 12.4 `services/project_invitation_service.py`

Zweck:

```text
Einladungslogik für Projektmitglieder
```

Verantwortlich für:

```text
E-Mail normalisieren
Berechtigung manage/team prüfen
Demo-Kontext ablehnen
Auth-/Registrierungsdienst abfragen
unregistrierte E-Mail ablehnen
bereits vorhandene Mitglieder erkennen
doppelte Pending-Invites vermeiden
ProjectInvitation erzeugen
Einladungsstatus ändern
Audit-Events schreiben
```

Wichtig:

```text
vectoplan-app erstellt keinen echten Useraccount.
```

---

### 12.5 `services/auth_identity_client.py`

Zweck:

```text
Adapter zum zukünftigen Auth-/Registrierungsdienst
```

Verantwortlich für:

```text
E-Mail-Lookup
Registrierungsstatus prüfen
Einladungsdispatch vorbereiten
Dev-Mode-Placeholder
TTL-Cache
Statusdiagnose
```

Wichtige ENV-/Config-Werte:

```text
AUTH_IDENTITY_INTERNAL_URL
AUTH_IDENTITY_LOOKUP_PATH
AUTH_IDENTITY_INVITATION_DISPATCH_PATH
AUTH_IDENTITY_API_TOKEN
AUTH_IDENTITY_DEV_MODE
AUTH_IDENTITY_DEV_REGISTERED_EMAILS
AUTH_IDENTITY_DEV_ACCEPT_ALL_REGISTERED
AUTH_IDENTITY_PLACEHOLDER_INVITES
```

---

### 12.6 `services/project_publication_service.py`

Zweck:

```text
Projekt-Sichtbarkeit und Workspace-Veröffentlichung zentral verwalten
```

Verantwortlich für:

```text
visibility normalisieren
published_workspaces normalisieren
effective_published_workspaces berechnen
private/unlisted/public abbilden
Admin/System/Team/Permissions ausschließen
Embed-Policy defensiv lesen/schreiben
Workspace-Zugriff prüfen
```

Veröffentlichbare Workspaces:

```text
project
map
editor3d
cad2d
lv
versions
```

Nie veröffentlichbar:

```text
admin
team
settings
permissions
system
```

---

### 12.7 `services/chunk_client.py`

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

### 12.8 `services/workspace_embed_service.py`

Zweck:

```text
browserfähige Public-Embed-/Redirect-Ziele für externe Workspaces bauen
```

Aktuell wichtigster Workspace:

```text
editor3d
```

Aufgaben:

```text
Workspace normalisieren
Public-URL-Konfiguration lesen
INTERNAL_URLs nicht an den Browser geben
Editor-Embed-URL bauen
Map-Embed-URL vorbereiten
App-Kontext-URL setzen
Return-URL setzen
Chunk-Hints als Query-Parameter setzen
Debug-/Statusdaten bereitstellen
kleinen TTL-Cache nutzen
```

Beispiel-Ausgabe für 3D:

```text
http://localhost:5100/editor
  ?embed=1
  &source=vectoplan-app
  &workspace=editor3d
  &app_project_public_id=prj_...
  &project_public_id=prj_...
  &context_url=http://localhost:5103/ui/project/prj_.../context.json
  &return_url=http://localhost:5103/project=prj_...
  &chunk_world_id=world_spawn
```

---

## 13. Aktuelle Route-Struktur

```text
services/vectoplan-app/routes/
  projects_api.py
  viewer.py
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

### 13.1 `routes/projects_api.py`

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

GET    /v1/projects/<project_id>/publication
PUT    /v1/projects/<project_id>/publication
PATCH  /v1/projects/<project_id>/publication

GET    /v1/projects/<project_id>/invitations
POST   /v1/projects/<project_id>/invitations
DELETE /v1/projects/<project_id>/invitations/<invitation_id>

POST   /v1/project-invitations/<invitation_id>/accept
POST   /v1/project-invitations/<invitation_id>/reject
```

---

### 13.2 `routes/ui/projects.py`

Zweck:

```text
projektgeführte UI-Shell und Kompatibilitätsschicht
```

Wichtige Routen:

```text
GET /                       → Projekt-Shell, neues Projekt
GET /project=new            → Projekt-Shell, neues Projekt
GET /project=<project_id>   → Projekt-Shell, bestehendes Projekt
GET /project/<project_id>   → Redirect auf /project=<project_id>
GET /projects               → Projekt-Shell
GET /ui/projects/sidebar.json
GET /ui/projects/status.json
```

Wichtig:

```text
Diese Datei rendert die Shell.
Die iframe-Workspaces unter /ui/project/... liegen nun bei routes/viewer.py.
```

---

### 13.3 `routes/viewer.py`

Zweck:

```text
zentraler App-Gateway für Projekt-Workspaces unter /ui/project/...
```

Wichtige Routen:

```text
GET /ui/_status
GET /ui/project/_status

GET /ui/project/new
GET /ui/project/new/project
GET /ui/project/new/context.json

GET /ui/project/<project_id>
GET /ui/project/<project_id>/
GET /ui/project/<project_id>/project
GET /ui/project/<project_id>/context.json

GET /ui/project/<project_id>/<workspace>
GET /ui/project/<project_id>/<workspace>/context.json
```

Workspace-Verhalten:

```text
project:
  rendert templates/viewer/project.html

admin:
  rendert geschützten Projekt-/Admin-Kontext über project.html bzw. geschützten Bereich

editor3d:
  prüft Zugriff
  baut Public-Editor-Embed-URL
  redirect auf vectoplan-editor

map:
  prüft Zugriff
  kann über workspace_embed_service auf OpenLayer weiterleiten

cad2d/lv/versions:
  aktuell Shell-/Platzhalter-/Gateway-Pfade
  später Service-spezifisch verfeinern
```

---

### 13.4 `routes/viewer_selection.py`

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

### 13.5 `routes/ui/editor.py`

Zweck im neuen Stand:

```text
älterer/zusätzlicher 3D-Editor-Gateway
```

Wichtige Routen können weiterhin existieren:

```text
GET /ui/project/<project_id>/editor
GET /ui/chat/<chat_id>/editor
GET /ui/editor
```

Aktueller primärer Projektpfad ist aber:

```text
GET /ui/project/<project_id>/editor3d
```

über:

```text
routes/viewer.py
services/workspace_embed_service.py
```

---

### 13.6 `routes/ui/map.py`

Zweck:

```text
älterer/zusätzlicher Map-Gateway
```

Wichtige Routen können weiterhin existieren:

```text
GET /ui/project/<project_id>/map
GET /ui/chat/<chat_id>/map
GET /ui/map
GET /ui/project/<project_id>/map.json
GET /ui/chat/<chat_id>/map.json
```

Im neuen Stand kann `/ui/project/<id>/map` auch zentral über `routes/viewer.py` und `workspace_embed_service.py` laufen.

Regel bleibt:

```text
Browser bekommt nur OPENLAYER_PUBLIC_URL
```

Nie:

```text
http://openlayer:8090
```

---

### 13.7 `routes/ui/viewer2d.py`

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

### 13.8 `routes/ui/chat.py`

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

## 14. Aktuelle Template-Struktur

```text
services/vectoplan-app/templates/
  chat_viewer.html

  partials/
    project_sidebar.html
    demo_banner.html

  viewer/
    project.html

    partials/
      project_address.html
      project_visibility.html
      project_publication.html
      project_team.html
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

### 14.1 `templates/chat_viewer.html`

Trotz Name ist diese Datei jetzt die Projekt-/Workspace-Shell.

Zweck:

```text
Hauptlayout der App
Projekt-Sidebar einbinden
Workspace-Toolbar rendern
iframe rendern
APP_CONFIG erzeugen
Demo-Banner einbinden
Workspace-Pfade zentral bereitstellen
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
    projectPagePath: "/ui/project/<id>/project",
    editor3dPagePath: "/ui/project/<id>/editor3d",
    editorPagePath: "/ui/project/<id>/editor3d",
    initialEditorUrl: "/ui/project/<id>/editor3d",
    mapPagePath: "/ui/project/<id>/map",
    cad2dPagePath: "/ui/project/<id>/cad2d",
    lvPagePath: "/ui/project/<id>/lv",
    versionsPagePath: "/ui/project/<id>/versions",
    adminPagePath: "/ui/project/<id>/admin"
  }
}
```

---

### 14.2 `templates/partials/project_sidebar.html`

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

### 14.3 `templates/partials/demo_banner.html`

Zweck:

```text
sichtbarer Hinweis bei Demo-Kontext
```

Zeigt:

```text
nicht eingeloggt
keine dauerhafte Speicherung
keine echten Einladungen
kein Bigdata-/Abo-Zugriff
temporäre Demo-Sitzung
```

---

### 14.4 `templates/viewer/project.html`

Zweck:

```text
Projektformular im Workspace-iframe
```

Enthält sichtbar:

```text
Projektname
Beschreibung
Adressbox address_text
Sichtbarkeit private/unlisted/public
Veröffentlichung
Team und Einladungen
```

Nicht mehr normal sichtbar:

```text
Einzeladressfelder
Koordinatenfelder
Systemreferenzen
Service-Refs
Chunk-Refs
```

Beim Speichern:

```text
POST /v1/projects
PATCH /v1/projects/<public_id>
```

Danach wird der Parent aktualisiert.

---

### 14.5 `templates/viewer/partials/project_address.html`

Zweck:

```text
einzige sichtbare Adressbox
```

Wichtig:

```text
address_text ist das sichtbare Feld.
strukturierte Adresse/Koordinaten bleiben spätere Geocoder-Aufgabe.
```

---

### 14.6 `templates/viewer/partials/project_visibility.html`

Zweck:

```text
Projekt-Sichtbarkeit private/unlisted/public
```

Wichtig:

```text
Sichtbarkeit ist nicht identisch mit Workspace-Veröffentlichung.
```

---

### 14.7 `templates/viewer/partials/project_publication.html`

Zweck:

```text
Workspace-Veröffentlichung je Projekt steuern
```

Workspaces:

```text
project
map
editor3d
cad2d
lv
versions
```

Nie öffentlich:

```text
admin
team
settings
permissions
system
```

---

### 14.8 `templates/viewer/partials/project_team.html`

Zweck:

```text
Teammitglieder und Einladungen verwalten
```

Wichtig:

```text
nur Projektverwalter
keine Anzeige für unberechtigte User
keine echten Useraccounts erzeugen
Einladungen nur an registrierte E-Mails
```

---

## 15. Aktuelle Static-Struktur

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
      project_publication.js
      project_team.js
```

---

### 15.1 `static/css/chat.css`

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

---

### 15.2 `static/css/project_sidebar.css`

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

### 15.3 `static/css/project_workspace.css`

Zweck:

```text
Styling des Projektformulars im iframe
```

Steuert:

```text
Form-Layout
Sektionen
Input-Felder
Adressbox
Sichtbarkeitskarten
Publication-Karten
Team-/Einladungslisten
Demo-Hinweise
Sticky Save-Bar
Status-Chips
Projekt-Konfigurationszustand
```

---

### 15.4 `static/js/chat/main.js`

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

Aktueller wichtiger Fix:

```text
3D normalisiert auf editor3d.
3D-Pfad ist /ui/project/<id>/editor3d.
Alte editor-/viewer-Fallbacks werden auf editor3d gemappt.
Interne Docker-Hosts werden als unsichere Browser-Ziele blockiert.
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
window.__VECTOPLAN_WORKSPACE_DEBUG__.modes.set("3d")
window.__VECTOPLAN_WORKSPACE_DEBUG__.routes.editor()
```

---

### 15.5 `static/js/project/project_form.js`

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

Gesendeter Standard-Payload:

```text
name
title
description
address_text
address.text
visibility
```

Nicht mehr gesendet im normalen UI-Pfad:

```text
street
house_number
postal_code
city
region
country
latitude
longitude
coordinate_srid
is_public
service_refs
system_refs
```

---

### 15.6 `static/js/project/project_publication.js`

Zweck:

```text
Veröffentlichungseinstellungen speichern
```

Steuert:

```text
published_workspaces
require_auth
require_project_permission
```

Speichert über:

```text
PATCH /v1/projects/<project_id>/publication
```

---

### 15.7 `static/js/project/project_team.js`

Zweck:

```text
Teammitglieder, Rollen und Einladungen verwalten
```

Steuert:

```text
GET    /v1/projects/<project_id>/members
PATCH  /v1/projects/<project_id>/members/<user_id>
DELETE /v1/projects/<project_id>/members/<user_id>
GET    /v1/projects/<project_id>/invitations
POST   /v1/projects/<project_id>/invitations
DELETE /v1/projects/<project_id>/invitations/<invitation_id>
```

Wichtig:

```text
Owner wird nicht per Einladung vergeben.
Echte Useraccounts werden nicht erzeugt.
```

---

## 16. Fortsetzung in Teil 2

Teil 2 aktualisiert anschließend:

```text
16. Detaillierte Ordner-/Filestruktur
17. Wichtigste Endpoints
18. Beispiel: Projekt erstellen
19. Beispiel: Projekt bearbeiten
20. Beispiel: Sidebar laden
21. Beispiel: Service-Link setzen
22. Beispiel: Version anlegen
23. Beispiel: Embed-/Publication-Policy
24. Beispiel: Workspace-State speichern
25. Frontend-Eventfluss nach Projektspeicherung
26. Aktueller UI-Zielzustand
27. Datenbank- und Runtime-Hinweise
```
## 16. Detaillierte aktuelle Ordner-/Filestruktur

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
    project_invitations.py
    project_embed.py
    project_links.py
    project_versions.py
    project_audit.py
    core.py

  services/
    current_user.py
    auth_identity_client.py
    project_permissions.py
    project_service.py
    project_invitation_service.py
    project_publication_service.py
    workspace_embed_service.py
    chunk_client.py

  routes/
    projects_api.py
    viewer.py
    viewer_selection.py

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

    embed.py
    speckle_upload.py
    vectoplan_ingest.py
    vectoplan_align.py

  templates/
    chat_viewer.html

    partials/
      project_sidebar.html
      demo_banner.html

    viewer/
      project.html

      partials/
        project_address.html
        project_visibility.html
        project_publication.html
        project_team.html

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
        project_publication.js
        project_team.js
```

Hinweis:

```text
Einige ältere Dateien können noch vorhanden sein, obwohl sie nicht mehr zum neuen primären Projektfluss gehören.
Sie werden später geprüft, deaktiviert oder entfernt.
```

---

## 17. Wichtigste Endpoints

### 17.1 Projekt-Shell / UI

```text
GET /                                      → Projekt-Shell, neues Projekt
GET /project=new                          → Projekt-Shell, neues Projekt
GET /project=<project_public_id>          → Projekt-Shell, bestehendes Projekt
GET /project/<project_public_id>          → Redirect auf /project=<project_public_id>
GET /projects                             → Projekt-Shell
```

Wichtig:

```text
Diese Routen rendern die App-Shell, nicht direkt den iframe-Workspace.
Die Shell-Datei ist aktuell templates/chat_viewer.html.
```

---

### 17.2 Projekt-Workspaces / iframe-Gateway

```text
GET /ui/project/new
GET /ui/project/new/project
GET /ui/project/new/context.json

GET /ui/project/<project_public_id>
GET /ui/project/<project_public_id>/
GET /ui/project/<project_public_id>/project
GET /ui/project/<project_public_id>/context.json

GET /ui/project/<project_public_id>/editor3d
GET /ui/project/<project_public_id>/map
GET /ui/project/<project_public_id>/cad2d
GET /ui/project/<project_public_id>/lv
GET /ui/project/<project_public_id>/versions
GET /ui/project/<project_public_id>/admin

GET /ui/project/<project_public_id>/<workspace>/context.json
```

Zentrale Datei:

```text
services/vectoplan-app/routes/viewer.py
```

Wichtig:

```text
/ui/project/<project_public_id>/editor3d ist jetzt der primäre 3D-Gateway.
Nicht mehr primär: /ui/project/<project_public_id>/editor
```

---

### 17.3 Projekt-API

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

GET    /v1/projects/<project_id>/publication
PUT    /v1/projects/<project_id>/publication
PATCH  /v1/projects/<project_id>/publication

GET    /v1/projects/<project_id>/publication/workspaces/<workspace>
GET    /v1/projects/<project_id>/workspace-access/<workspace>

GET    /v1/projects/<project_id>/invitations
POST   /v1/projects/<project_id>/invitations
DELETE /v1/projects/<project_id>/invitations/<invitation_id>
POST   /v1/projects/<project_id>/invitations/<invitation_id>/revoke

POST   /v1/project-invitations/<invitation_id>/accept
POST   /v1/project-invitations/<invitation_id>/reject
POST   /v1/project-invitations/<invitation_id>/expire
```

Zentrale Datei:

```text
services/vectoplan-app/routes/projects_api.py
```

---

### 17.4 Technischer State-Kompatibilitätspfad

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

Gespeicherte Werte:

```text
mode
workspace_mode
viewer_selection
workspace_selection
last_2d_selection
last_2d_hover
last_editor_selection
last_editor_message
last_map_selection
last_map_hover
last_lv_selection
last_workspace_error
```

Nicht speichern:

```text
speckle_*
legacy_viewer*
viewer_url
model_id
version_id
runtime_url
service_refs
artifact_refs
system_refs
chunk snapshot internals
```

---

## 18. Beispiel: Projekt erstellen

Aktueller Request im vereinfachten Projektformular:

```bash
curl -X POST http://localhost:5103/v1/projects \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Testprojekt",
    "description": "Erstes Testprojekt",
    "address_text": "Musterstraße 1, 12345 Berlin, Deutschland",
    "visibility": "private"
  }'
```

Nicht mehr im normalen UI-Payload:

```json
{
  "street": "Musterstraße",
  "house_number": "1",
  "postal_code": "12345",
  "city": "Berlin",
  "region": "Berlin",
  "country": "DE",
  "latitude": 52.52,
  "longitude": 13.405,
  "coordinate_srid": 4326,
  "is_public": false
}
```

Diese Felder können modellseitig oder über spätere Geocoder-/Systempfade weiter existieren, sind aber nicht mehr normal sichtbarer Projektformular-Fokus.

Erwartetes Ergebnis:

```json
{
  "ok": true,
  "code": "project_created",
  "project": {
    "public_id": "prj_...",
    "name": "Testprojekt",
    "description": "Erstes Testprojekt",
    "address_text": "Musterstraße 1, 12345 Berlin, Deutschland",
    "visibility": "private",
    "is_public": false,
    "setup_status": "configured",
    "is_configured": true,
    "chunk_project_id": "chk_prj_...",
    "chunk_universe_id": "dev-universe",
    "chunk_world_id": "world_spawn"
  },
  "redirect_url": "/project=prj_..."
}
```

Nach Erstellung:

```text
project_form.js sendet Parent-Event.
main.js aktualisiert APP_CONFIG.
Sidebar wird aktualisiert.
Workspace-Gating wird neu berechnet.
Browser öffnet /project=<project_public_id>.
```

---

## 19. Beispiel: Projekt bearbeiten

Request:

```bash
curl -X PATCH http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Testprojekt aktualisiert",
    "description": "Neue Beschreibung",
    "address_text": "Innenried 31",
    "visibility": "private"
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
    "description": "Neue Beschreibung",
    "address_text": "Innenried 31",
    "visibility": "private",
    "setup_status": "configured",
    "is_configured": true
  }
}
```

Wichtig:

```text
Normale Bearbeitung verändert keine Systemreferenzen.
Chunk-/Service-/Artifact-Referenzen bleiben separaten Backend-/Servicepfaden vorbehalten.
```

---

## 20. Beispiel: Sidebar laden

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

## 21. Beispiel: Service-Link setzen

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

## 22. Beispiel: Version anlegen

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

Wichtig:

```text
Versionen sind App-seitige Referenzen.
Sie ersetzen nicht die fachlichen Snapshot-/Persistenzmechanismen der einzelnen Microservices.
```

---

## 23. Beispiel: Projekt-Sichtbarkeit und Veröffentlichung

### 23.1 Sichtbarkeit lesen

```bash
curl http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b
```

Relevanter Ausschnitt:

```json
{
  "project": {
    "public_id": "prj_979eb0a4d8894086a5b2a74b",
    "visibility": "private",
    "is_public": false
  }
}
```

Sichtbarkeiten:

```text
private
unlisted
public
```

---

### 23.2 Veröffentlichung lesen

```bash
curl http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b/publication
```

Beispiel:

```json
{
  "ok": true,
  "publication": {
    "visibility": "public",
    "publication_enabled": true,
    "published_workspaces": {
      "project": true,
      "map": true,
      "editor3d": true,
      "cad2d": false,
      "lv": false,
      "versions": false
    },
    "effective_published_workspaces": {
      "project": true,
      "map": true,
      "editor3d": true,
      "cad2d": false,
      "lv": false,
      "versions": false
    },
    "require_auth": false,
    "require_project_permission": false
  }
}
```

---

### 23.3 Veröffentlichung ändern

```bash
curl -X PATCH http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b/publication \
  -H "Content-Type: application/json" \
  -d '{
    "visibility": "public",
    "published_workspaces": {
      "project": true,
      "map": true,
      "editor3d": true,
      "cad2d": false,
      "lv": false,
      "versions": false
    },
    "require_auth": false,
    "require_project_permission": false
  }'
```

Regel:

```text
Projekt-Sichtbarkeit und Workspace-Veröffentlichung sind getrennt.
Ein öffentliches Projekt veröffentlicht nicht automatisch alle Workspaces.
```

Nie öffentlich:

```text
admin
team
settings
permissions
system
```

---

## 24. Beispiel: Projekt-Einladung erstellen

Request:

```bash
curl -X POST http://localhost:5103/v1/projects/prj_979eb0a4d8894086a5b2a74b/invitations \
  -H "Content-Type: application/json" \
  -d '{
    "email": "person@example.com",
    "role": "editor"
  }'
```

Ablauf:

```text
1. User muss Projekt verwalten dürfen.
2. Demo-Kontext wird abgelehnt.
3. E-Mail wird normalisiert.
4. Auth-/Registrierungsdienst wird gefragt.
5. Wenn E-Mail nicht registriert ist: Ablehnung.
6. Wenn E-Mail registriert ist: ProjectInvitation wird erstellt.
7. Optional wird Einladungsdispatch vorbereitet.
8. Audit-Event wird geschrieben.
```

Erwartetes Ergebnis bei registrierter E-Mail:

```json
{
  "ok": true,
  "code": "project_invitation_created",
  "invitation": {
    "id": 123,
    "email": "person@example.com",
    "role": "editor",
    "status": "pending",
    "dispatch_status": "placeholder"
  }
}
```

Erwartetes Ergebnis bei nicht registrierter E-Mail:

```json
{
  "ok": false,
  "code": "auth_identity_email_not_registered",
  "error": "email not registered"
}
```

Wichtig:

```text
vectoplan-app erzeugt keinen echten Useraccount.
Owner wird nicht per Einladung vergeben.
```

---

## 25. Beispiel: Workspace-State speichern

Die App hat weiterhin technische State-Routen für Workspace-/Viewer-Auswahl.

Beispiel:

```bash
curl -X PUT http://localhost:5103/v1/chats/<chat_id>/viewer/selection \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "editor",
    "workspace_mode": "3d"
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

## 26. Frontend-Eventfluss nach Projektspeicherung

Wenn das Projektformular speichert, sendet `project_form.js` ein Event an den Parent:

```js
window.parent.dispatchEvent(new CustomEvent("vectoplan:project:saved", {
  detail: {
    project: savedProject,
    payload: responsePayload,
    isNew: true,
    isConfigured: true,
    redirectUrl: "/project=prj_..."
  }
}));
```

`static/js/chat/main.js` reagiert darauf:

```text
Projekt in APP_CONFIG aktualisieren
Root data-Attribute aktualisieren
Workspace-Pfade aktualisieren
3D-Pfad auf /ui/project/<id>/editor3d setzen
Workspace-Gating neu berechnen
Projekt-Sidebar refreshen
Versionen aktualisieren
ggf. Parent auf /project=<project_public_id> navigieren
```

Der sichtbare Chat ist daran nicht beteiligt.

---

## 27. Aktueller UI-Zielzustand

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

Aktuelle Projektseite im iframe:

```text
Basisdaten
  ├─ Projektname
  ├─ Beschreibung

Adresse
  └─ address_text

Sichtbarkeit
  ├─ private
  ├─ unlisted
  └─ public

Veröffentlichung
  ├─ project
  ├─ map
  ├─ editor3d
  ├─ cad2d
  ├─ lv
  └─ versions

Team und Rechte
  ├─ Mitglieder
  ├─ Einladungen
  └─ Rollenübersicht
```

Nur Projektverwalter sehen:

```text
Veröffentlichung
Team und Rechte
Admin/Settings/Systembereiche
```

Öffentliche Betrachter sehen diese Bereiche nicht.

---

## 28. Aktueller Datenbank- und Runtime-Hinweis

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
project_invitations table missing
ProjectInvitation import missing
```

Aktueller Stand nach den letzten Reparaturen:

```text
ConversationState-Kompatibilitätsfehler ist behoben.
Chunk-Service läuft read-only in Runtime.
Chunk-Seed/Schema sind ready.
App kann Chunk-Projekt für App-Projekt erzeugen.
Editor kann world_spawn laden.
3D-Reiter öffnet über /ui/project/<id>/editor3d den echten Editor.
Projektformular nutzt address_text als einzige sichtbare Adressbox.
Einladungsmodell und Invitation-Service sind vorbereitet.
Publication-Service ist vorbereitet.
Workspace-Embed-Service ist vorbereitet.
```

Noch möglich:

```text
state_api_bp unavailable; skipped
```

Einordnung:

```text
kein aktueller Blocker
separat prüfen
nicht derselbe Fehler wie ConversationState.merge_patch
```

---

## 29. Fortsetzung in Teil 3

Teil 3 aktualisiert anschließend:

```text
29. Bekannte technische Altlasten
30. Was behalten werden sollte
31. Was später umbenannt werden sollte
32. Was später entfernt/deaktiviert werden sollte
33. Aktueller primärer Projektfluss
34. Aktueller primärer App↔Chunk-Provisioning-Flow
35. Aktueller primärer 3D-Flow
36. Aktueller primärer Map-Flow
37. Aktueller primärer 2D-Flow
38. Aktueller primärer LV-Flow
39. Aktueller Workspace-State-Flow
40. Aktuelle Tests / Smoke-Checks
41. Offene Punkte
42. Was aktuell als stabil gilt
43. Gesamtfazit
44. Ordner- und Filestruktur als Übersicht
45. Abschlussstand
```
## 29. Bekannte technische Altlasten

### 29.1 Dateinamen mit altem Chat-Begriff

Einige neue Shell-Dateien liegen noch unter `chat/`, obwohl der sichtbare Chat entfernt wurde:

```text id="2kde7r"
templates/chat_viewer.html
static/js/chat/main.js
static/css/chat.css
static/js/chat/project_sidebar*.js
```

Aktuell bewusst akzeptiert, um Umbauaufwand zu reduzieren.

Später möglich:

```text id="vnqimr"
templates/app_shell.html
static/js/shell/main.js
static/css/shell.css
static/js/project_sidebar/
```

Wichtig:

```text id="0c612x"
Nicht während aktiver Fehleranalyse umbenennen.
Erst umbenennen, wenn Projektfluss, Editor-Embed, Publication und Invitations stabil sind.
```

---

### 29.2 Conversation bleibt technisch vorhanden

`Conversation` bleibt im Datenmodell, obwohl der sichtbare Chat entfernt wurde.

Aktuelle Gründe:

```text id="bdgtrz"
technischer State-Kontext
Kompatibilität zu alten Workspace-State-Routen
Version-/Upload-Historie in Altpfaden
mögliche spätere Projektkommunikation
Übergangspfad für viewer_selection.py
```

Später prüfen:

```text id="e3plso"
Conversation vollständig entfernen
oder als unsichtbare Projekt-Historie behalten
oder nur noch für technische Workspace-State-Kompatibilität nutzen
```

---

### 29.3 ConversationState ist jetzt Kompatibilitätsschicht

`ConversationState` ist aktuell wichtig für alte und neutrale UI-State-Routen.

Aktueller Zustand:

```text id="x7mqkm"
ConversationState.state      = echte DB-Spalte
ConversationState.state_json = Kompatibilitätsalias
ConversationState.selection  = optionale Auswahl-/Selection-Spalte
```

Neue/ergänzte API:

```text id="6jurjr"
ConversationState.get_or_create(...)
ConversationState.merge_patch(...)
ConversationState.replace_state(...)
```

Damit bleiben ältere Routen stabil, ohne überall auf freie Helper-Funktionen umgebaut werden zu müssen.

---

### 29.4 `state_api_bp unavailable`

Beim App-Start kann weiterhin erscheinen:

```text id="wknbxe"
blueprint state_api_bp unavailable; skipped
```

Einordnung:

```text id="0o87xx"
kein aktueller Blocker
nicht derselbe Fehler wie ConversationState.merge_patch
App startet trotzdem
Gunicorn-Worker booten
Projekt-/Chunk-/Editor-Flow funktioniert
```

Später prüfen:

```text id="0hml22"
alte state_api_bp-Registrierung entfernen
oder Blueprint korrekt hinter Feature-Flag legen
oder vorhandene State-Routen konsolidieren
```

---

### 29.5 Alte Chat-Routen existieren teilweise noch

Diese können noch vorhanden sein:

```text id="ltxk66"
routes/ui/chat.py
routes/chat/
routes/state.py
routes/viewer_selection.py
```

Sie sind nicht mehr primärer UI-Zielpfad.

Aktueller primärer Shell-Zielpfad:

```text id="gip8gb"
routes/ui/projects.py
  → templates/chat_viewer.html
  → iframe /ui/project/...
  → routes/viewer.py
```

---

### 29.6 Speckle-/Altviewer-Altlasten

Noch zu prüfen:

```text id="jjf7d8"
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

```text id="nfjrdk"
Speckle-Altlasten entfernen oder hart deaktivieren
keine Speckle-Karten
kein Auto-Publish
keine alten Viewer-URLs
kein Rückfall auf alte 3D-Viewerlogik
```

---

### 29.7 Alte 3D-Routen

Nicht mehr primär:

```text id="jz40j7"
/ui/project/<project_id>/editor
```

Aktuell primär:

```text id="ptgk1d"
/ui/project/<project_id>/editor3d
```

Wichtig:

```text id="d1om5k"
editor3d läuft über routes/viewer.py.
routes/viewer.py prüft Zugriff und leitet über workspace_embed_service.py an vectoplan-editor PUBLIC_URL weiter.
```

Alte `/editor`-Routen können als Kompatibilität bestehen, sollten aber nicht mehr Hauptpfad in Shell oder Dokumentation sein.

---

### 29.8 Admin-/Team-/Settings-Sichtbarkeit

Bekannte Sicherheitsregel:

```text id="2xry38"
Admin
Team
Settings
Permissions
System
```

dürfen nicht öffentlich sichtbar sein.

Das gilt auch bei:

```text id="rzrbr8"
visibility = public
visibility = unlisted
published_workspaces.project = true
```

Öffentliche Betrachter dürfen nur explizit veröffentlichte, erlaubte Workspaces sehen.

---

### 29.9 Einladungslogik ist vorbereitet, aber Auth-Service ist noch Platzhalter

Aktuell vorbereitet:

```text id="j5j9w2"
ProjectInvitation Model
project_invitation_service.py
auth_identity_client.py
Invitation API-Routen
Team-UI in project_team.html
project_team.js
```

Noch nicht final:

```text id="2dcgza"
echter Auth-/Registrierungsdienst
echter Einladungsversand
echte Account-Erstellung
echtes Login-/Abo-System
```

Wichtig:

```text id="qrg0oj"
vectoplan-app erzeugt keine echten User.
```

---

### 29.10 Publication-Logik ist vorbereitet, aber Public-Produktfluss ist noch zu testen

Aktuell vorbereitet:

```text id="83fqw1"
project_publication_service.py
project_publication.html
project_publication.js
Publication API-Routen
Workspace-Zugriffsprüfung
```

Noch zu testen:

```text id="9ah8ul"
public Projekt-Link ohne Login
unlisted Projekt-Link ohne Listing
öffentliche Map
öffentlicher 3D-Workspace
nicht öffentliche Admin-/Team-Bereiche
```

---

## 30. Was behalten werden sollte

Behalten:

```text id="nsl4db"
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
  project_invitations.py
  project_embed.py
  project_links.py
  project_versions.py
  project_audit.py
  core.py

services/
  current_user.py
  auth_identity_client.py
  project_permissions.py
  project_service.py
  project_invitation_service.py
  project_publication_service.py
  workspace_embed_service.py
  chunk_client.py

routes/
  projects_api.py
  viewer.py
  viewer_selection.py

routes/ui/
  projects.py
  editor.py
  map.py
  viewer2d.py

templates/
  chat_viewer.html
  partials/project_sidebar.html
  partials/demo_banner.html
  viewer/project.html
  viewer/partials/project_address.html
  viewer/partials/project_visibility.html
  viewer/partials/project_publication.html
  viewer/partials/project_team.html

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
  project_publication.js
  project_team.js
```

`viewer_selection.py` bleibt vorerst als technischer State-Kompatibilitätspfad erhalten.

---

## 31. Was später umbenannt werden sollte

Aktuell funktionieren die Dateien, aber die Namen sind historisch:

```text id="q5n6j1"
templates/chat_viewer.html      → templates/app_shell.html
static/css/chat.css             → static/css/app_shell.css
static/js/chat/main.js          → static/js/shell/main.js
static/js/chat/project_sidebar* → static/js/project_sidebar/*
```

Nicht sofort nötig, aber für Lesbarkeit sinnvoll.

Wichtig:

```text id="acfr2g"
Erst umbenennen, wenn Projektfluss, Chunk-Provisioning, Editor-Embed, Publication und Invitations stabil sind.
Keine gleichzeitige Umbenennung während Fehleranalyse.
```

---

## 32. Was später entfernt oder deaktiviert werden sollte

Prüfen und ggf. entfernen:

```text id="o3tnzm"
routes/embed.py
routes/speckle_upload.py
routes/vectoplan_ingest.py
routes/vectoplan_align.py
```

Zusätzlich neutralisieren:

```text id="owkahh"
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

```text id="xtw40x"
routes/state.py
state_api_bp Registrierung
alte Chat-State-APIs
alte Transcript-Versionierung
alte /ui/project/<id>/editor-Fallbacks
alte /ui/chat/<chat_id>/editor-Fallbacks
```

Nicht entfernen, solange noch UI oder Legacy-Pfade darauf zugreifen.

---

## 33. Aktueller primärer Projektfluss

```text id="j8i5ef"
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
/ui/project/new/project
oder /ui/project/<project_public_id>/project
  ↓
routes/viewer.py
  ↓
viewer/project.html
  ↓
project_form.js
  ↓
POST/PATCH /v1/projects
  ↓
routes/projects_api.py
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

## 34. Aktueller primärer App↔Chunk-Provisioning-Flow

```text id="teykbk"
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

```text id="e2asrl"
POST /projects/<chunk_project_id>/worlds/world_spawn/chunks/batch
```

Ergebnis:

```text id="7eqvms"
HTTP 200
```

---

## 35. Aktueller primärer 3D-Flow

```text id="8ypqzy"
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
iframe src = /ui/project/<project_public_id>/editor3d
  ↓
routes/viewer.py
  ↓
Projekt laden
  ↓
Berechtigung prüfen
  ↓
Workspace-Zugriff prüfen
  ↓
services/workspace_embed_service.py
  ↓
Public-Editor-URL bauen
  ↓
302 Redirect
  ↓
http://localhost:5100/editor?embed=1&app_project_public_id=...
  ↓
vectoplan-editor rendert 3D-Editor
  ↓
vectoplan-editor fragt vectoplan-chunk
  ↓
world_spawn / chunks/batch
```

Wichtig:

```text id="dadxvc"
/ui/project/<project_public_id>/editor3d ist App-Gateway.
http://localhost:5100/editor ist Browser-Public-Ziel.
http://vectoplan-editor:5000 ist niemals Browser-Ziel.
```

Query-Parameter können enthalten:

```text id="6cv5nc"
embed=1
source=vectoplan-app
workspace=editor3d
app_project_public_id=<project_public_id>
project_public_id=<project_public_id>
context_url=http://localhost:5103/ui/project/<project_public_id>/context.json
return_url=http://localhost:5103/project=<project_public_id>
read_only=0|1
chunk_project_id=<chunk_project_id>
chunk_universe_id=<chunk_universe_id>
chunk_world_id=world_spawn
```

---

## 36. Aktueller primärer Map-Flow

```text id="v8t9e8"
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
routes/viewer.py oder bestehender Map-Gateway
  ↓
OpenLayer Public URL
  ↓
http://localhost:5190/map?embed=1&project_public_id=...
```

Regel:

```text id="9q3k27"
Browser bekommt nur OPENLAYER_PUBLIC_URL.
Browser bekommt nie http://openlayer:8090.
```

---

## 37. Aktueller primärer 2D-Flow

```text id="n4uhaf"
Browser
  ↓
http://localhost:5103/project=<project_public_id>
  ↓
Projekt ist konfiguriert
  ↓
User klickt 2D
  ↓
static/js/chat/main.js
  ↓
iframe src = /ui/project/<project_public_id>/cad2d
  ↓
routes/viewer.py oder routes/ui/viewer2d.py
  ↓
2D-/CAD-Workspace oder locked response
```

Aktuell ist der 2D-Flow vorbereitet, aber fachliche 2D-Daten bleiben außerhalb der App.

---

## 38. Aktueller primärer LV-Flow

```text id="pe9bd9"
Browser
  ↓
http://localhost:5103/project=<project_public_id>
  ↓
Projekt ist konfiguriert
  ↓
User klickt LV
  ↓
static/js/chat/main.js
  ↓
iframe src = /ui/project/<project_public_id>/lv
  ↓
routes/viewer.py
  ↓
aktuell Platzhalter / später LV-Service
```

Wichtig:

```text id="80a7pc"
vectoplan-app speichert keine LV-Fachdaten.
vectoplan-app speichert nur lv_id / Service-Links / Referenzen.
```

---

## 39. Aktueller Publication-Flow

Projektverwalter öffnet Projekt-Workspace:

```text id="59r74i"
Browser
  ↓
/project=<project_public_id>
  ↓
Projekt-Workspace
  ↓
project_publication.html
  ↓
project_publication.js
  ↓
GET /v1/projects/<project_id>/publication
```

Beim Speichern:

```text id="e48fz0"
project_publication.js
  ↓
PATCH /v1/projects/<project_id>/publication
  ↓
routes/projects_api.py
  ↓
project_publication_service.py
  ↓
ProjectEmbedPolicy / metadata / settings defensiv aktualisieren
  ↓
Audit-Event
```

Publizierbare Workspaces:

```text id="1okut9"
project
map
editor3d
cad2d
lv
versions
```

Nie publizierbar:

```text id="543tkc"
admin
team
settings
permissions
system
```

---

## 40. Aktueller Team-/Invitation-Flow

Projektverwalter öffnet Team-Bereich:

```text id="yz0qt9"
Browser
  ↓
/project=<project_public_id>
  ↓
Projekt-Workspace
  ↓
project_team.html
  ↓
project_team.js
  ↓
GET /v1/projects/<project_id>/members
GET /v1/projects/<project_id>/invitations
```

Einladung:

```text id="p1ocxx"
User gibt E-Mail + Rolle ein
  ↓
POST /v1/projects/<project_id>/invitations
  ↓
project_invitation_service.py
  ↓
AuthIdentityClient prüft E-Mail
  ↓
nur wenn registriert:
  ProjectInvitation wird erstellt
  optional dispatch
  Audit-Event
```

Nicht erlaubt:

```text id="lnnv91"
Demo-Kontext
Owner per Einladung
unregistrierte E-Mail
echte Useraccount-Erstellung in vectoplan-app
```

---

## 41. Aktueller Workspace-State-Flow

```text id="hfrd4v"
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

```text id="9rh6ua"
mode
workspace_mode
viewer_selection
workspace_selection
last_*_selection
last_*_hover
last_workspace_error
```

Nicht gespeichert werden sollen:

```text id="znezxv"
speckle_*
legacy_viewer*
viewer_url
model_id
version_id
chunk snapshot internals
```

---

## 42. Aktuelle Tests / Smoke-Checks

### 42.1 App-Start

Erwartet:

```text id="mr3kf9"
Gunicorn startet.
Worker booten.
Keine ConversationState AttributeError mehr.
```

Noch möglich:

```text id="c23q3c"
blueprint state_api_bp unavailable; skipped
```

Diese Warnung ist aktuell kein Blocker.

---

### 42.2 Chunk-Status

Erwartet:

```text id="f580c5"
GET /projects/_status → 200
GET /chunks/_status   → 200
```

Chunk-Readiness:

```text id="ftc2el"
schemaReady = true
seedReady = true
defaultProjectReady = true
defaultUniverseReady = true
defaultWorldReady = true
```

---

### 42.3 App↔Chunk-Provisioning

Erwartet:

```text id="afsw84"
PUT /projects/by-app/<app_project_public_id> → 201 oder 200
```

Je nach Zustand:

```text id="s5o3r7"
201 = neu erstellt
200 = bereits vorhanden / idempotent zurückgegeben
```

---

### 42.4 Editor↔Chunk

Erwartet:

```text id="gv7tsi"
POST /projects/<chunk_project_id>/worlds/world_spawn/chunks/batch → 200
```

---

### 42.5 3D-Editor über App-Shell

Erwartet:

```text id="8f6owk"
GET /project=<project_public_id>
  ↓
3D klicken
  ↓
iframe lädt /ui/project/<project_public_id>/editor3d
  ↓
302 auf http://localhost:5100/editor?embed=1&...
  ↓
Editor wird im iframe sichtbar
```

Nicht mehr erwartet:

```text id="q7t7j9"
generischer Workspace-Platzhalter im 3D-Reiter
/ui/project/<id>/editor als primärer 3D-Pfad
http://vectoplan-editor:5000 im Browser
```

---

### 42.6 Viewer-Selection-State

Erwartet:

```text id="6dop4m"
PUT /v1/chats/<chat_id>/viewer/selection → 200
```

Nicht mehr erwartet:

```text id="8zp19g"
ConversationState.merge_patch failed
ConversationState fallback merge failed
AttributeError: ConversationState has no attribute merge_patch
AttributeError: ConversationState has no attribute get_or_create
```

---

### 42.7 Projektformular

Erwartet:

```text id="vb9hbw"
sichtbares Feld address_text
keine sichtbaren Einzeladressfelder
keine sichtbaren Koordinatenfelder
kein sichtbarer Systemreferenzbereich
```

Payload:

```text id="9ib1ba"
name
description
address_text
visibility
```

---

### 42.8 Publication

Erwartet:

```text id="z2go7a"
GET /v1/projects/<project_id>/publication → 200
PATCH /v1/projects/<project_id>/publication → 200
```

Nicht erwartet:

```text id="qvfjmm"
Admin/Team/Settings werden public sichtbar
private Projekt-Workspaces werden trotz private effektiv veröffentlicht
```

---

### 42.9 Invitations

Erwartet:

```text id="7bad7k"
GET /v1/projects/<project_id>/invitations → 200
POST /v1/projects/<project_id>/invitations → 200 oder kontrollierter 4xx
DELETE /v1/projects/<project_id>/invitations/<id> → 200
```

Kontrollierte Ablehnungen:

```text id="1uljin"
Demo-Kontext
fehlende manage/team-Berechtigung
unregistrierte E-Mail
bereits bestehendes Mitglied
duplizierte Pending-Einladung
Owner-Rolle per Einladung
```

---

## 43. Offene Punkte

### 43.1 `app.py` weiter prüfen

Noch sinnvoll:

```text id="echzsb"
Blueprint-Registrierung final prüfen
state_api_bp Warnung entfernen oder sauber hinter Feature-Flag legen
alte Chat-/Speckle-Routen hinter Feature-Flags legen
globale CSP final abstimmen
Dev-Reset-Option prüfen
```

Aktueller wichtiger Stand:

```text id="mn91oj"
ui_projects_bp muss vor viewer_bp registriert sein,
damit /project=<id> die Shell rendert.
viewer_bp liefert danach /ui/project/... iframe-Workspaces.
```

---

### 43.2 Route-Namen vereinheitlichen

Aktuell existieren noch Chat-Begriffe in Pfaden und Dateien.

Später:

```text id="nsw5sy"
chat_viewer.html → app_shell.html
static/js/chat/main.js → static/js/shell/main.js
routes/ui/chat.py → entfernen oder legacy_redirects.py
```

---

### 43.3 Conversation-Abhängigkeit reduzieren

Aktuell existiert `conversation_id` noch in mehreren Kontexten.

Später prüfen:

```text id="ljpmeu"
Projekt-State direkt an Project binden
Chat-State-Routen entfernen
Conversation nur behalten, wenn fachlich benötigt
viewer_selection.py langfristig in workspace_state.py überführen
```

---

### 43.4 Alte Chat-Module entfernen

Prüfen:

```text id="n89zut"
routes/chat/
static/js/chat/composer.js
static/js/chat/transcript.js
static/js/chat/layout.js
static/js/chat/uploads.js
```

Wenn sie nicht mehr geladen werden, können sie später entfernt oder archiviert werden.

---

### 43.5 Versionierung finalisieren

Aktuell gibt es `ProjectVersion` als neue zentrale Tabelle.

Noch zu tun:

```text id="b07a0h"
alte transcript-basierte Versionierung entfernen
Versionen vollständig auf ProjectVersion umstellen
Service-Artefakte sauber referenzieren
Chunk-/2D-/LV-Snapshots als Referenzen einbinden
```

---

### 43.6 Service-Links produktiv anbinden

Aktuell sind `ProjectServiceLink` und Referenzfelder vorbereitet.

Teilweise erreicht:

```text id="g1ixkz"
chunk_project_id automatisch aus Chunk-Service
chunk_universe_id automatisch aus Chunk-Service
chunk_world_id automatisch aus Chunk-Service
```

Noch zu tun:

```text id="klm9ys"
plan2d_id aus 2D-Service setzen
lv_id aus LV-Service setzen
OpenLayer-Layer/Dataset verknüpfen
Library-Asset-Referenzen verknüpfen
Service-Link-Status regelmäßig validieren
```

---

### 43.7 App-Schema sauber stabilisieren

Bei lokalen DB-Altständen können Schemafehler auftreten.

Noch sinnvoll:

```text id="llhak5"
App-DB-Initialisierung prüfen
fehlende App-Spalten erkennen
Entscheidung: Dev-Repair-Script oder weiterhin docker compose down -v
keine stillen Runtime-Mutationen in produktiver Runtime
```

Wichtig:

```text id="6p9oan"
Der zuvor angedachte schema_contract.py-Pfad wurde nicht umgesetzt.
```

---

### 43.8 Editor-Kontextverarbeitung finalisieren

Aktuell baut die App den 3D-Embed mit Parametern wie:

```text id="ukma1b"
app_project_public_id
project_public_id
context_url
chunk_project_id
chunk_universe_id
chunk_world_id
```

Noch zu prüfen:

```text id="it38l0"
liest vectoplan-editor context_url vollständig aus?
priorisiert editor app_project_public_id korrekt?
lädt editor immer world_spawn?
funktioniert read_only bei Public/Demo-Kontext?
```

---

### 43.9 Public/Unlisted Ende-zu-Ende testen

Noch zu testen:

```text id="febr4o"
public Projekt ohne Login
unlisted Projekt ohne Listing
published project workspace
published map workspace
published editor3d workspace
nicht veröffentlichte Workspaces blockiert
Admin/Team/Settings immer blockiert
```

---

## 44. Was aktuell als stabil gilt

Stabil genug für nächsten Entwicklungsstand:

```text id="mb1hcr"
Projekt-Shell
Projekt-Sidebar
Projektformular
vereinfachtes address_text Formular
Projekt-API
modulare App-Models
Current-User-/Demo-Kontext
Projekt-Berechtigungen
Embed-Policy-Grundlage
Publication-Service-Grundlage
ProjectInvitation-Grundlage
ProjectServiceLink-Grundlage
ProjectVersion-Grundlage
ProjectAuditEvent-Grundlage
App↔Chunk-Provisioning-Grundlage
Editor↔Chunk world_spawn Flow
3D-Gateway über /ui/project/<id>/editor3d
workspace_embed_service.py
ConversationState-Kompatibilität
```

Nicht final, aber funktionsfähig:

```text id="h1ctmc"
Legacy-State-Routen
alte Chat-Kompatibilität
alte Versionierungsrouten
LV-Platzhalter
2D-Gateway
OpenLayer-Gateway
AuthIdentityClient im Dev-/Placeholder-Modus
Einladungsdispatch als Placeholder
Publication-Ende-zu-Ende Public-Flow
```

---

## 45. Gesamtfazit

Die `vectoplan-app` ist jetzt deutlich näher am gewünschten Architekturziel.

Vorher:

```text id="25skl8"
Chat-Shell mit Workspace
sichtbarer VectoAI-Chat
3D/Map stark aus Chat-Kontext gedacht
Models noch zentral/monolithisch
keine stabile App↔Chunk-Provisioning-Kette
ConversationState API-Mismatch
3D-Reiter konnte Platzhalter statt Editor zeigen
Projektformular war zu technisch
```

Jetzt:

```text id="cvc30q"
Projekt-Shell mit Workspace
sichtbarer Chat entfernt
Projektliste links
Workspace rechts
Projekt-API vorhanden
modulare Models vorhanden
Projekt-Berechtigungen vorhanden
Demo-/Auth-Kontext vorbereitet
Einladungsmodell vorbereitet
Publication-Service vorbereitet
Service-Link-/Version-/Audit-Struktur vorhanden
Map/3D/2D/LV projektgeführt
3D öffnet über App-Gateway /ui/project/<id>/editor3d
workspace_embed_service.py baut Public-Editor-Ziel
App↔Chunk-Provisioning funktioniert
Chunk world_spawn ist korrekt angebunden
Editor lädt Chunks aus world_spawn
ConversationState-Kompatibilität repariert
```

Der wichtigste aktuelle Einstieg ist:

```text id="cq56u7"
http://localhost:5103/
```

und für ein Projekt:

```text id="m3vhh4"
http://localhost:5103/project=<project_public_id>
```

Der neue Kern der App ist nicht mehr Chat, sondern:

```text id="x9edcm"
Project
ProjectMembership
ProjectInvitation
ProjectEmbedPolicy
ProjectServiceLink
ProjectVersion
ProjectAuditEvent
Chunk-Referenzen
Workspace-State
Publication
Workspace-Gateway
```

Nächster sinnvoller technischer Schritt:

```text id="tocjbi"
app.py und verbleibende Legacy-Routen prüfen
state_api_bp-Warnung bereinigen
```

Danach:

```text id="swdp2g"
alte Chat-/Speckle-/Transcript-Pfade systematisch entfernen oder hinter Feature-Flags legen
```

---

## 46. Ordner- und Filestruktur als Übersicht

Die `vectoplan-app` ist aktuell in Schichten aufgebaut. Die App selbst ist die Projekt-, Portal- und Workspace-Shell. Fachliche Daten wie 3D-Welt, Chunk-State, CAD-Geometrie oder LV-Inhalte liegen nicht in der App, sondern in den jeweiligen Microservices.

---

### 46.1 Architektur-Schema

```text id="xzbvjj"
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
  │    ├─ routes/viewer.py
  │    ├─ templates/chat_viewer.html
  │    ├─ templates/partials/project_sidebar.html
  │    ├─ templates/partials/demo_banner.html
  │    ├─ templates/viewer/project.html
  │    ├─ templates/viewer/partials/project_address.html
  │    ├─ templates/viewer/partials/project_visibility.html
  │    ├─ templates/viewer/partials/project_publication.html
  │    ├─ templates/viewer/partials/project_team.html
  │    ├─ static/css/chat.css
  │    ├─ static/css/project_sidebar.css
  │    ├─ static/css/project_workspace.css
  │    ├─ static/js/chat/main.js
  │    ├─ static/js/chat/project_sidebar_data.js
  │    ├─ static/js/chat/project_sidebar_resize.js
  │    ├─ static/js/chat/project_sidebar.js
  │    ├─ static/js/project/project_form.js
  │    ├─ static/js/project/project_publication.js
  │    └─ static/js/project/project_team.js
  │
  ├─ API-Schicht
  │    ├─ routes/projects_api.py
  │    ├─ routes/viewer.py
  │    └─ routes/viewer_selection.py
  │
  ├─ Service-Schicht
  │    ├─ services/current_user.py
  │    ├─ services/auth_identity_client.py
  │    ├─ services/project_permissions.py
  │    ├─ services/project_service.py
  │    ├─ services/project_invitation_service.py
  │    ├─ services/project_publication_service.py
  │    ├─ services/workspace_embed_service.py
  │    └─ services/chunk_client.py
  │
  ├─ Model-Schicht
  │    ├─ models/base.py
  │    ├─ models/users.py
  │    ├─ models/legacy.py
  │    ├─ models/projects.py
  │    ├─ models/project_access.py
  │    ├─ models/project_invitations.py
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
       ├─ vectoplan-library     → Assets/Inventory
       └─ zukünftiger Auth-Service → Login/Registrierung/Account
```

---

### 46.2 Vereinfachter Runtime-Flow

```text id="uj5dvb"
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
iframe src = /ui/project/new/project
        oder /ui/project/<project_public_id>/project
        oder /ui/project/<project_public_id>/editor3d
        oder /ui/project/<project_public_id>/map
        oder /ui/project/<project_public_id>/cad2d
        oder /ui/project/<project_public_id>/lv
        oder /ui/project/<project_public_id>/versions
        oder /ui/project/<project_public_id>/admin
```

---

### 46.3 Workspace-Routing als Schema

```text id="yucag1"
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
/ui/project/<project_public_id>/editor3d
  ↓
routes/viewer.py
  ↓
workspace_embed_service.py
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

Toolbar "Versionen"
  ↓
/ui/project/<project_public_id>/versions
oder Versionen-Dropdown

Toolbar "Admin"
  ↓
/ui/project/<project_public_id>/admin
```

---

### 46.4 API-/Service-/Model-Fluss

```text id="0nb0f5"
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

### 46.5 Berechtigungsfluss

```text id="5mnlyk"
Route braucht Zugriff
  ↓
project_permissions.py
  ↓
1. Ist Projekt gelöscht?
2. Ist User owner_user_id?
3. Hat User ProjectMembership?
4. Ist Projekt public/unlisted und Workspace veröffentlicht?
5. Ist Admin/Team/Settings/System betroffen?
  ↓
PermissionResult / WorkspaceAccessResult
  ↓
Route erlaubt oder liefert kontrollierten Fehler
```

---

### 46.6 Datenfluss zwischen Projektformular und Shell

```text id="w9yph9"
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

### 46.7 Externe Services im Verhältnis zur App

```text id="k3ktde"
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
  ├─ öffnet /ui/project/<id>/editor3d
  │      └─ Browser-Public-Route zu vectoplan-editor
  │
  └─ öffnet /ui/project/<id>/map
         └─ Browser-Public-Route zu vectoplan-openLayer
```

---

### 46.8 Was im aktuellen Projektfluss primär ist

Primär:

```text id="lcr92y"
routes/ui/projects.py
routes/viewer.py
routes/projects_api.py
routes/viewer_selection.py
services/project_service.py
services/project_permissions.py
services/current_user.py
services/auth_identity_client.py
services/project_invitation_service.py
services/project_publication_service.py
services/workspace_embed_service.py
services/chunk_client.py
models/
templates/chat_viewer.html
templates/partials/project_sidebar.html
templates/partials/demo_banner.html
templates/viewer/project.html
templates/viewer/partials/project_*.html
static/js/chat/main.js
static/js/chat/project_sidebar*.js
static/js/project/project_form.js
static/js/project/project_publication.js
static/js/project/project_team.js
static/css/chat.css
static/css/project_sidebar.css
static/css/project_workspace.css
```

Nicht mehr primär:

```text id="ltemvv"
routes/ui/chat.py
routes/chat/
alte Chat-Composer-Module
alte Transcript-Module
Speckle-Routen
alte Viewer-Routen
/ui/project/<id>/editor als Hauptpfad
```

---

### 46.9 Spätere Umbenennung zur besseren Lesbarkeit

Aktuell funktionieren diese Namen, sind aber historisch:

```text id="jxwxdo"
templates/chat_viewer.html
static/css/chat.css
static/js/chat/main.js
static/js/chat/project_sidebar_data.js
static/js/chat/project_sidebar_resize.js
static/js/chat/project_sidebar.js
```

Später lesbarer:

```text id="gfkrgx"
templates/app_shell.html
static/css/app_shell.css
static/js/shell/main.js
static/js/project_sidebar/data.js
static/js/project_sidebar/resize.js
static/js/project_sidebar/sidebar.js
```

Die Umbenennung ist nicht dringend, aber sinnvoll, sobald der Projektfluss stabil ist.

---

## 47. Abschlussstand

Aktueller Status:

```text id="l57xzp"
vectoplan-app startet.
Projekt-Shell ist aktiv.
Projektformular ist vereinfacht.
address_text ist das sichtbare Adressfeld.
Projekt-Sichtbarkeit private/unlisted/public ist vorbereitet.
Workspace-Veröffentlichung ist vorbereitet.
Team-/Invitation-UI ist vorbereitet.
Demo-/Auth-Kontext ist vorbereitet.
Chunk-Provisioning funktioniert.
Editor lädt world_spawn aus Chunk.
3D-Reiter öffnet über /ui/project/<id>/editor3d den echten Editor.
ConversationState-Fehler ist repariert.
```

Aktuell noch nicht final:

```text id="d1gywr"
state_api_bp Warnung
Legacy-Chat-Routen
Speckle-/Altviewer-Routen
alte Versionierungsreste
Namensbereinigung chat_* → shell_*
echter Auth-/Registrierungsdienst
echter Einladungsversand
vollständiger Public/Unlisted-Ende-zu-Ende-Test
produktive 2D-/LV-Serviceintegration
```

Empfohlener nächster Schritt:

```text id="o5809f"
services/vectoplan-app/app.py prüfen
```

Ziel:

```text id="n5a3ep"
Blueprint-Registrierung final verstehen
state_api_bp Warnung entfernen oder bewusst dokumentieren
Legacy-Routen sauber hinter Feature-Flags legen
CSP/Frame-Ancestors final stabilisieren
```

Danach:

```text id="e4jwah"
Public/Unlisted-Flow testen
Invitation-Flow mit echtem Auth-Service testen
2D/LV-Gateways produktiv anbinden
alte Chat-/Speckle-Reste entfernen
```

---

## 48. Fertigstellung dieser IST-Aktualisierung

Diese IST-Aktualisierung wurde in drei Teilen neu gegliedert:

```text id="12swwk"
Teil 1:
  Zweck
  Kurzbefund
  Kernprobleme
  Service-Rollen
  Architekturentscheidung
  UI-Fluss
  Projekt-Konfiguration
  3D-Flow
  App↔Chunk
  Models
  Services
  Routes
  Templates
  Static

Teil 2:
  Ordner-/Filestruktur
  Endpoints
  Projekt-Beispiele
  Publication
  Invitations
  Workspace-State
  Frontend-Eventfluss
  UI-Zielzustand
  Runtime-Hinweise

Teil 3:
  Altlasten
  Behalten/Umbenennen/Entfernen
  Ziel-Flows
  Tests
  Offene Punkte
  Stabilitätsbewertung
  Fazit
  Architekturübersicht
  Abschlussstand
```

Damit ist die Aktualisierung der `IST-Zustand-vectoplan-app.md` vollständig.
