import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import shutil
import tempfile
import json
import builtins
import pytest
import subprocess

from src.validate_docs_structure import DocStructureValidator, RulesSchemaError, load_rules, main

# Hilfsfunktion zum Anlegen einer Teststruktur
@pytest.fixture
def temp_project():
    tmp_dir = tempfile.mkdtemp()
    docs_dir = Path(tmp_dir) / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    # Dummy-Ordner für alle Kategorien
    for d in ["adr", "reviews", "architecture", "deployment", "manual", "archive", "process", "prompts"]:
        (docs_dir / d).mkdir(exist_ok=True)
    yield tmp_dir
    shutil.rmtree(tmp_dir)

# Dummy YAML für die Tests
RULES_YAML = '''
required_directories:
  - adr
  - reviews
  - architecture
  - deployment
  - manual
  - archive
  - process
  - prompts
allowed_root_files:
  - README.md
  - STRUCTURE_RULES.yaml
categories:
  adr:
    file_patterns: ["adr-*.md"]
    allow_nested_categories: true
  reviews:
    file_patterns: ["*_REVIEW_*.md"]
    allow_nested_categories: true
  architecture:
    file_patterns: ["*ARCHITECTURE*.md"]
    allow_nested_categories: true
  deployment:
    file_patterns: ["*DEPLOYMENT*.md"]
    allow_nested_categories: true
  manual:
    file_patterns: ["*GUIDE*.md"]
    allow_nested_categories: true
  process:
    file_patterns: ["*PROCESS*.md"]
    allow_nested_categories: true
  prompts:
    file_patterns: ["*PROMPT*.md"]
    allow_nested_categories: true
  archive:
    allow_nested_categories: true
    is_container: true
  archive_error:
    allow_nested_categories: true
    is_container: true
validation:
  require_lowercase_directories: true
  allow_nested_categories: true
  case_sensitive: false
  language: de
  language_scope:
    __root__: de
    adr: de
    archive: any
reporting:
  auto_save_report: false
  report_location: docs
  report_format: "STRUCTURE_VALIDATION_REPORT_{timestamp}.md"
'''

def write_rules(docs_dir):
    with open(docs_dir / "STRUCTURE_RULES.yaml", "w", encoding="utf-8") as f:
        f.write(RULES_YAML)

