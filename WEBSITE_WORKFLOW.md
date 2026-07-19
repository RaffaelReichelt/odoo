# Website-Content: Workflow & Deployment

Handreichung für `addons/18.0/privatemind_website`. Basiert auf den Erkenntnissen aus der
Übersetzungs-/Text-Aufräumaktion (Juli 2026) — siehe Abschnitt 3 für die konkreten Fallstricke,
die dort aufgetreten sind.

## 1. Texte ändern oder neu erstellen

### Grundprinzip

Englisch ist die **Quellsprache**. Alle Templates in `views/*.xml` sind auf Englisch geschrieben.
Deutsch kommt **ausschließlich** aus `i18n/de.po` (msgid = exakter englischer Text aus der XML,
msgstr = deutsche Übersetzung). Es gibt keine separate deutsche Template-Datei.

### 1.1 Bestehenden Text ändern

1. Englischen Text in der passenden Datei unter `views/*.xml` ändern (falls sich der englische
   Text selbst ändert).
2. Passenden Eintrag in `i18n/de.po` suchen/anpassen. **Der `msgid` muss exakt (Zeichen für
   Zeichen, inkl. Zeilenumbrüche/Einrückung bei mehrzeiligen Strings) mit dem Text aus der XML
   übereinstimmen** — sonst greift die Übersetzung nicht.
3. Modul aktualisieren (siehe Befehls-Spickzettel unten). **Immer mit `--i18n-overwrite`**,
   sonst werden bestehende (ggf. veraltete) Übersetzungen NICHT überschrieben.
4. Live auf Englisch UND Deutsch prüfen (`/pfad` und `/de/pfad`).

### 1.2 Neuen Text / neue Seite hinzufügen

1. Neues `<template>` in `views/*.xml` (oder neue Datei) anlegen, auf Englisch.
2. Neue Datei ggf. in `__manifest__.py` unter `data` eintragen.
3. Für jeden übersetzbaren Textblock einen Eintrag in `i18n/de.po` ergänzen (msgid = EN,
   msgstr = DE). Reihenfolge im File ist grob alphabetisch nach msgid, aber nicht zwingend.
4. Modul aktualisieren mit `--i18n-overwrite`, live prüfen.

### 1.3 Harte Regeln

- **Niemals direkt über den Website-Builder / "Bearbeiten"-Modus im Browser** Texte auf Seiten
  ändern, die zu diesem Modul gehören (Homepage, Products, About, Privacy, Impressum, Contact).
  Solche Änderungen landen nur in der Datenbank, nicht in Git — und gehen beim nächsten
  `--i18n-overwrite` oder bei einer frischen Installation verloren bzw. driften unbemerkt vom
  Quelltext auseinander (das ist genau das Problem, das wir bei der Datenschutzerklärung gefunden
  haben, siehe unten).
- **Niemals `arch_db` direkt per SQL/ORM-Skript patchen**, außer als Notfall-Sofortmaßnahme —
  und dann **immer im Anschluss** einen passenden `.po`-Eintrag ergänzen, sonst wird der Patch
  beim nächsten Modul-Update wieder verworfen.
- Bei Unsicherheit, ob ein Text schon (korrekt) übersetzt ist: direkt in der laufenden DB
  nachsehen ist zuverlässiger als nur die Dateien zu lesen (siehe `--i18n-export` Trick unten).

### 1.4 Befehls-Spickzettel

Container-Namen können je nach Host variieren (`docker ps --filter name=_odoo` zum Finden).

```bash
# Modul aktualisieren (IMMER mit --i18n-overwrite bei Text-/Übersetzungsänderungen)
CID=$(docker ps -q -f name=odoo_odoo)
docker exec -i "$CID" python3 /usr/src/odoo/community/odoo-bin \
  --config=/tmp/odoo.conf -d odoo -u privatemind_website --i18n-overwrite --stop-after-init

# Aktuellen Stand der deutschen Übersetzungen aus der DB exportieren
# (nützlich um zu prüfen, was die DB WIRKLICH gerade anzeigt, unabhängig vom .po-File,
#  und um versehentlich nur-in-der-DB vorhandene Übersetzungen ins Git zu retten)
docker exec -i "$CID" python3 /usr/src/odoo/community/odoo-bin \
  --config=/tmp/odoo.conf -d odoo -l de_DE \
  --i18n-export=/tmp/export_de.po --modules=privatemind_website --stop-after-init
docker cp "$CID":/tmp/export_de.po /tmp/export_de.po

# Live-Check ohne Browser-Cache-Risiko (direkt aus dem Container heraus)
docker exec "$CID" python3 -c "
import urllib.request
req = urllib.request.Request('http://localhost:8069/de/PFAD', headers={'Host': 'ZIELHOST'})
html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8','ignore')
print('gesuchter Text' in html)
"
```

## 2. Stage → Produktiv übertragen, ohne Geschäftsdaten zu berühren

### Mentales Modell: Code vs. Daten

