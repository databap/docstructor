Dokumentation: Dokumentations-Governance-Tool
**Produktname**: **Docstructor**
**Version**: 2.2 — Production Ready mit stabiler CLI/API  
**Datum**: 20. Mai 2026  
**Status**: 🔄 Teststatus siehe aktuellen CI-/pytest-Run

---

## 🚀 Neu in v2.1: Bug-Fixes für Produktion

Die folgenden **8 kritischen Bugs** wurden behoben:

| # | Bug | Severity | Status |
|---|-----|----------|--------|
| **1** | Root-File-Scan nur *.md | HOCH | ✅ Fixed: `_get_root_files()` scannt ALLE Dateien |
| **2** | allow_nested_categories doppelt definiert | MITTEL | ✅ Fixed: `_allows_nested_categories_in()` nutzt Category-Flag |
| **3** | Unicode-Normalisierung fehlt | HOCH | ✅ Fixed: `unicodedata.normalize('NFC')` in Language-Check |
| **4** | Performance: Komplette Dateien gelesen | MITTEL | ✅ Fixed: MAX_LANG_VALIDATION_BYTES = 8192 |
| **5** | archive ist kein echtes Container | MITTEL | ✅ Fixed: `_validate_archive_container()` + is_container Flag |
| **6** | Dry-run CLI verwirrend | MITTEL | ✅ Fixed: Subcommands `validate/plan/apply/report` + Legacy `--apply` |
| **7** | Locking nicht atomar | HOCH | ✅ Fixed: `os.O_EXCL` statt `write_text()` |
| **8** | Rollback inkonsistent | MITTEL | ✅ Fixed: `_precheck_move()` prüft source + target |

### Testabdeckung
- ✅ `test_collision_in_archive_category` — Bug #8
- ✅ `test_invalid_yaml` — Error handling
- ✅ `test_rollback_on_move_error` — Bug #8
- ✅ `test_root_file_to_archive_category` — Bug #1
- ✅ `test_root_file_to_archive_default` — Bug #1
- ✅ `test_root_file_to_archive_reviews` — Bug #1
- ✅ `test_allowed_root_file` — Bug #1
- ✅ `test_nested_category_in_archive` — Bug #2
- ✅ `test_git_overwrite_uses_git_mv_force_or_fails_without_shutil_fallback` — Git-Policy
- ✅ `test_git_mv_no_overwrite_still_has_no_shutil_fallback_on_error` — Git-Policy
- ✅ `test_archive_error_is_container` — Container-Semantik
- ✅ `test_archive_is_never_target_category` — Container-Schutz

---

## Operator Quick Reference (Docstructor)

# 1) Standardprüfung (empfohlen vor Commit)
python scripts/validate_docs_structure.py validate --projects foo foo-mcp-server

# 2) Plan anzeigen (Violations + geplante Moves)
python scripts/validate_docs_structure.py plan --projects foo

# 3) Reorganisation anwenden (nur bewusst)
python scripts/validate_docs_structure.py apply --projects foo

# 4) CI-/MCP-Ausgabe als JSON
python scripts/validate_docs_structure.py validate --projects foo --json --ci

# 5) Report explizit speichern
python scripts/validate_docs_structure.py report --projects foo --save-report

# 6) Nur geänderte docs-Dateien prüfen (git diff --name-only HEAD)
python scripts/validate_docs_structure.py validate --projects foo --changed-only

# 7) Strukturierte Laufzeitdaten als JSON-Datei schreiben
python scripts/validate_docs_structure.py validate --projects foo --log-json docs/.doc_tool.runtime.json

# 8) Rules-Schema-Validierung überspringen (nur für Debug/Recovery)
python scripts/validate_docs_structure.py validate --projects foo --skip-rules-schema

# Optional: zusätzliche Projekt-Suchpfade für relative --projects Angaben
export DOC_VALIDATOR_PROJECTS_ROOTS="/workspace:/opt/repos"


Hinweis:

Bei Parallelstart blockiert .doc_tool.lock die zweite Instanz.

Bei Fehlern in Subcommand `apply` versucht das Tool ein automatisches Rollback.

Exit-Codes:
- `0` = keine Violations, keine harten Fehler
- `1` = Violations gefunden, Tool lief technisch sauber
- `2` = harte Fehler (z. B. Security/Lock/Rules-Load/Apply)

