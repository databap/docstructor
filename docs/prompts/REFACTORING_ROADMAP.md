Um diesen Refactoring-Sprint erfolgreich durchzuführen, müssen wir sicherstellen, dass die **Logik des Validators** (die Engine) und die **Struktur der Regeln** (die Daten) synchronisiert werden. 

Da wir in einem **Agenten-basierten Team** arbeiten, ist dieser Plan so konzipiert, dass er die Rollen von `@caliando-architect` (Design), `@caliando-developer` (Implementierung) und `@caliando-qa` (Verifizierung) nutzt.

---

# 🚀 Refactoring Sprint Plan: "Project Modular Docs"
**Ziel**: Transformation der monolithischen `STRUCTURE_RULES.yaml` in ein modulares System und Anpassung des `validate_docs_structure.py` Validators.
**Dauer**: 1 Sprint (geschätzt 4-6 Arbeitsstunden)
**Status**: 🛠️ PLANNING PHASE

---

## 📋 Sprint Backlog & Task Breakdown

### 🏗️ Phase 1: Architecture & Design (The Blueprint)
**Verantwortlich**: `@caliando-architect`
**Ziel**: Definition des neuen Schemas und der Import-Logik.

- [ ] **Task 1.1: Schema-Definition für Module**: Festlegen, wie eine `.rules.yaml` Datei aufgebaut sein muss (z.B. Pflichtfelder: `name`, `scope`, `rules`).
- [  ] **Task 1.2: Design der Import-Logik**: Festlegen, wie der Python-Skript die `imports` im Hauptdokument findet (z.B. via `!include` Syntax oder ein `imports`-Array).
- [ ] **Task 1.3: Verzeichnis-Struktur**: Finalisierung des Pfads `docs/rules/` für die neuen Modul-Dateien.

### 🛠️ Phase 2: Implementation (The Engine)
**Verantwortlich**: `@developer` (oder `me`)
**Ziel**: Anpassung des Python-Skripts zur Unterstützung von Multi-File-Konfigurationen.

- [ ] **Task 2.1: Refactoring `validate_docs.py`**:
    - Implementierung eines `load_config()` Moduls, das rekursiv `.yaml` oder `.py` (je nach Format) Dateien einliest.
    - Implementierung einer `ConfigLoader` Klasse, die `imports` erkennt und die Dateien zusammenführt (Merging Logic).
- [  ] **Task 2.2: Implementierung der Merging-Logik**:
    - Sicherstellen, dass `dict.update()` korrekt verwendet wird, um Regeln aus Sub-Files in das Haupt-Objekt zu mergen.
    - Implementierung von Error-Handling (z.B. `FileNotFoundError`, wenn ein importiertes File fehlt).
- [ ] **Task 2.3: Unit Tests für den Loader**:
    - Erstellung eines kleinen Test-Sets mit: 1. Single File, 2. File mit Broken Import, 3. File mit Deeply Nested Imports.

### 🧪 Phase 3: Migration (The Data)
**Verantwortlich**: `@developer`
**Ziel**: Umzug der bestehenden Regeln in die neue Struktur.

- [ ] **Task 3.1: Erstellung der Modul-Dateien**:
    - `rules/language_rules.yaml` (Sprachregeln, Tonalität).
    - `rules/structure_rules.yaml` (Ordnerstruktur, Dateinamen).
    - `rules/metadata_rules.yaml` (Header-Vorgaben, Dateikopf).
- [ ] **Task 3.2: Bereinigung der `STRUCTURE_RULES.yaml`**:
    - Entfernen der extrahierten Regeln aus der Hauptdatei.
    - Hinzufügen der `imports: [...]` Sektion.

### ✅ Phase 4: Verification & QA (The Guard)
**Verantwortlich**: `@qa_engineer` (oder `me`)
**Ziel**: Sicherstellen, dass die Validierung weiterhin korrekt funktioniert.

- [ ] **Task 4.1: Regression Test**: Ausführen des Validators auf dem alten (jetzt migrierten) Dateisatz. Erwartetes Ergebnis: `SUCCESS`.
- [ ] **Task 4.2: Negative Test**: Erstellen einer Datei mit einem absichtlich fehlerhaften Import (z.B. `imports: ['non_existent_file.yaml']`). Erwartetes Ergebnis: `ERROR: Import failed`.
- [ ] **Task 4.3: Dokumentation**: Aktualisierung der `README.md` oder der Dokumentation des Validators, um den neuen Workflow (Erstellen von Modulen) zu erklären.

---

## 🛠️ Technischer Blueprint (Die "Merging" Logik)

Damit der Plan erfolgreich ist, muss der `validate_docs.py` Skript folgendem Muster folgen:

```python
# Pseudo-Code für die neue Logik
class ConfigLoader:
    def load(self, main_file):
        config = self.parse_yaml(main_sfile)
        if 'imports' in config:
            for import_path in config['imports']:
                sub_config = self.load(import_path) # Rekursiv!
                config.update(sub_config) # Merge
        return config
```

## 🚀 Definition of Done (DoD)
1. [ ] Der Befehl `python validate_docs.py` läuft ohne Fehler durch.
2. [ ] Die Konfiguration ist in mindestens zwei separate Dateien aufgeteilt.
3. [ ] Ein fehlender Import wird als klarer Fehler im Log ausgegeben.
4. [ ] Die Dokumentation beschreibt, wie neue Regeln via `imports` hinzugefügt werden.

---
**Status:** `READY_FOR_EXECUTION`
**Next Step:** Start Task 2.1 (Refactoring the Engine).