| | Stage (gx10) | Produktiv (strato) |
|---|---|---|
| **Code** (`addons/18.0/privatemind_website`, `docker-compose-*.yml`, ...) | Git-Checkout auf gx10-Host | **eigener** Git-Checkout auf strato-Host (`/opt/stacks/privatemind/odoo/...`) |
| **Datenbank** (Bestellungen, Kunden, Rechnungen, Website-Inhalte die nur per Builder erstellt wurden, Sessions) | eigene Postgres-Instanz, Testdaten | **eigene** Postgres-Instanz mit echten Geschäftsdaten |

Die beiden Datenbanken sind komplett getrennt und **dürfen niemals** ineinander kopiert werden
(kein `pg_dump`/`pg_restore` von Stage nach Produktiv, kein Kopieren von `data/filestore`).
Nur **Code** wandert von Stage nach Produktiv — über Git, nicht über die Datenbank.

### Was "wandert" beim Deploy automatisch mit, was nicht

- ✅ Alles, was in `views/*.xml`, `i18n/de.po`, `__manifest__.py`, `controllers/` steht:
  kommt automatisch korrekt an, sobald auf Produktiv `git pull` + Modul-Update laufen.
- ❌ Alles, was nur über den Website-Builder in der Stage-DB erstellt/geändert wurde: kommt
  **nicht** automatisch mit. Das ist der Grund, warum wir bei der Datenschutzerklärung erst
  den `--i18n-export`-Trick brauchten, um den Text überhaupt ins Git zu bekommen — sonst wäre er
  auf Produktiv schlicht nicht vorhanden gewesen.
- ❌ Neue Bilder/Assets, die per Backend hochgeladen wurden (landen nur im Stage-Filestore).
  Für dauerhafte Assets: als statische Datei im Modul (`static/src/img/...`) ablegen statt
  hochzuladen.

### Ablauf für einen Deploy Stage → Produktiv

1. Auf Stage/lokal: Änderungen fertig, getestet, committet (`git commit`).
2. `git push` zum Remote-Repo.
3. Auf dem **strato-Host** (per SSH): `git pull` im dortigen Checkout unter
   `/opt/stacks/privatemind/odoo/`.
4. Auf dem strato-Host das Modul aktualisieren — **derselbe Befehl** wie oben, nur gegen den
   dortigen `odoo`-Container/Datenbank:
   ```bash
   CID=$(docker ps -q -f name=odoo_odoo)   # Container-Name auf strato prüfen, kann abweichen
   docker exec -i "$CID" python3 /usr/src/odoo/community/odoo-bin \
     --config=/tmp/odoo.conf -d odoo -u privatemind_website --i18n-overwrite --stop-after-init
   ```
   Das Modul-Update fasst **ausschließlich** die Datensätze dieses Moduls an (Views, Übersetzungen,
   Menüs, Sicherheitsregeln von `privatemind_website`) — Bestellungen, Kunden, Rechnungen etc.
   gehören zu anderen Modulen (`sale`, `account`, ...) und werden dabei nicht berührt.
5. Live auf der echten Domain gegenprüfen (EN + DE).

> Diesen SSH-/Deploy-Ablauf zum strato-Host habe ich noch nicht 1:1 verifiziert (kein Zugriff in
> dieser Session) — Container-Name und genauer `git pull`-Pfad ggf. anpassen. Wenn du mir einmal
> zeigst, wie ihr aktuell auf strato deployt, ergänze ich das hier exakt.

### Vor jedem Produktiv-Deploy checken

- [ ] Änderung wurde vorher auf Stage (`odoo-stage.tail5a2ccd.ts.net`) sichtbar getestet
- [ ] Kein Schritt kopiert die Datenbank oder den Filestore von Stage nach Produktiv
- [ ] Bei Text-/Übersetzungsänderungen: `--i18n-overwrite` im Update-Befehl auf strato dabei
- [ ] Nach dem Update: Stichprobe auf der echten Domain (nicht nur Stage) — Browser-Hard-Reload
      nicht vergessen (Cmd+Shift+R)

## 3. Bekannte Fallstricke (aus dieser Session)

- **`-u` ohne `--i18n-overwrite`** aktualisiert Views/Struktur, lässt aber bestehende (veraltete)
  Übersetzungen unangetastet stehen. Wirkt wie "meine Textänderung kommt nicht an", ist aber nur
  ein fehlendes Flag.
- **Website-Builder-Bearbeitungen** erzeugen Inhalte, die nur in der DB existieren und vom
  Moduldateien-Quelltext komplett entkoppelt sind (COW-Mechanismus von Odoo). Führt zu doppelten
  `website.page`-Einträgen für dieselbe URL, wobei die website-spezifische Kopie Vorrang hat und
  "unsichtbar" den aktuellen, modul-gepflegten Inhalt blockiert.
- **Direkte DB-Patches** (z. B. schnelles Fixen einer Mischsprachen-Zeile per Shell) werden vom
  nächsten `--i18n-overwrite` wieder verworfen, wenn kein passender `.po`-Eintrag existiert.
