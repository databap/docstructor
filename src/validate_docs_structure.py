#!/usr/bin/env python3
"""
Docstructor - Documentation Structure Validator & Reorganizer

Production-Grade Erweiterungen:
- Security: Path-Traversal-Prevention via resolve() + is_relative_to()
- Data Integrity: Rollback bei Fehlern waehrend apply
- Robustheit: Git-Erkennung mit sicherem Fallback auf shutil.move
- Concurrency: Lock-Datei pro project_root gegen parallele Ausfuehrung

Bug-Historie (Kurzreferenz):
- Bug #1: Root-File-Scan (allowed_root_files)
- Bug #2: Nested-Categories Override
- Bug #3: Unicode/Language-Heuristik
- Bug #4: Language-Check Performance
- Bug #5: archive als Container
- Bug #6: CLI-Semantik (default dry-run, --apply aktiv)
- Bug #7: Atomarer Lock
- Bug #8: Move/Precheck/Rollback- und Overwrite-Semantik
"""

import argparse
import contextlib
import fnmatch
import io
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml


TOOL_NAME = "Docstructor"
TOOL_TAGLINE = "Documentation Structure Validator & Reorganizer"


class SecurityError(Exception):
    """Wird geworfen, wenn ein Pfad gegen Sicherheitsregeln verstoesst."""


class LockError(Exception):
    """Wird geworfen, wenn ein Projekt bereits durch eine andere Instanz gelockt ist."""


class RulesSchemaError(Exception):
    """Wird geworfen, wenn STRUCTURE_RULES.yaml gegen das Schema verstoesst."""


