# Docstructor

[![CI](https://github.com/databap/docstructor/actions/workflows/ci.yml/badge.svg)](https://github.com/databap/docstructor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Docstructor ist ein Validator und Reorganizer für Markdown-basierte Projektdokumentation.

Das Tool prüft die Struktur unter `docs/`, validiert Regeln aus `STRUCTURE_RULES.yaml`, erstellt Reorganisationspläne und kann Dokumente kontrolliert mit Rollback verschieben.

## Features

- Validierung von `docs/`-Strukturen anhand von `STRUCTURE_RULES.yaml`
- Kategorisierung per Dateimuster
- Reorganisationsplan vor Anwendung (`plan`)
- Optionales Anwenden mit Rollback und Locking
- JSON-Ausgabe für CI- und Automatisierungsszenarien
- Schema-Validierung für die Rules-Datei

## Installation

### Laufzeitinstallation
```bash
pip install -e .
```

### Entwicklung inklusive Test-Abhängigkeiten
```bash
pip install -e ".[dev]"
```

## Quickstart

### Validierung ausführen
```bash
docstructor validate --projects .
```

### Reorganisationsplan anzeigen
```bash
docstructor plan --projects .
```

### Änderungen anwenden
```bash
docstructor apply --projects .
```

### Alternativ über das Wrapper-Skript
```bash
python scripts/validate_docs_structure.py validate --projects .
```

## Entwicklung

### Tests ausführen
```bash
pytest -q
```

### Pre-commit lokal ausführen
```bash
python -m pre_commit install
python -m pre_commit run --all-files
```

Hinweis: In restriktiven Windows-Umgebungen kann `pre-commit.exe` durch Gruppenrichtlinien blockiert sein. In dem Fall funktioniert in der Regel der Aufruf über `python -m pre_commit`.

### Projektstruktur
```text
.
├── .pre-commit-config.yaml
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/
│   ├── DOKUMENTATIONS_GOVERNANCE_TOOL.md
│   ├── STRUCTURE_RULES.yaml
│   └── VALIDATE_DOCS_JSON_SCHEMA.json
├── scripts/
│   └── validate_docs_structure.py
├── src/
│   ├── __init__.py
│   └── validate_docs_structure.py
├── tests/
│   └── test_validate_docs_structure.py
├── .gitignore
├── LICENSE
├── pyproject.toml
└── README.md
```

## CI

Für Pushes auf `main` und Pull Requests läuft eine minimale GitHub-Actions-Pipeline, die das Paket installiert und `pytest -q` ausführt.

## Veröffentlichung nach GitHub

Standardmäßig empfiehlt sich HTTPS:

```bash
git init
git add .
git commit -m "chore: initialize Docstructor project"
git branch -M main
git remote add origin https://github.com/databap/docstructor.git
git push -u origin main
```

Wenn `origin` schon existiert:
```bash
git remote set-url origin https://github.com/databap/docstructor.git
```

Optional mit SSH, wenn dein Netzwerk GitHub-SSH erlaubt:
```bash
git remote set-url origin git@github.com:databap/docstructor.git
```

## Weiterführende Dokumentation

- `docs/DOKUMENTATIONS_GOVERNANCE_TOOL.md`
- `docs/STRUCTURE_RULES.yaml`
- `docs/VALIDATE_DOCS_JSON_SCHEMA.json`

## Lizenz

MIT — siehe [LICENSE](LICENSE).