Rules-Schema-Verhalten:
- `docs/STRUCTURE_RULES.yaml` wird beim Laden gegen `docs/process/VALIDATE_DOCS_JSON_SCHEMA.json` validiert.
- Bei Schema-Verletzung liefert das Tool einen harten Fehler (`error_type = rules_schema_invalid`, Exit-Code `2`).
- Für Notfälle kann die Schema-Prüfung mit `--skip-rules-schema` deaktiviert werden.

Mini-Troubleshooting
1) Fehler: "Lock existiert bereits"

Ursache:

Eine zweite Tool-Instanz läuft noch oder wurde unerwartet beendet.

Lösung:

rm -f /path/to/project/.doc_tool.lock


Danach den Validator erneut starten.

2) Fehler: "SecurityError" (Pfad außerhalb project_root)

Ursache:

--projects oder --rules-file zeigt auf einen Pfad außerhalb der erlaubten Projektgrenze.

Lösung:

Nur Projektpfade innerhalb des vorgesehenen Workspace-Roots verwenden.

--rules-file auf die lokale docs/STRUCTURE_RULES.yaml setzen.

3) Fehler: "Rollback unvollständig"

Ursache:

Während --apply trat ein Fehler auf und mindestens ein Rückverschieben schlug fehl.

Lösung:

git status prüfen.

Betroffene Pfade manuell korrigieren.

Danach erneut mit `validate` prüfen.

Safe Recovery Playbook (5 Befehle)
# 1) In Projekt wechseln
cd /path/to/foo

# 2) Hängende Lock-Datei entfernen
rm -f .doc_tool.lock

# 3) Aktuellen Zustand prüfen
git status --short

# 4) Validator im sicheren Modus laufen lassen
python scripts/validate_docs_structure.py validate --projects foo

# 5) Erst dann bewusst anwenden
python scripts/validate_docs_structure.py apply --projects foo

1. Zweck und Scope

Dieses Tool prüft die Dokumentationsstruktur und Governance-Regeln eines Projekts auf Basis einer deklarativen Konfiguration in docs/STRUCTURE_RULES.yaml.

Es ist zuständig für:

Strukturvalidierung (Ordner, Root-Dateien, optional Case-Regeln)

Kategorisierung per Dateinamen-Pattern

Sprach-Governance per Bereichs-Scope (z. B. adr=de, deployment=en)

Sichere Reorganisation (optional, mit Rollback)

Nicht im Scope:

Inhaltliche Fachqualität der Doku

Semantisches NLP-Scoring

2. Architekturüberblick

Einstiegspunkt ist scripts/validate_docs_structure.py.

Ablauf:

Projektpfade auflösen und Sicherheitsgrenzen prüfen.

Lock-Datei pro Projekt setzen (.doc_tool.lock).

Regeln laden (explizite Rule-Datei oder lokal in docs/).

Struktur- und Sprachvalidierung ausführen.

Optional Report schreiben.

Optional Reorganisation anwenden (Subcommand `apply`) mit Rollback bei Fehlern.

Lock in finally wieder entfernen.

3. Kernfunktionen
A) Strukturvalidierung

required_directories: prüft Pflichtordner.

allowed_root_files: prüft erlaubte Root-Dateien in docs/.

require_lowercase_directories: prüft lowercase-Verzeichnisnamen.

allow_nested_categories: verbietet/erlaubt Unterordner in Kategorieordnern.

case_sensitive: steuert Dateiname/Pattern-Vergleich.

B) Kategorisierung

Regeln aus categories.<name>.file_patterns.

Matching erfolgt auf Dateinamen (glob), nicht auf kompletten Pfaden.

Verwendet für Vorschläge bei falsch platzierten Root-Dateien.

C) Sprach-Governance

Default-Sprache: validation.language.

Bereichsspezifisch: validation.language_scope (__root__, adr, deployment, ...).

Sprachheuristik: validation.language_rules.<lang> mit markers, min_chars, min_marker_hits.

Scope-Werte any|none|skip deaktivieren Sprachprüfung für den Bereich.

Wichtig:

Die Sprachprüfung ist heuristisch (Marker-basiert), nicht NLP-basiert.

Es wird auf Mindesttreffer geprüft; keine semantische Gesamtklassifikation.

4. Security, Integrität, Robustheit
A) Path Traversal Prevention

Alle kritischen Pfade werden mit Path.resolve() gehärtet.

Jede zu lesende/zu bewegende Datei muss innerhalb des project_root liegen.

Prüfung erfolgt mit relative_to-basierter is_relative_to-Logik.