# Test: Bug #8 v2 - Overwrite ist explizit erlaubt (bestehende Zieldatei wird überschrieben)
def test_collision_in_archive_category(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    # Ziel-Datei existiert bereits
    (docs_dir / "archive" / "architecture").mkdir(parents=True, exist_ok=True)
    existing = docs_dir / "archive" / "architecture" / "TEST_ARCHITECTURE.md"
    existing.write_text("EXISTING")
    testfile = docs_dir / "TEST_ARCHITECTURE.md"
    testfile.write_text("NEW")
    validator = DocStructureValidator(temp_project, dry_run=False)
    validator.load_rules()
    validator.validate_structure()
    validator.generate_moves()
    # Bug #8 v2: apply_reorganization SOLLTE erfolgreich sein (Overwrite erlaubt)
    result = validator.apply_reorganization()
    # Nach dem Move sollte die bestehende Datei überschrieben sein
    assert result is True
    content = (docs_dir / "archive" / "architecture" / "TEST_ARCHITECTURE.md").read_text()
    assert content == "NEW", "Overwrite sollte funktionieren"
    # Source sollte nicht mehr existieren (erfolgreich verschoben)
    assert not testfile.exists()

# Test: Fehlerhafte YAML-Konfiguration
def test_invalid_yaml(temp_project):
    docs_dir = Path(temp_project) / "docs"
    # Schreibe ungültige YAML
    with open(docs_dir / "STRUCTURE_RULES.yaml", "w", encoding="utf-8") as f:
        f.write(": invalid_yaml: [unbalanced]")
    testfile = docs_dir / "TEST_ARCHITECTURE.md"
    testfile.write_text("Test")
    # Sollte beim Laden der Regeln fehlschlagen
    validator = DocStructureValidator(temp_project, dry_run=True)
    result = validator.load_rules()
    assert result is False

# Test: Rollback bei Fehler während Verschiebung
def test_rollback_on_move_error(monkeypatch, temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    testfile = docs_dir / "TEST_ARCHITECTURE.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=False)
    validator.load_rules()
    validator.validate_structure()
    validator.generate_moves()
    # Simuliere Fehler beim Verschieben
    def fail_move(*args, **kwargs):
        raise Exception("Simulierter Fehler beim Move")
    monkeypatch.setattr(validator, "_move_with_fallback", fail_move)
    result = validator.apply_reorganization()
    # Es sollte False zurückgegeben werden und die Datei sollte noch im Root liegen
    assert result is False
    assert (docs_dir / "TEST_ARCHITECTURE.md").exists()

# Test: Datei im docs-Root wird korrekt nach archive/<kategorie>/ verschoben
def test_root_file_to_archive_category(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    # Lege eine Datei an, die exakt zum Pattern *ARCHITECTURE*.md passt
    testfile = docs_dir / "TEST_ARCHITECTURE.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    validator.validate_structure()
    validator.generate_moves()
    # Es muss ein Move nach archive/architecture/TEST_ARCHITECTURE.md vorgeschlagen werden
    targets = [str(t[1]) for t in validator.proposed_moves]
    print("DEBUG: proposed_moves:", targets)
    assert any("archive/architecture" in t.replace("\\", "/") and "TEST_ARCHITECTURE.md" in t for t in targets)

# Test: Datei im docs-Root ohne Kategorie landet in archive/misc/
def test_root_file_to_archive_default(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    testfile = docs_dir / "UNMATCHED_FILE.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    validator.validate_structure()
    validator.generate_moves()
    targets = [str(t[1]) for t in validator.proposed_moves]
    print("DEBUG: proposed_moves:", targets)
    assert any(t.replace("\\", "/").endswith("archive/misc/UNMATCHED_FILE.md") for t in targets)


def test_untracked_git_fallback_warns_once_and_summarizes(monkeypatch, temp_project, capsys):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    file_a = docs_dir / "UNMATCHED_A.md"
    file_b = docs_dir / "UNMATCHED_B.md"
    file_a.write_text("A")
    file_b.write_text("B")

    validator = DocStructureValidator(temp_project, dry_run=False)
    validator.load_rules()
    validator.git_repo = True

    def fake_run(*args, **kwargs):
        class Result:
            returncode = 1
            stderr = "fatal: not under version control"
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    validator.validate_structure()
    validator.generate_moves()
    assert validator.apply_reorganization() is True

    captured = capsys.readouterr().out
    assert captured.count("WARN: Git-Repository erkannt, aber Quelle nicht versioniert") == 1
    assert "INFO: 2 unversionierte Datei(en) wurden per shutil.move verschoben." in captured

# Test: Datei, die zu reviews passt, wird nach archive/reviews verschoben
def test_root_file_to_archive_reviews(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    # Lege eine Datei an, die exakt zum Pattern *_REVIEW_*.md passt
    testfile = docs_dir / "TEST_REVIEW_2026_REVIEW.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    validator.validate_structure()
    validator.generate_moves()
    targets = [str(t[1]) for t in validator.proposed_moves]
    print("DEBUG: proposed_moves:", targets)
    assert any("archive/reviews" in t.replace("\\", "/") and "TEST_REVIEW_2026_REVIEW.md" in t for t in targets)

# Test: Erlaubte Datei im Root wird nicht verschoben
def test_allowed_root_file(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    testfile = docs_dir / "README.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    validator.validate_structure()
    validator.generate_moves()
    targets = [str(t[1]) for t in validator.proposed_moves]
    assert not any("README.md" in t for t in targets)

# Test: Unterordner in archive werden akzeptiert, wenn allow_nested_categories true ist
def test_nested_category_in_archive(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    (docs_dir / "archive" / "architecture").mkdir(parents=True, exist_ok=True)
    testfile = docs_dir / "archive" / "architecture" / "NESTED.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    # Sollte keine violation für nested_category_not_allowed geben
    validator.validate_structure()
    assert not any(v["type"] == "nested_category_not_allowed" for v in validator.violations)

# Bug #2 v2: Category-Flag kann globales allow_nested_categories übersteuern (false → true)
def test_category_can_override_global_allow_nested_false_to_true(temp_project):
    docs_dir = Path(temp_project) / "docs"
    # Rules mit global FALSE, aber adr.allow_nested_categories = true
    custom_rules = """
required_directories: [adr, reviews, architecture, deployment, manual, archive, process, prompts]
allowed_root_files: [README.md, STRUCTURE_RULES.yaml]
categories:
  adr:
    file_patterns: ["adr-*.md"]
    allow_nested_categories: true
  archive:
    allow_nested_categories: true
validation:
  require_lowercase_directories: true
  allow_nested_categories: false
  case_sensitive: false
  language: de
reporting:
  auto_save_report: false
"""
    (docs_dir / "STRUCTURE_RULES.yaml").write_text(custom_rules)
    # Nested in adr sollte OK sein trotz global false
    (docs_dir / "adr" / "nested").mkdir(parents=True, exist_ok=True)
    testfile = docs_dir / "adr" / "nested" / "adr-001.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    validator.validate_structure()
    # Sollte NO violation sein für adr nested
    assert not any(
        v["type"] == "nested_category_not_allowed" and "adr" in v["path"]
        for v in validator.violations
    )

# Bug #2 v2: Category-Flag kann globales allow_nested_categories übersteuern (true → false)
def test_category_can_override_global_allow_nested_true_to_false(temp_project):
    docs_dir = Path(temp_project) / "docs"
    # Rules mit global TRUE, aber adr.allow_nested_categories = false
    custom_rules = """
required_directories: [adr, reviews, architecture, deployment, manual, archive, process, prompts]
allowed_root_files: [README.md, STRUCTURE_RULES.yaml]
categories:
  adr:
    file_patterns: ["adr-*.md"]
    allow_nested_categories: false
  archive:
    allow_nested_categories: true
validation:
  require_lowercase_directories: true
  allow_nested_categories: true
  case_sensitive: false
  language: de
reporting:
  auto_save_report: false
"""
    (docs_dir / "STRUCTURE_RULES.yaml").write_text(custom_rules)
    # Nested in adr sollte NICHT OK sein trotz global true
    (docs_dir / "adr" / "nested").mkdir(parents=True, exist_ok=True)
    testfile = docs_dir / "adr" / "nested" / "adr-001.md"
    testfile.write_text("Test")
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    validator.validate_structure()
    # Sollte nested_category_not_allowed violation für adr geben
    assert any(
        v["type"] == "nested_category_not_allowed" and "adr" in v["path"]
        for v in validator.violations
    )

# Bug #8 v2: Precheck erlaubt Overwrite (existierende Zieldatei wird überschrieben)
def test_precheck_allows_overwrite(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    # Ziel-Datei existiert bereits
    (docs_dir / "archive" / "architecture").mkdir(parents=True, exist_ok=True)
    target_file = docs_dir / "archive" / "architecture" / "TEST_ARCHITECTURE.md"
    target_file.write_text("EXISTING")
    # Source-Datei
    source_file = docs_dir / "TEST_ARCHITECTURE.md"
    source_file.write_text("NEW")
    
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    
    # Precheck sollte OK sein (Overwrite erlaubt)
    result = validator._precheck_move(source_file, target_file)
    assert result is True, "Precheck sollte Overwrite erlauben"

# Bug #8 v2: Precheck erlaubt neue Parent-Ordner
def test_precheck_allows_new_parent_directory(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    # Parent-Ordner existiert NICHT
    source_file = docs_dir / "TEST.md"
    source_file.write_text("Test")
    target_file = docs_dir / "archive" / "new_category" / "TEST.md"
    # archive/new_category/ existiert noch nicht
    
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    
    # Precheck sollte OK sein (Parent wird später angelegt)
    result = validator._precheck_move(source_file, target_file)
    assert result is True, "Precheck sollte neue Parent-Ordner erlauben"

# Bug #5 v2: Archive-Root-Datei erzeugt Violation UND wird in Moves übersetzt
def test_archive_root_file_generates_move(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    # Bug #5: archive muss is_container: true haben in Rules
    # Test-Datei direkt in archive/
    (docs_dir / "archive").mkdir(exist_ok=True)
    testfile = docs_dir / "archive" / "OLD_FILE.md"
    testfile.write_text("Test")
    
    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()
    validator.validate_structure()
    validator.generate_moves()
    
    # Sollte archive_root_file_forbidden violation geben
    assert any(
        v["type"] == "archive_root_file_forbidden" and "OLD_FILE.md" in v["path"]
        for v in validator.violations
    )
    
    # Sollte auch in proposed_moves sein (→ archive_error/)
    targets = [str(t[1]) for t in validator.proposed_moves]
    assert any("archive_error" in t for t in targets), f"Move sollte zu archive_error gehen, got: {targets}"


def test_git_overwrite_uses_git_mv_force_or_fails_without_shutil_fallback(monkeypatch, temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    source = docs_dir / "TEST_ARCHITECTURE.md"
    source.write_text("NEW")
    target_dir = docs_dir / "archive" / "architecture"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "TEST_ARCHITECTURE.md"
    target.write_text("EXISTING")

    validator = DocStructureValidator(temp_project, dry_run=False)
    validator.load_rules()
    validator.git_repo = True

    called = {"cmd": None, "shutil_called": False}

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd

        class Result:
            returncode = 1
            stderr = "simulated git overwrite failure"

        return Result()

    def fail_shutil_move(*args, **kwargs):
        called["shutil_called"] = True
        raise AssertionError("shutil.move darf bei Git-Overwrite-Fehler nicht aufgerufen werden")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "move", fail_shutil_move)

    with pytest.raises(RuntimeError):
        validator._move_with_fallback(source, target)

    assert called["cmd"] is not None
    assert called["cmd"][0:2] == ["git", "mv"]
    assert "-f" in called["cmd"], "Overwrite im Git-Repo muss git mv -f verwenden"
    assert called["shutil_called"] is False


def test_git_mv_no_overwrite_still_has_no_shutil_fallback_on_error(monkeypatch, temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    source = docs_dir / "TEST_ARCHITECTURE.md"
    source.write_text("NEW")
    target_dir = docs_dir / "archive" / "architecture"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "TEST_ARCHITECTURE.md"
    if target.exists():
        target.unlink()

    validator = DocStructureValidator(temp_project, dry_run=False)
    validator.load_rules()
    validator.git_repo = True

    called = {"cmd": None, "shutil_called": False}

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd

        class Result:
            returncode = 1
            stderr = "simulated git move failure"

        return Result()

    def fail_shutil_move(*args, **kwargs):
        called["shutil_called"] = True
        raise AssertionError("shutil.move darf bei Git-Fehler nicht aufgerufen werden")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "move", fail_shutil_move)

    with pytest.raises(RuntimeError):
        validator._move_with_fallback(source, target)

    assert called["cmd"] is not None
    assert called["cmd"][0:2] == ["git", "mv"]
    assert "-f" not in called["cmd"], "Ohne existierendes Ziel darf kein -f gesetzt werden"
    assert called["shutil_called"] is False


def test_archive_error_is_container(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()

    assert validator._is_archive_container("archive_error") is True


def test_archive_is_never_target_category(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    validator = DocStructureValidator(temp_project, dry_run=True)
    validator.load_rules()

    # Simuliere Fehlkonfiguration: archive bekommt ein Pattern.
    validator.rules.setdefault("categories", {}).setdefault("archive", {})["file_patterns"] = ["*ARCHIVE*.md"]

    result = validator._find_file_target_directory("SHOULD_ARCHIVE.md")
    assert result == "", "Container-Kategorie archive darf niemals Zielkategorie sein"


def test_rules_schema_valid(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    validator = DocStructureValidator(temp_project, dry_run=True)
    assert validator.load_rules() is True
    assert validator.last_error_type is None


def test_rules_schema_invalid(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    invalid_rules = (docs_dir / "STRUCTURE_RULES.yaml").read_text(encoding="utf-8")
    invalid_rules = invalid_rules.replace("required_directories:\n  - adr", "required_directories: adr")
    (docs_dir / "STRUCTURE_RULES.yaml").write_text(invalid_rules, encoding="utf-8")

    validator = DocStructureValidator(temp_project, dry_run=True)
    assert validator.load_rules() is False
    assert validator.last_error_type == "rules_schema_invalid"


def test_rules_schema_skip_flag(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    invalid_rules = (docs_dir / "STRUCTURE_RULES.yaml").read_text(encoding="utf-8")
    invalid_rules = invalid_rules.replace("required_directories:\n  - adr", "required_directories: adr")
    (docs_dir / "STRUCTURE_RULES.yaml").write_text(invalid_rules, encoding="utf-8")

    validator = DocStructureValidator(temp_project, dry_run=True, validate_rules_schema=False)
    assert validator.load_rules() is True


def test_rules_schema_api_exception(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    invalid_rules = (docs_dir / "STRUCTURE_RULES.yaml").read_text(encoding="utf-8")
    invalid_rules = invalid_rules.replace("required_directories:\n  - adr", "required_directories: adr")
    (docs_dir / "STRUCTURE_RULES.yaml").write_text(invalid_rules, encoding="utf-8")

    with pytest.raises(RulesSchemaError):
      load_rules(temp_project, validate_schema=True)


def test_changed_only_filters_files(monkeypatch, temp_project):
    project_root = Path(temp_project)
    docs_dir = project_root / "docs"
    write_rules(docs_dir)
    (project_root / ".git").mkdir()

    (docs_dir / "README.md").write_text("ok", encoding="utf-8")
    (docs_dir / "UNMATCHED_FILE.md").write_text("bad", encoding="utf-8")

    class Result:
      returncode = 0
      stdout = "docs/README.md\nREADME.md\nsrc/x.py\n"
      stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())

    validator = DocStructureValidator(temp_project, dry_run=True, changed_only=True)
    assert validator.load_rules() is True
    assert validator.validate_structure() is True
    assert all("UNMATCHED_FILE.md" not in v.get("path", "") for v in validator.violations)
    assert len(validator.checked_files) == 1
    assert validator.checked_files[0].replace("\\", "/").endswith("docs/README.md")


def test_changed_only_skips_global_checks(monkeypatch, temp_project):
    project_root = Path(temp_project)
    docs_dir = project_root / "docs"
    write_rules(docs_dir)
    (project_root / ".git").mkdir()

    shutil.rmtree(docs_dir / "prompts")
    (docs_dir / "README.md").write_text("ok", encoding="utf-8")

    class Result:
      returncode = 0
      stdout = "docs/README.md\n"
      stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())

    validator = DocStructureValidator(temp_project, dry_run=True, changed_only=True)
    assert validator.load_rules() is True
    validator.validate_structure()
    assert not any(v.get("type") == "missing_directory" for v in validator.violations)


def test_changed_only_no_changes_returns_zero(temp_project):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)

    old_argv = sys.argv
    sys.argv = [
        "validate_docs_structure.py",
        "validate",
        "--projects",
        temp_project,
        "--changed-only",
    ]
    try:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = old_argv


def test_json_log_written(monkeypatch, temp_project, tmp_path):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    (docs_dir / "README.md").write_text("ok", encoding="utf-8")
    log_path = tmp_path / "runtime-log.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_docs_structure.py",
            "validate",
            "--projects",
            temp_project,
            "--log-json",
            str(log_path),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert log_path.exists()

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["command"] == "validate"
    assert "timestamp" in payload
    assert "duration_ms" in payload


def test_json_log_contains_operations(monkeypatch, temp_project, tmp_path):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    (docs_dir / "UNMATCHED_FILE.md").write_text("bad", encoding="utf-8")
    log_path = tmp_path / "runtime-log-ops.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_docs_structure.py",
            "plan",
            "--projects",
            temp_project,
            "--log-json",
            str(log_path),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert isinstance(payload.get("operation_log"), list)
    assert len(payload["operation_log"]) > 0


def test_json_log_written_on_error(monkeypatch, tmp_path):
    log_path = tmp_path / "runtime-log-error.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_docs_structure.py",
            "validate",
            "--projects",
            "/definitely/not/existing/project",
            "--log-json",
            str(log_path),
            "--json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert log_path.exists()

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["command"] == "validate"
    assert payload["violations"] == []


def test_json_log_path_invalid_warns_but_continues(monkeypatch, temp_project, capsys, tmp_path):
    docs_dir = Path(temp_project) / "docs"
    write_rules(docs_dir)
    (docs_dir / "README.md").write_text("ok", encoding="utf-8")

    log_path = tmp_path / "runtime-log-invalid.json"
    real_open = builtins.open

    def fail_log_open(file, mode="r", *args, **kwargs):
        if str(file) == str(log_path) and "w" in mode:
            raise OSError("simulierter Schreibfehler")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_log_open)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_docs_structure.py",
            "validate",
            "--projects",
            temp_project,
            "--log-json",
            str(log_path),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "WARN: JSON-Log konnte nicht geschrieben werden" in captured.out