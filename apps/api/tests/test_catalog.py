"""데이터 조회 계층과 web 스냅샷 검사."""

import hashlib
import json
from pathlib import Path

import pytest

from api.domain.calc import js_round
from api.domain.catalog import SNAPSHOT_PATH, ConcernId, get_catalog

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_PRICES = REPO_ROOT / "apps" / "web" / "src" / "data" / "prices.json"


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
    added_place_keys = {
        "aiRating",
        "ratingScope",
        "ratingTier",
        "openedDate",
        "channels",
        "centralChannel",
        "proofs",
    }
    assert [
        {k: v for k, v in p.items() if k not in added_place_keys}
        for p in snapshot["places"]
    ] == web["places"]


@pytest.mark.skipif(not WEB_PRICES.exists(), reason="web 데이터가 없는 환경")
def test_snapshot_sources_are_unchanged() -> None:
    """회복 기간·바늘(treatment-rules.ts), 후기 평점(ai-rating.ts·후기 데이터), calc.ts,
    예약 채널(clinic-channels.ts), 가격표 증빙(official-proofs.ts), 문의 문장(clinic-helpers.ts),
    내보내기 스크립트가 바뀌면 실패한다.

    스냅샷과 calc 기대값이 그 원본으로 만들어졌으므로 다시 내보내야 한다.
    줄바꿈을 LF로 맞춘 뒤 비교하므로 CRLF로 받은 환경에서도 같은 결과가 나온다.
    고치는 법: `pnpm --filter @repo/api export:web-data`
    """
    sources = get_catalog().sources
    assert sources
    for file, digest in sources.items():
        text = (REPO_ROOT / file).read_bytes().replace(b"\r\n", b"\n")
        actual = hashlib.sha256(text).hexdigest()
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


def test_concern_table_has_six_concerns_with_known_menus() -> None:
    """고민별 시술표(설계 문서 9장)는 web treatment-rules.ts의 CONCERN_MENUS와 같다."""
    catalog = get_catalog()
    menu_ids = {m.id for m in catalog.menus}

    assert [c.id for c in catalog.concerns] == list(ConcernId.__args__)
    for concern in catalog.concerns:
        assert concern.menu_ids
        assert set(concern.menu_ids) <= menu_ids
    # 처짐(lift)은 슈링크가 가장 직접적이고 인모드가 다음이다.
    assert catalog.concern("lift").menu_ids == ("shrink-300", "inmode-fx")


def test_unknown_menu_raises() -> None:
    with pytest.raises(KeyError):
        get_catalog().menu("nope")


def test_every_offer_has_a_price_proof() -> None:
    """가격표 증빙(web getOfficialMenuProof)은 병원이 가격을 낸 시술마다 있다."""
    for place in get_catalog().places:
        assert set(place.proofs) == set(place.prices), place.id
        for proof in place.proofs.values():
            assert proof.url_ko.startswith("http")
            assert proof.url_ja.startswith("http")


def test_only_new_clinics_have_opened_date() -> None:
    # web brand-reviews.ts NEW_CLINIC_OPENED_AT과 같다(이슈 #101).
    opened = {p.id: p.opened_date for p in get_catalog().places if p.opened_date}

    assert opened == {"vands-balsan": "2026.11.25"}
