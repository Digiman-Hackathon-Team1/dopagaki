"""데이터 조회 계층과 web 스냅샷 검사."""

import hashlib
import json
from pathlib import Path

import pytest

from api.domain.calc import js_round
from api.domain.catalog import SNAPSHOT_PATH, get_catalog

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
WEB_PRICES = WEB_DIR / "src" / "data" / "prices.json"


@pytest.mark.skipif(not WEB_PRICES.exists(), reason="web 데이터가 없는 환경")
def test_snapshot_matches_web_prices_json() -> None:
    """web prices.json이 바뀌었는데 스냅샷을 다시 내보내지 않았으면 실패한다.

    고치는 법: `pnpm --filter @repo/api export:web-data`
    환율(naver-fx.json)은 `pnpm build` 때마다 바뀌므로 대조하지 않는다.
    """
    web = json.loads(WEB_PRICES.read_text("utf-8"))
    snapshot = json.loads(SNAPSHOT_PATH.read_text("utf-8"))

    assert snapshot["meta"] == web["meta"]
    assert snapshot["areas"] == web["areas"]
    added_menu_keys = {"downtimeMaxDays", "usesNeedle"}
    assert [
        {k: v for k, v in m.items() if k not in added_menu_keys}
        for m in snapshot["menus"]
    ] == web["menus"]
    assert [
        {k: v for k, v in p.items() if k != "aiRating"} for p in snapshot["places"]
    ] == web["places"]


@pytest.mark.skipif(not WEB_PRICES.exists(), reason="web 데이터가 없는 환경")
def test_snapshot_sources_are_unchanged() -> None:
    """회복 기간·바늘(concierge.ts), 후기 평점(ai-rating.ts·후기 데이터), calc.ts가 바뀌면 실패한다.

    스냅샷과 calc 기대값이 그 원본으로 만들어졌으므로 다시 내보내야 한다.
    고치는 법: `pnpm --filter @repo/api export:web-data`
    """
    sources = get_catalog().sources
    assert sources
    for file, digest in sources.items():
        actual = hashlib.sha256((WEB_DIR / file).read_bytes()).hexdigest()
        assert actual == digest, f"{file}이 바뀌었습니다. 스냅샷을 다시 내보내세요"


def test_catalog_loads_every_menu_and_place() -> None:
    catalog = get_catalog()

    assert len(catalog.menus) == 9
    assert len(catalog.places) == 137
    assert catalog.fx.krw_per_100_jpy > 0
    for menu in catalog.menus:
        # 일본 비교 기준은 일본 주요 병원 공식가 평균이다(설계 문서 4.7).
        prices = [b.price_jpy for b in menu.jp_benchmarks]
        assert len(prices) >= 5
        assert menu.jp_benchmark_jpy == js_round(sum(prices) / len(prices))


def test_unknown_menu_raises() -> None:
    with pytest.raises(KeyError):
        get_catalog().menu("nope")