B) Locking gegen Parallelbetrieb

Pro Projekt wird .doc_tool.lock erzeugt.

Existiert die Datei bereits, bricht das Tool sofort ab.

Entfernen der Lock-Datei erfolgt in finally.

C) Git-Fallback

Wenn .git vorhanden ist: bevorzugt git mv.

Wenn kein Git-Repo: automatischer Fallback auf shutil.move mit Warnhinweis.

D) Atomarität via Rollback

Erfolgreiche Moves werden pro Session protokolliert.

Tritt während --apply ein Fehler auf, werden bereits ausgeführte Moves rückwärts zurückgerollt (best effort).

Session-Log ist im Report sichtbar.

5. Konfigurationsdatei

Datei: docs/STRUCTURE_RULES.yaml

Relevante Sektionen:

required_directories

allowed_root_files

categories

validation:

language

language_scope

language_rules

require_lowercase_directories

allow_nested_categories

case_sensitive

reporting:

auto_save_report

report_location

report_format


6. Betriebsmodi
----------------
Dry-Run (Standard)
	python scripts/validate_docs_structure.py validate --projects foo foo-mcp-server

Plan (mit vorgeschlagenen Moves)
	python scripts/validate_docs_structure.py plan --projects foo

Apply (mit Moves)
	python scripts/validate_docs_structure.py apply --projects foo

Explizite Rule-Datei
	python scripts/validate_docs_structure.py validate --projects foo --rules-file /abs/path/to/docs/STRUCTURE_RULES.yaml

7. Ergebnisinterpretation
-------------------------

- Keine Violations gefunden: Struktur/Policy konform.
- Violations gefunden: Report listet Typ, Schwere, Pfad.
- Bei --apply mit Fehler: Rollback wird gestartet. Ergebnis ist best effort; bei Rollback-Fehlern manuell prüfen.

8. Teststrategie & Qualitätssicherung
-------------------------------------

Die Kernlogik wird durch automatisierte Unit-Tests in `tests/test_validate_docs_structure.py` abgedeckt. Die wichtigsten Testfälle sind:

**Kategorielogik und Archivierung:**
- Datei, die zu einer Kategorie passt, wird korrekt nach `archive/<kategorie>/` verschoben.
- Datei ohne Kategorie landet in `archive/`.
- Erlaubte Root-Dateien (z. B. README.md) werden nicht verschoben.
- Unterordner in archive werden akzeptiert, wenn `allow_nested_categories` gesetzt ist.

**Edge-Case-Tests:**
- **Kollision im Zielverzeichnis:** Es wird geprüft, dass eine bereits existierende Datei im Ziel (z. B. `archive/architecture/TEST_ARCHITECTURE.md`) durch die neue Datei überschrieben wird.
- **Fehlerhafte YAML-Konfiguration:** Das Laden einer ungültigen YAML-Datei wird robust abgefangen und führt zu keinem Absturz.
- **Rollback bei Fehler:** Ein künstlich ausgelöster Fehler beim Verschieben löst ein Rollback aus; die ursprünglichen Dateien bleiben erhalten.

**Testprinzipien:**
- Jeder Test läuft in einer isolierten temporären Umgebung und räumt nach sich auf.
- Die Tests sind deterministisch und plattformunabhängig (Pfadvergleich via `.replace("\\", "/")`).
- Die Testdaten werden konsistent über eine Hilfsfunktion erzeugt.

**Testausführung:**
	python -m pytest tests/test_validate_docs_structure.py

9. Verbindlichkeit (Hard Gate)
-----------------------------

Dieses Tool ist in unseren Projekten verbindlich.

Durchsetzung:
- Pre-Commit Hook: Verhindert lokale Commits bei Violations.
- CI Workflow: Build/Pipeline fällt bei Violations.

Regel:
- Kein Merge ohne grünen Validator-Run.
- Struktur- und Sprach-Policy sind verpflichtende Quality Gates.

10. Policy-Entscheidung für Ablageort
-------------------------------------

Diese Dokumentation wird in `docs/process/` abgelegt.

Begründung:
- Inhalt ist Governance + Workflow (kein Enduser-Manual, keine ADR-Entscheidung).
- Laut Sprach-Policy ist process deutschsprachig.
- Damit bleibt die Doku policy-konform und validierungsstabil.

11. Lizenz
----------

Docstructor steht unter der **MIT License**.

Die vollständigen Lizenzbedingungen stehen in [LICENSE](../../LICENSE).
