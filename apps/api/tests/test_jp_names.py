"""일본 시술명 대응표와 대조(domain/jp_names.py, ADR 0012)."""

import pytest

from api.domain.catalog import get_catalog
from api.domain.jp_names import (
    JP_NAMES,
    UNIT_NOTES,
    JpName,
    JpNameIndex,
    find_jp_clinic,
    get_jp_names,
    menus_with_jp_clinic,
    normalize,
)

MENU_IDS = {m.id for m in get_catalog().menus}


def match(query: str):
    return get_jp_names().match(query)


def test_table_uses_only_menus_in_data_and_every_menu_has_unit_note() -> None:
    for entry in JP_NAMES:
        assert set(entry.menu_ids) <= MENU_IDS, entry.id
        assert entry.reason_ja and entry.reason_ko
    assert set(UNIT_NOTES) == MENU_IDS


def test_every_menu_has_a_direct_name() -> None:
    direct = {i for e in JP_NAMES if e.kind == "direct" for i in e.menu_ids}
    assert direct == MENU_IDS


def test_direct_entries_point_to_one_menu() -> None:
    """direct 이름은 한 시술만 가리킨다. 후보가 여럿이면 broad여야 한다."""
    for entry in JP_NAMES:
        if entry.kind == "direct":
            assert len(entry.menu_ids) == 1, entry.id


def test_same_name_in_two_entries_is_rejected() -> None:
    a = JpName("a", ("ハイフ",), ("shrink-300",), "x", "x")
    b = JpName("b", ("ハイフー",), ("potenza",), "x", "x")
    with pytest.raises(ValueError, match="both a and b"):
        JpNameIndex((a, b))


@pytest.mark.parametrize(
    "query",
    [
        "ハイフ",
        "ハイフー",  # 장음
        "ﾊｲﾌ",  # 반각 가타카나
        "HIFU",
        "hifu",
        "ＨＩＦＵ",  # 전각 영문
        "ハイフを受けたい",  # 뒤에 조사
        "ハイフ受けたい",  # 뒤에 한자
        "顔のハイフ",  # 앞에 조사
        "하이후",
        "하이후를 받고 싶어",  # 한국어 조사
        "シュリンク",
        "ウルトラフォーマー3",
        "UTIMS A3 HIFU",
    ],
)
def test_hifu_variants_match_shrink(query: str) -> None:
    result = match(query)
    assert (result.status, result.menu_ids) == ("found", ("shrink-300",))


@pytest.mark.parametrize(
    ("query", "menu_id"),
    [
        ("ブナジュ", "re2o-5cc"),
        ("BNAJU", "re2o-5cc"),
        ("リトゥオ", "re2o-5cc"),
        ("Mini FX", "inmode-fx"),
        ("miniFX", "inmode-fx"),
        ("ミニFX", "inmode-fx"),
        (
            "インモード ミニFX",
            "inmode-fx",
        ),  # 확인 필요 이름(インモード)과 함께 와도 대응
        ("エラボトックス", "jaw-botox"),
        ("사각턱 보톡스", "jaw-botox"),
        ("ララドクター", "lalapeel"),
        ("ハイドラジェントル", "aquapeel"),
        ("ポテンツァ", "potenza"),
        ("ジュベルック", "juvelook-2cc"),
        ("リジュラン", "rejuran-2cc"),
        ("リジュランHB", "rejuran-2cc"),  # 뒤가 영문이라 경계
        ("水光注射（リジュランHB）", "rejuran-2cc"),  # 주입 방식 + 약 이름이면 약으로
        ("ボツラックス エラ 25単位", "jaw-botox"),  # 넓은 이름 + 부위
        ("エラのボトックス", "jaw-botox"),
        ("エラのハイフ", "shrink-300"),  # 부위는 넓은 이름이 없으면 쓰지 않음
        ("ピーリング ララピール", "lalapeel"),
    ],
)
def test_product_and_device_names_match(query: str, menu_id: str) -> None:
    result = match(query)
    assert (result.status, result.menu_ids) == ("found", (menu_id,))