def _validate_json_schema_subset(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Kleiner, deterministischer JSON-Schema-Validator (Subset) ohne externe Abhängigkeit."""
    errors: List[str] = []

    schema_type = schema.get("type")
    if schema_type is not None:
        if schema_type == "object" and not isinstance(instance, dict):
            return [f"{path}: expected object, got {type(instance).__name__}"]
        if schema_type == "array" and not isinstance(instance, list):
            return [f"{path}: expected array, got {type(instance).__name__}"]
        if schema_type == "string" and not isinstance(instance, str):
            return [f"{path}: expected string, got {type(instance).__name__}"]
        if schema_type == "integer" and not (isinstance(instance, int) and not isinstance(instance, bool)):
            return [f"{path}: expected integer, got {type(instance).__name__}"]
        if schema_type == "boolean" and not isinstance(instance, bool):
            return [f"{path}: expected boolean, got {type(instance).__name__}"]
        if schema_type == "null" and instance is not None:
            return [f"{path}: expected null, got {type(instance).__name__}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} not in enum {schema['enum']!r}")

    if "anyOf" in schema:
        anyof_errors: List[List[str]] = []
        for idx, subschema in enumerate(schema["anyOf"]):
            sub_errors = _validate_json_schema_subset(instance, subschema, path)
            if not sub_errors:
                anyof_errors = []
                break
            anyof_errors.append([f"anyOf[{idx}] {e}" for e in sub_errors])
        if anyof_errors:
            for err_group in anyof_errors:
                errors.extend(err_group)

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required key '{key}'")

        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(_validate_json_schema_subset(value, properties[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property '{key}' is not allowed")

    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for index, value in enumerate(instance):
            errors.extend(_validate_json_schema_subset(value, item_schema, f"{path}[{index}]"))

    minimum = schema.get("minimum")
    if minimum is not None and isinstance(instance, int) and instance < minimum:
        errors.append(f"{path}: value {instance} is less than minimum {minimum}")

    return errors


class DocStructureValidator:
    """Validiert und reorganisiert Dokumentations-Struktur."""

    # Bug #4 Fix: Performance-Limit für Language-Check
    MAX_LANG_VALIDATION_BYTES = 8192

    def __init__(
        self,
        project_root: Path,
        dry_run: bool = True,
        verbose: bool = False,
        rules_file: Optional[Path] = None,
        validate_rules_schema: bool = True,
        changed_only: bool = False,
    ):
        self.project_root = Path(project_root).resolve()
        self.docs_dir = (self.project_root / "docs").resolve()
        self.dry_run = dry_run
        self.verbose = verbose
        self.rules: Dict = {}
        self.violations: List[Dict] = []
        self.proposed_moves: List[Tuple[Path, Path]] = []
        self.rules_file = Path(rules_file).resolve() if rules_file else None
        self.validate_rules_schema = validate_rules_schema
        self.changed_only = changed_only
        self.checked_files: List[str] = []
        self.last_error_type: Optional[str] = None
        self._untracked_shutil_fallback_count: int = 0
        self._untracked_shutil_fallback_warned: bool = False

        # Feature 2: Session-Log fuer Nachvollziehbarkeit/Rollback
        self.operation_log: List[str] = []

        # Feature 3: Git-Abhaengigkeit robust behandeln
        self.git_repo = self._is_git_repo()

        # Feature 4: Locking gegen parallele Ausfuehrung
        self.lock_file = self.project_root / ".doc_tool.lock"

        # Feature 1: Security-Validierung der Kernpfade
        self._validate_core_paths()

    # ---------------------------------------------------------------------
    # Security helpers (Feature 1)
    # ---------------------------------------------------------------------

    def _is_relative_to(self, path: Path, parent: Path) -> bool:
        """Kompatibler is_relative_to-Ersatz."""
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    def _secure_path_in_project(self, path: Path) -> Path:
        """Sichert ab, dass ein Pfad innerhalb project_root liegt."""
        resolved = path.resolve()
        if not self._is_relative_to(resolved, self.project_root):
            raise SecurityError(
                f"Unsicherer Pfad ausserhalb project_root: {resolved} (root={self.project_root})"
            )
        return resolved

    def _validate_core_paths(self) -> None:
        """
        Feature 1:
        Validiert project_root/docs sowie optionales rules_file gegen Traversal.
        """
        if not self.project_root.exists():
            raise SecurityError(f"project_root existiert nicht: {self.project_root}")

        if not self._is_relative_to(self.docs_dir, self.project_root):
            raise SecurityError(
                f"docs_dir liegt ausserhalb project_root: docs={self.docs_dir}, root={self.project_root}"
            )

        if self.rules_file is not None and not self._is_relative_to(self.rules_file, self.project_root):
            raise SecurityError(
                f"rules_file liegt ausserhalb project_root: rules={self.rules_file}, root={self.project_root}"
            )

    # ---------------------------------------------------------------------
    # Locking (Feature 4)
    # ---------------------------------------------------------------------

    def acquire_lock(self) -> None:
        """Erzeugt Lock-Datei atomar; wenn vorhanden -> sofortiger Abbruch.
        
        Bug #7 Fix: Atomar mit os.O_EXCL (keine Race-Condition).
        """
        for attempt in range(2):
            try:
                fd = os.open(
                    str(self.lock_file),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n")
                self.operation_log.append(f"LOCK acquire {self.lock_file}")
                return
            except FileExistsError:
                if attempt == 0 and self._cleanup_stale_lock():
                    continue

                lock_info = self._read_lock_info()
                details = f" ({lock_info})" if lock_info else ""
                raise LockError(
                    f"Lock existiert bereits: {self.lock_file}{details}. "
                    "Eine andere Instanz laeuft vermutlich noch."
                )

    def _read_lock_info(self) -> str:
        """Liest Lock-Metadaten für Diagnosezwecke."""
        if not self.lock_file.exists():
            return ""

        try:
            content = self.lock_file.read_text(encoding="utf-8", errors="ignore")
            compact = content.replace("\n", "; ").strip("; ").strip()
            return compact
        except Exception:
            return ""

    def _is_process_alive(self, pid: int) -> bool:
        """Prüft robust, ob ein Prozess mit PID läuft."""
        if pid <= 0:
            return False

        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Prozess existiert, aber keine Berechtigung.
            return True
        except Exception:
            return True

    def _cleanup_stale_lock(self) -> bool:
        """Entfernt verwaiste Lock-Dateien (stale lock)."""
        if not self.lock_file.exists():
            return False

        try:
            content = self.lock_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False

        pid: Optional[int] = None
        for line in content.splitlines():
            if line.startswith("pid="):
                try:
                    pid = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pid = None
                break

        if pid is None:
            return False

        if self._is_process_alive(pid):
            return False

        try:
            self.lock_file.unlink()
            self.operation_log.append(f"LOCK stale removed {self.lock_file} (pid={pid})")
            return True
        except Exception:
            return False

    def release_lock(self) -> None:
        """Loescht Lock-Datei robust (auch bei Fehlern)."""
        if self.lock_file.exists():
            try:
                self.lock_file.unlink()
                self.operation_log.append(f"LOCK release {self.lock_file}")
            except Exception as e:
                print(f"WARN: Lock konnte nicht geloescht werden: {self.lock_file} ({e})")

    # ---------------------------------------------------------------------
    # Config helpers
    # ---------------------------------------------------------------------

    def _validation_cfg(self) -> Dict:
        return self.rules.get("validation", {})

    def _reporting_cfg(self) -> Dict:
        return self.rules.get("reporting", {})

    def _is_git_repo(self) -> bool:
        """
        Feature 3:
        Git-Erkennung gemaess Anforderung ueber .git-Ordner.
        """
        return (self.project_root / ".git").exists()

    # ---------------------------------------------------------------------
    # Rules loading
    # ---------------------------------------------------------------------

    def load_rules(self) -> bool:
        """
        Regeln laden in Reihenfolge:
        1) explizites rules_file
        2) lokales docs/STRUCTURE_RULES.yaml
        3) vertrauenswuerdiger Fallback im gleichen project_root (wenn vorhanden)
        """
        self.last_error_type = None

        if self.rules_file and self.rules_file.exists():
            try:
                safe_rules = self._secure_path_in_project(self.rules_file)
                with open(safe_rules, "r", encoding="utf-8") as f:
                    self.rules = yaml.safe_load(f) or {}
                if self.validate_rules_schema:
                    self._validate_rules_schema(safe_rules)
                if self.verbose:
                    print(f"Regeln geladen (EXPLIZIT): {safe_rules}")
                return True
            except RulesSchemaError as e:
                self.last_error_type = "rules_schema_invalid"
                print(f"Fehler beim Laden von rules_file: {e}")
                return False
            except Exception as e:
                print(f"Fehler beim Laden von rules_file: {e}")
                return False

        local_rules_file = self.docs_dir / "STRUCTURE_RULES.yaml"
        if local_rules_file.exists():
            try:
                safe_rules = self._secure_path_in_project(local_rules_file)
                with open(safe_rules, "r", encoding="utf-8") as f:
                    self.rules = yaml.safe_load(f) or {}
                if self.validate_rules_schema:
                    self._validate_rules_schema(safe_rules)
                if self.verbose:
                    print(f"Regeln geladen (LOKAL): {safe_rules}")
                return True
            except RulesSchemaError as e:
                self.last_error_type = "rules_schema_invalid"
                print(f"Fehler beim Laden lokaler Regeln: {e}")
                return False
            except Exception as e:
                print(f"Fehler beim Laden lokaler Regeln: {e}")
                return False

        print("Keine Regelfile gefunden.")
        print("Gesucht in:")
        if self.rules_file:
            print(f"- {self.rules_file} (explizit)")
        print(f"- {local_rules_file} (lokal)")
        return False

    def _rules_schema_path(self) -> Optional[Path]:
        """Ermittelt den Pfad des Rules-Schemas mit robustem Fallback."""
        candidates = [
            self.project_root / "docs" / "VALIDATE_DOCS_JSON_SCHEMA.json",
            self.project_root / "docs" / "process" / "VALIDATE_DOCS_JSON_SCHEMA.json",
            Path(__file__).resolve().parent.parent / "docs" / "VALIDATE_DOCS_JSON_SCHEMA.json",
            Path(__file__).resolve().parent.parent / "docs" / "process" / "VALIDATE_DOCS_JSON_SCHEMA.json",
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        return None

    def _validate_rules_schema(self, rules_source_path: Path) -> None:
        """Validiert geladene Rules gegen JSON-Schema."""
        schema_path = self._rules_schema_path()
        if schema_path is None:
            raise RulesSchemaError(
                "Schema nicht gefunden: docs/VALIDATE_DOCS_JSON_SCHEMA.json "
                "oder docs/process/VALIDATE_DOCS_JSON_SCHEMA.json"
            )

        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
        except Exception as e:
            raise RulesSchemaError(f"Schema konnte nicht geladen werden ({schema_path}): {e}") from e

        errors = _validate_json_schema_subset(self.rules, schema)
        if errors:
            preview = " | ".join(errors[:5])
            if len(errors) > 5:
                preview += f" | ... (+{len(errors) - 5} weitere)"
            raise RulesSchemaError(
                f"Rules-Schema ungültig: source={rules_source_path}, schema={schema_path}: {preview}"
            )

    def _changed_docs_files(self) -> List[Path]:
        """Ermittelt geänderte Dateien unter docs/ via git diff --name-only HEAD."""
        if not self.git_repo:
            self.operation_log.append("CHANGED_ONLY: no git repo detected")
            return []

        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.operation_log.append(
                    f"CHANGED_ONLY WARN: git diff failed stderr={result.stderr.strip()}"
                )
                return []

            changed_paths: List[Path] = []
            for rel in result.stdout.splitlines():
                rel = rel.strip()
                if not rel or not rel.startswith("docs/"):
                    continue
                abs_path = (self.project_root / rel).resolve()
                if self._is_relative_to(abs_path, self.docs_dir):
                    changed_paths.append(abs_path)

            self.operation_log.append(f"CHANGED_ONLY files={len(changed_paths)}")
            return changed_paths
        except Exception as e:
            self.operation_log.append(f"CHANGED_ONLY ERROR: {e}")
            return []

    # Bug #2 Helper: Category-spezifische allow_nested_categories
    def _allows_nested_categories_in(self, category: str) -> bool:
        """Prüft kategorie-spezifisches Flag, fallback zu global.
        
        Bug #2 Fix: Category-Flag überschreibt globales Flag.
        """
        category_cfg = self.rules.get("categories", {}).get(category, {})
        # Kategorie-spezifisches Flag hat Priorität
        if "allow_nested_categories" in category_cfg:
            return category_cfg["allow_nested_categories"]
        # Fallback zu global
        return self._validation_cfg().get("allow_nested_categories", True)

    # Bug #1 Helper: Alle Dateien im docs-Root (nicht nur *.md)
    def _get_root_files(self) -> List[Path]:
        """Alle Dateien im docs-Root außer Ordnern.
        
        Bug #1 Fix: Scannt alle Dateitypen (nicht nur *.md),
        damit STRUCTURE_RULES.yaml etc. validiert werden.
        """
        return [p for p in self.docs_dir.iterdir() if p.is_file()]

    # Bug #5 Helper: Archiv Container Validierung
    def _is_archive_container(self, category: str) -> bool:
        """Prüft, ob Kategorie ein spezieller Container ist."""
        category_cfg = self.rules.get("categories", {}).get(category, {})
        return category_cfg.get("is_container", False)

    def _validate_archive_container(self) -> None:
        """Validiere, dass archive nur Unterordner enthält.
        
        Bug #5 Fix: archive ist kein normales Archiv, sondern ein Container
        mit Unterordnern. Dateien direkt in archive/ sind nicht erlaubt.
        """
        archive_path = self.docs_dir / "archive"
        if not archive_path.exists():
            return

        for item in archive_path.iterdir():
            if item.is_file():
                # Datei direkt in archive/ ist nicht erlaubt
                archive_target = archive_path.parent / "archive_error" / item.name
                self.violations.append({
                    "type": "archive_root_file_forbidden",
                    "severity": "medium",
                    "message": f"Datei in archive-Container nicht erlaubt (nur Unterordner): {item.name}",
                    "path": str(item),
                    "target": str(archive_target),
                })

    # Helper: Language-Check mit Performance-Limit
    def _get_file_sample_for_lang_check(self, file_path: Path, max_bytes: Optional[int] = None) -> str:
        """Lies nur die ersten N Bytes einer Datei für Language-Check.
        
        Bug #4 Fix: Begrenzt Dateigröße für Sprachvalidierung.
        """
        if max_bytes is None:
            max_bytes = self.MAX_LANG_VALIDATION_BYTES
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read(max_bytes)
        except Exception:
            return ""

    # Helper: Unicode-normalisiertes Pattern-Matching
    def _matches_pattern_normalized(self, filename: str, pattern: str) -> bool:
        """Normalisiert filename vor fnmatch (für Unicode-Stabilität).
        
        Bug #3 Fix: Verwendet unicodedata.normalize für konsistente Marker-Treffer.
        """
        case_sensitive = self._validation_cfg().get("case_sensitive", False)
        
        # Unicode-Normalisierung (NFC = Composed Form)
        filename_norm = unicodedata.normalize("NFC", filename)
        pattern_norm = unicodedata.normalize("NFC", pattern)
        
        if case_sensitive:
            return fnmatch.fnmatch(filename_norm, pattern_norm)
        return fnmatch.fnmatch(filename_norm.upper(), pattern_norm.upper())

    # Pre-Check für Move-Operationen (Overwrite erlaubt, Konsistenz erzwungen)
    def _precheck_move(self, source: Path, target: Path) -> bool:
        """Prüft vor einem Move, dass keine Inkonsistenzen entstehen.
        
        Bug #8 v2 Fix:
        - Source muss existieren.
        - Target.parent: wenn existent, muss writable sein. Wenn nicht existent → OK (mkdir anlegt es).
        - Target.exists() ist OK → wird überschrieben (Overwrite-Semantik).
        """
        source = self._secure_path_in_project(source)
        target = self._secure_path_in_project(target)

        # Source muss existieren
        if not source.exists():
            self.operation_log.append(f"PRECHECK FAIL: source not found {source}")
            return False

        # Target.parent: wenn existent, muss writable sein
        if target.parent.exists() and not os.access(target.parent, os.W_OK):
            self.operation_log.append(f"PRECHECK FAIL: target parent not writable {target.parent}")
            return False
        
        # Target.exists() ist OK: Overwrite ist erlaubt.
        # Die eigentliche Policy fuer Overwrite im Git-Repo wird in _move_with_fallback
        # strikt via "git mv -f" durchgesetzt.

        self.operation_log.append(f"PRECHECK OK: {source} -> {target}")
        return True

    # Bug #1 Helper: Find target directory für Datei
    def _find_file_target_directory(self, filename: str) -> str:
        """Findet die Zielkategorie für eine Datei basierend auf Patterns."""
        for category, config in self.rules.get("categories", {}).items():
            # Container-Kategorien sind niemals reguläre Zielkategorien.
            if category == "archive" or self._is_archive_container(category):
                continue

            patterns = config.get("file_patterns", [])
            for pattern in patterns:
                if self._matches_pattern_normalized(filename, pattern):
                    return category
        return ""

    # Bug #3 Fix: Language validation mit Unicode-Normalisierung
    def _validate_language_consistency(self, language: str, markdown_files: Optional[List[Path]] = None) -> None:
        """Validiere Sprachkonsistenz mit Unicode-Normalisierung.
        
        Bug #3 Fix: Nutzt unicodedata.normalize('NFC') für stabile Marker-Treffer.
        """
        if markdown_files is None:
            markdown_files = list(self.docs_dir.rglob("*.md"))

        validation_cfg = self._validation_cfg()
        language_rules = validation_cfg.get("language_rules", {})
        language_scope = validation_cfg.get("language_scope", {})

        fallback_rules = {
            "de": {
                "markers": [
                    " und ", " der ", " die ", " das ", " mit ", " für ", " über ",
                    "nicht", "soll", "wird", "ä", "ö", "ü", "ß",
                    "dokumentation", "verzeichnis", "validierung",
                ],
                "min_chars": 120,
                "min_marker_hits": 2,
            },
            "en": {
                "markers": [
                    " and ", " the ", " with ", " should ", " will ", " for ", " to ",
                    "documentation", "validation", "directory", "guide", "setup",
                ],
                "min_chars": 120,
                "min_marker_hits": 2,
            },
        }

        for md_file in markdown_files:
            # Nur erste MAX_LANG_VALIDATION_BYTES lesen; Fehler werden im Helper abgefangen.
            content = self._get_file_sample_for_lang_check(md_file)

            if not content:
                continue

            relative = md_file.relative_to(self.docs_dir)
            top_level = relative.parts[0] if len(relative.parts) > 1 else "__root__"
            expected_language = language_scope.get(top_level, language)

            if str(expected_language).lower() in {"any", "none", "skip"}:
                continue

            lang_key = str(expected_language).lower()
            rule = language_rules.get(lang_key, fallback_rules.get(lang_key))
            if rule is None:
                self.violations.append({
                    "type": "language_rule_missing",
                    "severity": "low",
                    "message": (
                        f"Keine language_rules für '{expected_language}' konfiguriert "
                        f"(Datei erwartet Sprache laut scope: {top_level})."
                    ),
                    "path": str(md_file),
                })
                continue

            # Bug #3 Fix: Unicode-Normalisierung VOR lower()
            normalized = f" {unicodedata.normalize('NFC', content).lower()} "
            min_chars = int(rule.get("min_chars", 120))
            min_marker_hits = int(rule.get("min_marker_hits", 2))
            markers = set(rule.get("markers", []))

            if len(normalized.strip()) < min_chars:
                continue

            marker_hits = sum(1 for marker in markers if marker in normalized)
            if marker_hits < min_marker_hits:
                self.violations.append({
                    "type": "language_mismatch",
                    "severity": "low",
                    "message": (
                        f"Datei wirkt nicht {expected_language}-sprachig (heuristische Prüfung, "
                        f"marker_hits={marker_hits}, required={min_marker_hits})."
                    ),
                    "path": str(md_file),
                })

    # Reorganisation mit Pre-Check vor jedem Move und Rollback bei Fehlern
    def apply_reorganization(self) -> bool:
        """Wende alle proposed_moves an mit Rollback bei Fehler.
        """
        if not self.proposed_moves:
            self.operation_log.append("No moves to apply")
            return True

        print(f"\nReorganisiere {len(self.proposed_moves)} Dateien...")
        applied_moves: List[Tuple[Path, Path]] = []
        self._untracked_shutil_fallback_count = 0
        self._untracked_shutil_fallback_warned = False

        for source, target in self.proposed_moves:
            source = self._secure_path_in_project(source)
            target = self._secure_path_in_project(target)

            # Pre-Check vor Move
            if not self._precheck_move(source, target):
                print(f"ERROR: Pre-Check fehlgeschlagen: {source} -> {target}")
                self._rollback_moves(applied_moves)
                return False

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                method = self._move_with_fallback(source, target)
                applied_moves.append((source, target))
                self.operation_log.append(f"MOVE ok ({method}) {source} -> {target}")
            except Exception as e:
                print(f"ERROR: Move fehlgeschlagen: {e}")
                self._rollback_moves(applied_moves)
                return False

        if self._untracked_shutil_fallback_count > 0:
            print(
                "INFO: "
                f"{self._untracked_shutil_fallback_count} unversionierte Datei(en) "
                "wurden per shutil.move verschoben."
            )

        return True

    # ---------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------

    def validate_structure(self) -> bool:
        if not self.docs_dir.exists():
            print(f"docs-Verzeichnis nicht gefunden: {self.docs_dir}")
            return False

        print(f"\nValidiere Struktur: {self.project_root.name}/docs/")

        validation_cfg = self._validation_cfg()
        require_lowercase = validation_cfg.get("require_lowercase_directories", True)
        configured_language = validation_cfg.get("language")
        case_sensitive = validation_cfg.get("case_sensitive", False)

        changed_files_abs: List[Path] = []
        if self.changed_only:
            changed_files_abs = self._changed_docs_files()
            self.checked_files = [str(p) for p in changed_files_abs]
            if not changed_files_abs:
                if self.verbose:
                    print("Changed-only: Keine geänderten docs-Dateien gefunden, skip checks.")
                return True

        changed_files_set = {p.resolve() for p in changed_files_abs}

        # 1) required_directories
        if not self.changed_only:
            required_dirs = self.rules.get("required_directories", [])
            existing_dirs = [p for p in self.docs_dir.iterdir() if p.is_dir()]
            for dir_name in required_dirs:
                if case_sensitive:
                    dir_exists = (self.docs_dir / dir_name).exists()
                else:
                    dir_exists = any(d.name.lower() == dir_name.lower() for d in existing_dirs)

                if not dir_exists:
                    self.violations.append(
                        {
                            "type": "missing_directory",
                            "severity": "high",
                            "message": f"Erforderliches Verzeichnis fehlt: {dir_name}/",
                            "path": str(self.docs_dir / dir_name),
                        }
                    )

        # 2) allowed_root_files
        # Bug #1 Fix: Scanne ALLE Dateien im Root, nicht nur *.md
        root_files = self._get_root_files()
        if self.changed_only:
            root_files = [p for p in root_files if p.resolve() in changed_files_set]
        allowed_root_files = self.rules.get("allowed_root_files", [])
        allowed_cmp = (
            {name.lower() for name in allowed_root_files}
            if not case_sensitive
            else set(allowed_root_files)
        )

        for file_path in root_files:
            file_name = file_path.name
            file_name_cmp = file_name if case_sensitive else file_name.lower()
            if file_name_cmp not in allowed_cmp:
                # Berechne Zielverzeichnis basierend auf Dateiname
                archive_category = self._find_file_target_directory(file_name)
                if archive_category and archive_category != "archive":
                    archive_target = self.docs_dir / "archive" / archive_category / file_name
                else:
                    # Unmatched-Dateien nicht in archive/-Root ablegen,
                    # damit Folge-Läufe idempotent bleiben.
                    archive_target = self.docs_dir / "archive" / "misc" / file_name
                self.violations.append(
                    {
                        "type": "file_in_wrong_location",
                        "severity": "medium",
                        "message": f"Datei ist im docs/-Root nicht erlaubt: {file_name}",
                        "path": str(file_path),
                        "target": str(archive_target),
                    }
                )

        # 3) lowercase dir names
        if require_lowercase and not self.changed_only:
            for item in self.docs_dir.iterdir():
                if item.is_dir() and item.name not in ["__pycache__", ".git"]:
                    if item.name != item.name.lower():
                        self.violations.append(
                            {
                                "type": "case_inconsistency",
                                "severity": "low",
                                "message": f"Verzeichnis-Name sollte lowercase sein: {item.name}",
                                "path": str(item),
                                "target": str(self.docs_dir / item.name.lower()),
                            }
                        )

        # 4) nested categories
        # Bug #2 Fix (v2): Category-Flag kann globales Flag in BEIDE Richtungen übersteuern
        if not self.changed_only:
            for category in self.rules.get("categories", {}).keys():
                # Hole category-spezifisches Flag (Fallback auf validation.allow_nested_categories)
                allows_nested = self._allows_nested_categories_in(category)

                if allows_nested:
                    # Diese Kategorie erlaubt nested dirs, skip
                    continue

                # Diese Kategorie verbietet nested dirs → Check starten
                category_path = self.docs_dir / category
                if not category_path.exists() or not category_path.is_dir():
                    continue

                for nested_dir in category_path.rglob("*"):
                    if (
                        nested_dir.is_dir()
                        and nested_dir != category_path
                        and nested_dir.name not in ["__pycache__", ".git"]
                    ):
                        self.violations.append(
                            {
                                "type": "nested_category_not_allowed",
                                "severity": "medium",
                                "message": (
                                    f"Verschachteltes Verzeichnis nicht erlaubt in {category}/: "
                                    f"{nested_dir.relative_to(category_path)}"
                                ),
                                "path": str(nested_dir),
                            }
                        )

        # 5) language consistency
        if configured_language:
            if self.changed_only:
                changed_markdown = [p for p in changed_files_abs if p.suffix.lower() == ".md" and p.exists()]
                self._validate_language_consistency(configured_language, markdown_files=changed_markdown)
            else:
                self._validate_language_consistency(configured_language)

        # Bug #5 Fix: Validiere archive Container (keine Root-Dateien erlaubt)
        if not self.changed_only and self._is_archive_container("archive"):
            self._validate_archive_container()

        if self.verbose:
            print(f"Validierung abgeschlossen: {len(self.violations)} Violations gefunden")

        return len(self.violations) == 0



    # ---------------------------------------------------------------------
    # Reorganization + Rollback (Feature 2 & 3)
    # ---------------------------------------------------------------------

    def generate_moves(self) -> None:
        for violation in self.violations:
            if violation["type"] == "file_in_wrong_location":
                source = self._secure_path_in_project(Path(violation["path"]))
                target = self._secure_path_in_project(Path(violation["target"]))
                self.proposed_moves.append((source, target))
            elif violation["type"] == "case_inconsistency":
                source = self._secure_path_in_project(Path(violation["path"]))
                target = self._secure_path_in_project(Path(violation["target"]))
                self.proposed_moves.append((source, target))
            elif violation["type"] == "archive_root_file_forbidden":
                # Bug #5 v2: Archive-Root-Dateien in Moves übersetzen (-> archive_error/)
                source = self._secure_path_in_project(Path(violation["path"]))
                target = self._secure_path_in_project(Path(violation["target"]))
                self.proposed_moves.append((source, target))

    def _move_with_fallback(self, source: Path, target: Path) -> str:
        source = self._secure_path_in_project(source)
        target = self._secure_path_in_project(target)

        if self.git_repo:
            try:
                source_rel = os.path.relpath(source, self.project_root)
                target_rel = os.path.relpath(target, self.project_root)

                overwrite_mode = target.exists()
                cmd = ["git", "mv"]
                if overwrite_mode:
                    cmd.append("-f")
                cmd.extend([source_rel, target_rel])

                result = subprocess.run(
                    cmd,
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    self.operation_log.append(f"MOVE git {source} -> {target}")
                    return "git"

                stderr = (result.stderr or "").strip()
                stderr_l = stderr.lower()

                # In gemischten Repositories kann docs/ Dateien enthalten,
                # die (noch) nicht getrackt sind. Dann ist git mv technisch
                # nicht anwendbar; wir fallen kontrolliert auf shutil zurück.
                untracked_indicators = [
                    "not under version control",
                    "did not match any files",
                    "pathspec",
                    "kein dateieintrag",
                    "nicht unter versionskontrolle",
                ]
                if any(ind in stderr_l for ind in untracked_indicators):
                    self._untracked_shutil_fallback_count += 1
                    if not self._untracked_shutil_fallback_warned:
                        print(
                            "WARN: Git-Repository erkannt, aber Quelle nicht versioniert. "
                            "Nutze shutil.move; weitere gleichartige Fälle werden zusammengefasst."
                        )
                        self._untracked_shutil_fallback_warned = True
                    shutil.move(str(source), str(target))
                    self.operation_log.append(
                        f"MOVE shutil(untracked) {source} -> {target} | stderr={stderr}"
                    )
                    return "shutil"

                mode = "git mv -f" if overwrite_mode else "git mv"
                raise RuntimeError(
                    f"{mode} failed: {source} -> {target} | stderr={stderr}"
                )
            except Exception as e:
                # Policy: In Git-Repositories KEIN shutil-Fallback bei Git-Fehlern.
                raise RuntimeError(f"Git-Move fehlgeschlagen: {source} -> {target} | err={e}")
        else:
            print(
                "WARN: Kein Git-Repository erkannt (.git fehlt). "
                "Nutze shutil.move; Git-Historie bleibt nicht erhalten."
            )

        shutil.move(str(source), str(target))
        self.operation_log.append(f"MOVE shutil {source} -> {target}")
        return "shutil"

    def _rollback_moves(self, applied_moves: List[Tuple[Path, Path]]) -> bool:
        print("Rollback gestartet...")
        rollback_ok = True

        for source, target in reversed(applied_moves):
            try:
                source = self._secure_path_in_project(source)
                target = self._secure_path_in_project(target)

                if target.exists() and not source.exists():
                    shutil.move(str(target), str(source))
                    self.operation_log.append(f"ROLLBACK {target} -> {source}")
                    print(f"  rollback ok: {target.name} -> {source.parent.name}/")
                else:
                    self.operation_log.append(
                        f"ROLLBACK-SKIP {target} -> {source} (source={source.exists()}, target={target.exists()})"
                    )
                    print(f"  rollback skip: {target.name}")
            except Exception as e:
                rollback_ok = False
                self.operation_log.append(f"ROLLBACK-ERROR {target} -> {source} | err={e}")
                print(f"  rollback fehler: {target} -> {source}: {e}")

        return rollback_ok

    # Hinweis: apply_reorganization ist weiter oben definiert.

    # ---------------------------------------------------------------------
    # Reporting
    # ---------------------------------------------------------------------

    def generate_report(self) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report = f"""# {TOOL_NAME} Validation Report

