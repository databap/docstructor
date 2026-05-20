# Validate Docs: Pre-Commit und CI Design

## Ziel
Schneller lokaler Check in Pre-Commit und deterministischer CI-Lauf mit klaren Exit-Codes und JSON-Ausgabe.

## Exit-Codes
- `0`: keine Violations, kein Fehler
- `1`: Violations gefunden, aber Tool lief technisch sauber
- `2`: harte Fehler (Security, Lock, Rules-Load, Apply-Fehler)

## Empfohlene Commands

### 1) Lokaler Pre-Commit Check (nur validieren, JSON für Hooks)
```bash
python scripts/validate_docs_structure.py validate --projects . --json --ci --changed-only
```

Erwartung:
- Exit `0` oder `1` blockt optional je nach Team-Policy
- Exit `2` blockt immer (Tooling- oder Systemfehler)

### 2) CI Strict Mode
```bash
python scripts/validate_docs_structure.py validate --projects . --json --ci
```

Empfehlung in CI:
- `exit 0`: pass
- `exit 1`: fail (Policy-Verstoß)
- `exit 2`: fail (infra/tooling problem)

### 3) Apply in Wartungs-Job
```bash
python scripts/validate_docs_structure.py apply --projects . --save-report --log-json docs/.doc_tool.runtime.json
```

## JSON-Ausgabe
Rules-Schema beim Laden von `STRUCTURE_RULES.yaml`: [VALIDATE_DOCS_JSON_SCHEMA.json](VALIDATE_DOCS_JSON_SCHEMA.json)

Top-Level:
- `summary.projects_total`
- `summary.projects_with_violations`
- `summary.projects_with_errors`
- `projects[]` mit `status = ok|violations|error`

## Pre-Commit Integration (Beispiel)
```yaml
repos:
  - repo: local
    hooks:
      - id: validate-doc-structure
        name: validate-doc-structure
        entry: python scripts/validate_docs_structure.py validate --projects . --json --ci --changed-only
        language: system
        pass_filenames: false
```

## Erweiterung (nächster Schritt)
      - optionales Team-Policy-Flag für Exit-1-Verhalten in Hooks
      - zusätzliche Artefakt-Ablage/Retention für `--log-json`