@pytest.mark.parametrize(
    ("query", "candidates"),
    [
        ("ピーリング", ("lalapeel", "aquapeel")),
        ("ボトックス", ("jaw-botox",)),  # 부위가 없으면 사각턱인지 확인
        ("ボツラックス", ("jaw-botox",)),
        ("リジュランi", ("rejuran-2cc",)),  # 눈가용은 다른 제품
        ("リジュランアイ", ("rejuran-2cc",)),
        ("水光注射", ("rejuran-2cc", "juvelook-2cc")),
        ("ウルセラ", ("shrink-300",)),  # 다른 HIFU 기기
        ("インモード", ("inmode-fx",)),
        ("ハイフとポテンツァ", ("shrink-300", "potenza")),  # 시술 두 개
        ("ウルセラ ハイフ", ("shrink-300",)),  # 다른 기기는 함께 와도 확인
        ("ハイフ ボトックス", ("shrink-300", "jaw-botox")),
        ("エラ", ("jaw-botox",)),
    ],
)
def test_broad_or_other_names_need_confirmation(query: str, candidates) -> None:
    result = match(query)
    assert (result.status, result.menu_ids) == ("ambiguous", candidates)


@pytest.mark.parametrize(
    "query",
    [
        "ダーマペン",
        "ヒアルロン酸",  # 필러
        "糸リフト",
        "ケミカルピール",  # 'ピール'은 다른 단어의 일부
        "이마보톡스",  # '보톡스'는 다른 단어의 일부, 데이터에 없는 부위
        "ハイフル",  # 'ハイフ' 뒤에 가타카나가 이어짐
        "shrinkage",
        "",
    ],
)
def test_names_not_in_table_are_unknown(query: str) -> None:
    assert match(query).status == "unknown"


def test_normalize_drops_long_vowel_and_keeps_spaces() -> None:
    assert normalize("ハイフー  全顔") == "ハイフ 全顔"
    assert normalize("ＭＩＮＩ ＦＸ") == "mini fx"


def test_benchmark_plan_names_never_match_another_menu() -> None:
    """실제 데이터의 일본 플랜명을 넣으면 그 시술로 대응하거나, 그 시술만 후보로 확인하거나, 대응 없음이다.

    다른 시술로 대응하거나 다른 시술을 후보에 넣으면 틀린 대응이다. 확인으로 나오는 플랜은 넓은 이름만 있는
    'ニューロノックス 両側', 부위만 있는 '韓国製 エラ 25単位', 눈가용 이름인 'リジュランi 1cc × 2'다.
    """
    for menu in get_catalog().menus:
        for benchmark in menu.jp_benchmarks:
            result = match(benchmark.plan_ja)
            if result.status != "unknown":
                assert result.menu_ids == (menu.id,), benchmark.plan_ja


def test_menu_japanese_names_match_their_menu() -> None:
    for menu in get_catalog().menus:
        result = match(menu.name_ja)
        assert (result.status, result.menu_ids) == ("found", (menu.id,)), menu.name_ja


def test_find_jp_clinic_by_partial_name() -> None:
    shrink = get_catalog().menu("shrink-300")

    result = find_jp_clinic(shrink, "品川")
    assert result.status == "found"
    assert (result.benchmarks[0].name_ja, result.benchmarks[0].price_jpy) == (
        "品川美容外科",
        27500,
    )
    assert find_jp_clinic(shrink, "SBC").benchmarks[0].name_ja.startswith("湘南")
    assert find_jp_clinic(shrink, "시나가와").status == "found"


def test_find_jp_clinic_with_two_matches_is_ambiguous() -> None:
    lalapeel = get_catalog().menu("lalapeel")
    result = find_jp_clinic(lalapeel, "表参道")
    assert result.status == "ambiguous"
    assert {b.name_ja for b in result.benchmarks} == {
        "表参道スキンクリニック",
        "表参道美容皮膚科",
    }


def test_find_jp_clinic_without_price_for_menu_is_unknown() -> None:
    shrink = get_catalog().menu("shrink-300")
    assert find_jp_clinic(shrink, "TAクリニック").status == "unknown"
    assert find_jp_clinic(shrink, " ").status == "unknown"
    assert [m.id for m in menus_with_jp_clinic("TAクリニック")] == ["jaw-botox"]