Projekt: {self.project_root.name}
Datum: {timestamp}
Modus: {'DRY-RUN' if self.dry_run else 'APPLY'}

## Zusammenfassung

- Violations gefunden: {len(self.violations)}
- Geplante Verschiebungen: {len(self.proposed_moves)}
- Status: {'OK' if len(self.violations) == 0 else 'Violations gefunden'}

## Violations

"""
        if not self.violations:
            report += "Keine Violations gefunden\n"
        else:
            for v in self.violations:
                report += f"- [{v.get('severity', 'low')}] {v['type']}: {v['message']} ({v['path']})\n"

        report += "\n## Geplante Verschiebungen\n\n"
        if not self.proposed_moves:
            report += "Keine Verschiebungen erforderlich\n"
        else:
            for source, target in self.proposed_moves:
                report += f"- {source.name} -> {target.parent.name}/\n"

        # Feature 2: Session-Log im Report
        report += "\n## Session-Log\n\n"
        if not self.operation_log:
            report += "(keine Operationen geloggt)\n"
        else:
            for entry in self.operation_log:
                report += f"- {entry}\n"

        return report

    def save_report(self) -> Optional[Path]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        reporting_cfg = self._reporting_cfg()
        report_location = reporting_cfg.get("report_location", "docs")
        report_format = reporting_cfg.get(
            "report_format",
            "STRUCTURE_VALIDATION_REPORT_{timestamp}.md",
        )

        location_path = Path(report_location)
        if not location_path.is_absolute():
            location_path = self.project_root / location_path
        location_path = self._secure_path_in_project(location_path.resolve())
        location_path.mkdir(parents=True, exist_ok=True)

        report_name = report_format.replace("{timestamp}", timestamp)
        report_path = self._secure_path_in_project((location_path / report_name).resolve())

        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(self.generate_report())
            self.operation_log.append(f"REPORT {report_path}")
            return report_path
        except Exception as e:
            print(f"Fehler beim Speichern des Reports: {e}")
            return None

    def get_machine_readable_result(self, applied: Optional[bool] = None) -> Dict[str, Any]:
        """Liefert ein maschinenlesbares Ergebnis für CI/MCP-Integrationen."""
        return {
            "project": self.project_root.name,
            "project_root": str(self.project_root),
            "docs_dir": str(self.docs_dir),
            "violations_count": len(self.violations),
            "moves_count": len(self.proposed_moves),
            "violations": self.violations,
            "proposed_moves": [
                {"source": str(source), "target": str(target)}
                for source, target in self.proposed_moves
            ],
            "applied": applied,
            "mode": "changed_only" if self.changed_only else "full",
            "checked_files": self.checked_files,
            "operation_log": self.operation_log,
        }


def load_rules(
    project_root: Path | str,
    rules_file: Optional[Path] = None,
    verbose: bool = False,
    validate_schema: bool = True,
) -> DocStructureValidator:
    """Python-API: Lädt Regeln und liefert einen initialisierten Validator."""
    validator = DocStructureValidator(
        project_root=Path(project_root),
        dry_run=True,
        verbose=verbose,
        rules_file=rules_file,
        validate_rules_schema=validate_schema,
    )
    if not validator.load_rules():
        if validator.last_error_type == "rules_schema_invalid":
            raise RulesSchemaError("Regeln entsprechen nicht dem JSON-Schema")
        raise RuntimeError("Regeln konnten nicht geladen werden")
    return validator


def validate(
    project_root: Path | str,
    rules_file: Optional[Path] = None,
    verbose: bool = False,
    validate_schema: bool = True,
) -> Dict[str, Any]:
    """Python-API: Führt nur Validierung aus (keine Move-Planung)."""
    return _run_api_command(
        "validate",
        project_root,
        rules_file=rules_file,
        verbose=verbose,
        validate_schema=validate_schema,
    )


def plan(
    project_root: Path | str,
    rules_file: Optional[Path] = None,
    verbose: bool = False,
    validate_schema: bool = True,
) -> Dict[str, Any]:
    """Python-API: Validiert und erzeugt Move-Plan."""
    return _run_api_command(
        "plan",
        project_root,
        rules_file=rules_file,
        verbose=verbose,
        validate_schema=validate_schema,
    )


def apply(
    project_root: Path | str,
    rules_file: Optional[Path] = None,
    verbose: bool = False,
    validate_schema: bool = True,
) -> Dict[str, Any]:
    """Python-API: Validiert, plant und führt Reorganisation aus."""
    return _run_api_command(
        "apply",
        project_root,
        rules_file=rules_file,
        verbose=verbose,
        validate_schema=validate_schema,
    )


def _run_api_command(
    command: Literal["validate", "plan", "apply"],
    project_root: Path | str,
    rules_file: Optional[Path] = None,
    verbose: bool = False,
    validate_schema: bool = True,
) -> Dict[str, Any]:
    """Gemeinsame Ausführung für die Python-API-Funktionen."""
    validator = DocStructureValidator(
        project_root=Path(project_root),
        dry_run=(command != "apply"),
        verbose=verbose,
        rules_file=rules_file,
        validate_rules_schema=validate_schema,
    )

    validator.acquire_lock()
    try:
        if not validator.load_rules():
            if validator.last_error_type == "rules_schema_invalid":
                raise RulesSchemaError("Regeln entsprechen nicht dem JSON-Schema")
            raise RuntimeError("Regeln konnten nicht geladen werden")

        validator.validate_structure()

        applied: Optional[bool] = None
        if command in {"plan", "apply"}:
            validator.generate_moves()

        if command == "apply":
            applied = validator.apply_reorganization()

        return validator.get_machine_readable_result(applied=applied)
    finally:
        validator.release_lock()


def main():
    parser = argparse.ArgumentParser(
        description=f"{TOOL_NAME} - {TOOL_TAGLINE}"
    )

    parser.add_argument(
        "command",
        nargs="?",
        choices=["validate", "plan", "apply", "report"],
        default=None,
        help="Subcommand (default: validate)",
    )

    parser.add_argument(
        "--projects",
        nargs="+",
        required=True,
        help="Projekt-Namen oder Pfade zu validieren (z.B. Foo Foo-mcp-server)",
    )

    parser.add_argument(
        "--rules-file",
        type=Path,
        default=None,
        help="Optional: Explizite STRUCTURE_RULES.yaml innerhalb des Projekt-Roots",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Backwards-kompatibel: mapped intern auf Subcommand 'apply'",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Detaillierte Ausgabe",
    )

    parser.add_argument(
        "--save-report",
        action="store_true",
        default=False,
        help="Speichere Report als Markdown-Datei",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Gib maschinenlesbares JSON (pro Projekt) aus",
    )

    parser.add_argument(
        "--ci",
        action="store_true",
        default=False,
        help="CI-Modus: kompakte Summary-Ausgabe",
    )

    parser.add_argument(
        "--skip-rules-schema",
        action="store_true",
        default=False,
        help="Überspringe Rules-Schema-Validierung beim Laden",
    )

    parser.add_argument(
        "--changed-only",
        action="store_true",
        default=False,
        help="Prüfe nur geänderte docs-Dateien via git diff --name-only HEAD",
    )

    parser.add_argument(
        "--log-json",
        type=Path,
        default=None,
        help="Schreibe strukturierte Laufzeitdaten als JSON-Datei",
    )

    args = parser.parse_args()

    command = args.command or ("apply" if args.apply else "validate")

    def run_maybe_silenced(func, *f_args, **f_kwargs):
        """Unterdrückt stdout im JSON-Modus, damit Ausgabe strikt maschinenlesbar bleibt."""
        if args.json:
            with contextlib.redirect_stdout(io.StringIO()):
                return func(*f_args, **f_kwargs)
        return func(*f_args, **f_kwargs)

    project_roots: List[Path] = []
    cwd = Path.cwd().resolve()

    # Konfigurierbare Suchpfade fuer relative --projects Angaben.
    # Format: DOC_VALIDATOR_PROJECTS_ROOTS="/path/a:/path/b"
    env_roots_raw = os.getenv("DOC_VALIDATOR_PROJECTS_ROOTS", "")
    env_search_roots: List[Path] = []
    if env_roots_raw.strip():
        for entry in env_roots_raw.split(os.pathsep):
            entry = entry.strip()
            if not entry:
                continue
            env_search_roots.append(Path(entry).expanduser().resolve())

    # Default ohne Hardcoding: CWD und CWD-Parent, plus optionale ENV-Roots.
    search_roots: List[Path] = [cwd, cwd.parent, *env_search_roots]

    # Subcommand-Semantik:
    # validate = nur Validierung
    # plan = Validierung + Move-Plan
    # apply = Validierung + Move-Plan + Ausführung
    # report = Validierung + Move-Plan + Report-Ausgabe
    dry_run = command != "apply"
    had_hard_errors = False
    had_violations = False
    project_results: List[Dict[str, Any]] = []
    log_written = False
    started_at = datetime.now()

    # Feature 1: project_root-Validierung gegen Traversal/ungewollte Pfade.
    for project_input in args.projects:
        project_path = Path(project_input)
        candidates: List[Path] = []

        if project_path.is_absolute():
            candidates.append(project_path.resolve())
        else:
            for root in search_roots:
                candidates.append((root / project_path).resolve())

        resolved_project = next((p for p in candidates if p.exists()), None)
        if resolved_project is None:
            if not args.json:
                print(f"Projekt nicht gefunden: {project_input}")
            had_hard_errors = True
            project_results.append(
                {
                    "project": project_input,
                    "project_root": str(project_input),
                    "status": "error",
                    "error": f"Projekt nicht gefunden: {project_input}",
                    "error_type": "project_not_found",
                    "result": None,
                    "report_path": None,
                }
            )
            continue

        project_roots.append(resolved_project)
    for project_root in project_roots:
        validator: Optional[DocStructureValidator] = None
        project_result: Dict[str, Any] = {
            "project": project_root.name,
            "project_root": str(project_root),
            "status": "ok",
            "error": None,
            "error_type": None,
            "result": None,
            "report_path": None,
        }
        try:
            validator = DocStructureValidator(
                project_root,
                dry_run=dry_run,
                verbose=args.verbose,
                rules_file=args.rules_file,
                validate_rules_schema=not args.skip_rules_schema,
                changed_only=args.changed_only,
            )

            run_maybe_silenced(validator.acquire_lock)

            if not run_maybe_silenced(validator.load_rules):
                had_hard_errors = True
                project_result["status"] = "error"
                project_result["error"] = "rules_load_failed"
                project_result["error_type"] = validator.last_error_type or "rules_load_failed"
                continue

            valid = run_maybe_silenced(validator.validate_structure)
            if not valid and not validator.violations:
                had_hard_errors = True
                project_result["status"] = "error"
                project_result["error"] = "validation_failed"
                project_result["error_type"] = "validation_failed"
                continue

            if validator.violations:
                had_violations = True
                project_result["status"] = "violations"

                if not args.json:
                    print(f"\n{len(validator.violations)} Violations gefunden")

                if command in {"plan", "apply", "report"}:
                    validator.generate_moves()

                if command in {"plan", "report"} and not args.json and not args.ci:
                    print(validator.generate_report())

                reporting_cfg = validator._reporting_cfg()
                auto_save_report = reporting_cfg.get("auto_save_report", False)
                if args.save_report or auto_save_report:
                    report_path = run_maybe_silenced(validator.save_report)
                    project_result["report_path"] = str(report_path) if report_path else None
                    if not args.json:
                        print(f"Report gespeichert: {report_path}")

                if command == "apply":
                    if not args.json:
                        print("\nWende Reorganisierung an...")

                    applied_ok = run_maybe_silenced(validator.apply_reorganization)
                    if applied_ok:
                        if not args.json:
                            print("Reorganisierung erfolgreich")
                    else:
                        if not args.json:
                            print("Reorganisierung fehlgeschlagen")
                        had_hard_errors = True
                        project_result["status"] = "error"
                        project_result["error"] = "apply_failed"
                        project_result["error_type"] = "apply_failed"

                    project_result["result"] = validator.get_machine_readable_result(applied=applied_ok)
                else:
                    project_result["result"] = validator.get_machine_readable_result(applied=None)
            else:
                if not args.json:
                    print(f"{project_root.name}: Keine Violations gefunden")
                reporting_cfg = validator._reporting_cfg()
                auto_save_report = reporting_cfg.get("auto_save_report", False)
                if args.save_report or auto_save_report:
                    report_path = run_maybe_silenced(validator.save_report)
                    project_result["report_path"] = str(report_path) if report_path else None
                    if not args.json:
                        print(f"Report gespeichert: {report_path}")

                if command in {"plan", "report"} and not args.json and not args.ci:
                    print(validator.generate_report())

                project_result["result"] = validator.get_machine_readable_result(applied=None)

        except (SecurityError, LockError) as e:
            if not args.json:
                print(f"Fehler in {project_root}: {e}")
            had_hard_errors = True
            project_result["status"] = "error"
            project_result["error"] = str(e)
            project_result["error_type"] = "runtime_error"
        finally:
            if validator is not None:
                run_maybe_silenced(validator.release_lock)
            project_results.append(project_result)

    duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)

    def write_json_runtime_log() -> bool:
        if args.log_json is None:
            return False

        try:
            target = args.log_json
            if not target.is_absolute():
                target = (Path.cwd() / target).resolve()

            if target.parent and not target.parent.exists():
                target.parent.mkdir(parents=True, exist_ok=True)

            aggregated_violations: List[Dict[str, Any]] = []
            aggregated_moves: List[Dict[str, Any]] = []
            aggregated_oplog: List[str] = []
            for project in project_results:
                result = project.get("result") or {}
                aggregated_violations.extend(result.get("violations", []))
                aggregated_moves.extend(result.get("proposed_moves", []))
                aggregated_oplog.extend(result.get("operation_log", []))

            payload = {
                "timestamp": datetime.now().isoformat(),
                "project_root": project_results[0]["project_root"] if len(project_results) == 1 else "<multiple>",
                "command": command,
                "violations": aggregated_violations,
                "moves": aggregated_moves,
                "operation_log": aggregated_oplog,
                "duration_ms": duration_ms,
            }

            with open(target, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            if not args.json:
                print(f"WARN: JSON-Log konnte nicht geschrieben werden: {e}")
            return False

    if args.log_json is not None:
        log_written = write_json_runtime_log()

    if args.json:
        payload = {
            "summary": {
                "projects_total": len(project_results),
                "projects_with_violations": sum(1 for r in project_results if r["status"] == "violations"),
                "projects_with_errors": sum(1 for r in project_results if r["status"] == "error"),
            },
            "mode": "changed_only" if args.changed_only else "full",
            "log_written": log_written,
            "projects": project_results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2 if not args.ci else None))
    elif args.ci:
        violations_projects = sum(1 for r in project_results if r["status"] == "violations")
        error_projects = sum(1 for r in project_results if r["status"] == "error")
        print(
            f"CI-SUMMARY projects={len(project_results)} "
            f"violations={violations_projects} errors={error_projects}"
        )

    # Exit-Codes:
    # 0 = sauber, keine Violations
    # 1 = Violations gefunden, aber keine harten Fehler
    # 2 = harte Fehler (Security/Lock/Load/Apply/Validierungsfehler ohne Violations)
    if had_hard_errors:
        sys.exit(2)
    if had_violations:
        sys.exit(1)
    sys.exit(0)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    main()
