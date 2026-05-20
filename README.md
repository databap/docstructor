# Docstructor

Docstructor ist ein **Documentation Structure Validator & Reorganizer** für Markdown-basierte Projektdokumentation.

## Features
- Validierung von `docs/`-Struktur via `STRUCTURE_RULES.yaml`
- Kategorisierung per Dateimuster
- Optionales Reorganisieren mit Rollback
- Locking gegen parallele Ausführung
- JSON-Output für CI/MCP
- Schema-Validierung der Rules

## Projektstruktur

```text
.
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
└── README.md
```

## Schnellstart

### 1) Tests
```bash
python -m pytest tests/test_validate_docs_structure.py -q
```

### 2) Validierung
```bash
python scripts/validate_docs_structure.py validate --projects .
```

### 3) Plan anzeigen
```bash
python scripts/validate_docs_structure.py plan --projects .
```

### 4) Apply
```bash
python scripts/validate_docs_structure.py apply --projects .
```

## Veröffentlichung nach GitHub (SSH)

```bash
git init
git add .
git commit -m "chore: initialize Docstructor project"
git branch -M main
git remote add origin git@github.com:databap/docstructor.git
git push -u origin main
```

Wenn `origin` schon existiert:
```bash
git remote set-url origin git@github.com:databap/docstructor.git
```

## Lizenz
MIT — siehe [LICENSE](LICENSE).
