"""백엔드 구조 규칙(AGENTS.md "백엔드 구조 규칙") 중 import 계약으로 못 잡는 것을 소스 AST로 검사한다.

계층 방향과 domain의 의존은 `uv run lint-imports`(pyproject의 importlinter 계약),
SSE 직접 구현 금지는 ruff `TID251`이 맡는다.
"""

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SRC = API_ROOT / "src" / "api"


def _modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(SRC.rglob("*.py"))
    ]


def _imported_names(tree: ast.Module) -> list[tuple[int, str, str]]:
    """(줄, 모듈, 이름). `import x`는 이름을 빈 문자열로 둔다."""
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name, "") for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found += [(node.lineno, node.module, alias.name) for alias in node.names]
    return found


def _http_imports(tree: ast.Module) -> list[str]:
    """서비스가 쓰면 안 되는 HTTP import. DI용 `fastapi.Depends`만 허용한다."""
    bad = []
    for line, module, name in _imported_names(tree):
        top = module.split(".")[0]
        if top == "starlette" or (
            top == "fastapi" and (module, name) != ("fastapi", "Depends")
        ):
            bad.append(f"{line}: {module}{'.' + name if name else ''}")
    return bad


def test_services_do_not_know_http() -> None:
    violations = [
        f"{path.relative_to(API_ROOT)}:{v}"
        for path, tree in _modules()
        if path.name == "service.py"
        for v in _http_imports(tree)
    ]
    assert not violations, (
        "서비스는 HTTP를 모른다. 서비스 고유 예외를 던지고 router가 HTTP 상태로 바꾼다: "
        f"{violations}"
    )


def test_http_check_catches_violations() -> None:
    allowed = ast.parse("from fastapi import Depends\nimport json")
    banned = ast.parse(
        "from fastapi import Depends, HTTPException\n"
        "from fastapi.responses import JSONResponse\n"
        "from starlette.requests import Request"
    )
    assert _http_imports(allowed) == []
    assert _http_imports(banned) == [
        "1: fastapi.HTTPException",
        "2: fastapi.responses.JSONResponse",
        "3: starlette.requests.Request",
    ]


def test_fx_provider_only_behind_get_fx_provider() -> None:
    """환율은 `get_fx_provider()` 하나로 읽는다. FxProvider는 그 함수를 정의한 모듈과 정의 모듈만 쓴다."""
    violations = []
    for path, tree in _modules():
        defines = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        if defines & {"get_fx_provider", "FxProvider"}:
            continue
        violations += [
            f"{path.relative_to(API_ROOT)}:{line}"
            for line, _, name in _imported_names(tree)
            if name == "FxProvider"
        ]
    assert not violations, f"FxProvider 대신 get_fx_provider()를 쓴다: {violations}"


def test_alembic_ini_is_ascii() -> None:
    # Windows의 Alembic은 OS 인코딩(cp949)으로 읽어 한글이 깨진다.
    files = [p for p in API_ROOT.rglob("alembic.ini") if ".venv" not in p.parts]
    bad = [str(p.relative_to(API_ROOT)) for p in files if not p.read_bytes().isascii()]
    assert not bad, f"alembic.ini에는 ASCII만 쓴다: {bad}"
