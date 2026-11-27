"""모든 테스트에 공통으로 적용하는 설정."""

import importlib

import pytest

from api.domain.catalog import get_catalog
from api.domain.fx import FxProvider

# api.agent.tools 패키지는 같은 이름의 도구 객체를 내보내므로 모듈은 이름으로 가져온다.
search_clinics_module = importlib.import_module("api.agent.tools.search_clinics")


@pytest.fixture(autouse=True)
def snapshot_fx(monkeypatch: pytest.MonkeyPatch) -> None:
    """개발자 .env의 WEB_FX_URL과 상관없이 스냅샷 환율로 테스트한다.

    기대값(calc-expected.json)과 rank_rows 기본값이 스냅샷 환율이기 때문이다.
    환율 조회 자체는 tests/test_fx.py가 가짜 응답으로 검사한다.
    """
    provider = FxProvider(None, get_catalog().fx)
    monkeypatch.setattr(search_clinics_module, "get_fx_provider", lambda: provider)
