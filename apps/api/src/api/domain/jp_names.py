"""일본 시술명 → 한국 시술 대응표와 대조(설계 문서 4.3, 9장, FR-09, ADR 0012).

이름은 `web-snapshot.json`의 일본 벤치마크 플랜명(`jp_benchmarks[].plan_ja`)과 메뉴 이름에서 시작했다.
표에 없는 이름은 "대응 없음"이다. 비슷해 보여도 지어내서 대응하지 않는다.

이름의 종류(`kind`)
- direct: 같은 시술의 다른 이름. 맞으면 그 시술로 본다.
- broad: 범위가 넓은 이름(ボトックス, ピーリング, 水光注射, インモード). 혼자 오면 사용자에게 확인하고,
  더 좁은 이름과 함께 오면 그 이름을 따른다('水光注射（リジュランHB）' → 리쥬란).
- other: 데이터에 없는 다른 기기·제품(ウルセラ, リジュランアイ). 비슷한 시술이 있어도 같은 시술로 보지 않고
  항상 사용자에게 확인한다.
- part: 시술 부위(エラ). broad 이름과 함께 올 때만 범위를 좁힌다('ボツラックス エラ' → 사각턱 보톡스).
  혼자 오거나 다른 시술과 오면 대응에 쓰지 않는다('エラのハイフ' → 슈링크).

대조 규칙
- 비교 전에 전각·반각과 대소문자를 맞추고(NFKC + 소문자), 장음 부호(ー)를 지운다. ハイフ = ハイフー = ﾊｲﾌ.
  공백은 지우지 않는다. 지우면 다른 단어의 일부에도 맞기 때문이다(ADR 0011). 공백 변형은 별칭으로 둔다.
- 이름 앞이 같은 문자 종류(가타카나, 영문·숫자, 한글, 한자)면 다른 단어의 일부로 보고 버린다.
  'ケミカルピール'의 'ピール', '이마보톡스'의 '보톡스'는 맞지 않는다. 히라가나(조사)는 경계다.
- 이름 뒤는 가타카나·영문·한자 이름일 때만 같은 문자 종류가 오면 버린다('ハイフ受けたい'는 맞고 'ハイフル'·
  '水光肌'은 아님). 한글은 조사가 붙으므로('하이후를') 뒤를 보지 않는다. 숫자는 영문과 다른 종류다('HIFU300').
- 다른 이름 안에 든 이름은 버린다. 'エラボトックス' → エラボトックス(안의 ボトックス는 버림).
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from api.domain.catalog import JpBenchmark, Menu, get_catalog


@dataclass(frozen=True)
class UnitNote:
    """시술별 한국·일본 단위 차이. 근거는 `jp_benchmarks[].plan_ja`의 단위 표기다."""

    ja: str
    ko: str


@dataclass(frozen=True)
class JpName:
    """대응표의 한 줄: 같은 뜻의 이름들 → 한국 시술."""

    id: str
    names: tuple[str, ...]
    menu_ids: tuple[str, ...]
    reason_ja: str
    reason_ko: str
    kind: Literal["direct", "broad", "other", "part"] = "direct"
    # 일본 벤치마크 플랜명에 이 이름이 있을 때, 그 병원 가격과 직접 비교하며 함께 알릴 제품 차이(설계 4.7).
    compare_note: "UnitNote | None" = None


UNIT_NOTES: dict[str, UnitNote] = {
    "rejuran-2cc": UnitNote(
        ja="韓国は2cc基準です。日本は1cc単位のクリニックもあり、その場合は1cc×2で比べています。",
        ko="한국은 2cc 기준이에요. 일본은 1cc 단위인 곳도 있어 그곳은 1cc×2로 비교해요.",
    ),
    "shrink-300": UnitNote(
        ja="韓国は300ショット基準です。日本はクリニックにより300ショット・650ショットや「全顔」単位です。",
        ko="한국은 300샷 기준이에요. 일본은 병원마다 300샷·650샷이나 '전체 얼굴' 단위예요.",
    ),
    "potenza": UnitNote(
        ja="韓国は全顔1回基準です。日本はモードやチップで価格が変わり、両頬単位のクリニックもあります。",
        ko="한국은 전체 얼굴 1회 기준이에요. 일본은 모드·팁에 따라 가격이 다르고 양 볼 단위인 곳도 있어요.",
    ),
    "jaw-botox": UnitNote(
        ja="韓国は50単位基準です。日本は20〜40単位のプランが多く、単位数が違います。",
        ko="한국은 50단위 기준이에요. 일본은 20~40단위 플랜이 많아 단위 수가 달라요.",
    ),
    "juvelook-2cc": UnitNote(
        ja="韓国・日本とも2cc基準です。", ko="한국·일본 모두 2cc 기준이에요."
    ),
    "re2o-5cc": UnitNote(
        ja="韓国・日本とも1本基準です。日本は「全顔1回」と書くクリニックもあります。",
        ko="한국·일본 모두 1병 기준이에요. 일본은 '전체 얼굴 1회'로 적는 곳도 있어요.",
    ),
    "inmode-fx": UnitNote(
        ja="韓国・日本とも1部位1回基準です。",
        ko="한국·일본 모두 1부위 1회 기준이에요.",
    ),
    "lalapeel": UnitNote(
        ja="韓国は1回基準です。日本は顔全体または顔＋首の1回です。",
        ko="한국은 1회 기준이에요. 일본은 얼굴 전체 또는 얼굴+목 1회예요.",
    ),
    "aquapeel": UnitNote(
        ja="韓国・日本とも全顔1回基準です。",
        ko="한국·일본 모두 전체 얼굴 1회 기준이에요.",
    ),
}

SAME_NAME_JA = "日本でも同じ名前で呼ばれています。"
SAME_NAME_KO = "일본에서도 같은 이름으로 불러요."

JP_NAMES: tuple[JpName, ...] = (
    JpName(
        id="hifu",
        # 플랜명: 脂肪溶解リニアハイフ, ウルトラスキンハイフ, UTIMS A3 HIFU, ウルトラフォーマー3, ドクターハイフ
        names=(
            "ハイフ",
            "はいふ",
            "HIFU",
            "リニアハイフ",
            "ウルトラスキンハイフ",
            "ドクターハイフ",
            "UTIMS",
            "ウルトラフォーマー",
            "Ultraformer",
            "シュリンク",
            "シュリンクユニバース",
            "Shurink",
            "Shrink",
            "하이후",
            "하이푸",
            "슈링크",
            "슈링크 유니버스",
            "슈링크유니버스",
        ),
        menu_ids=("shrink-300",),
        reason_ja=(
            "日本は技術の名前（HIFU・ハイフ）で、韓国は機器の名前（シュリンク）で呼ぶためです。"
        ),
        reason_ko="일본은 기술 이름(HIFU·하이후)으로, 한국은 기기 이름(슈링크)으로 부르기 때문이에요.",
    ),
    JpName(
        id="ulthera",
        names=("ウルセラ", "ウルセラピー", "Ulthera", "울쎄라", "울세라"),
        menu_ids=("shrink-300",),
        reason_ja=(
            "ウルセラは別の会社のHIFU機器で、データにはありません。同じHIFU方式のシュリンクならあります。"
        ),
        reason_ko="울쎄라는 다른 회사의 HIFU 기기라 데이터에 없어요. 같은 HIFU 방식인 슈링크는 있어요.",
        kind="other",
    ),
    JpName(
        id="rejuran",
        # 플랜명: リジュランHB, 水光注射（リジュランHB）, リジュラン 2cc
        names=(
            "リジュラン",
            "リジュランヒーラー",
            "Rejuran",
            "리쥬란",
            "리쥬란 힐러",
            "리쥬란힐러",
        ),
        menu_ids=("rejuran-2cc",),
        reason_ja=SAME_NAME_JA,
        reason_ko=SAME_NAME_KO,
    ),
    JpName(
        id="rejuran-eye",
        # 리쥬란 아이는 눈가용 제품이다. 데이터의 리쥬란은 힐러라 바꿔 답하지 않는다(설계 4.2).
        names=(
            "リジュランi",
            "リジュランアイ",
            "Rejuran i",
            "리쥬란i",
            "리쥬란 i",
            "리쥬란 아이",
            "리쥬란아이",
        ),
        menu_ids=("rejuran-2cc",),
        reason_ja="リジュランアイ（目元用）はデータにありません。データのリジュランはヒーラーです。",
        reason_ko="리쥬란 아이(눈가용)는 데이터에 없어요. 데이터의 리쥬란은 힐러예요.",
        kind="other",
        # SBC 리쥬란 벤치마크 'リジュランi 1cc × 2'. SBC 공식 페이지: リジュランi는 PN 2%, 눈가에 권함.
        # SBC는 리쥬란 힐러를 팔지 않아 가장 가까운 이 제품으로 비교한다(ADR 0012).
        compare_note=UnitNote(
            ja=(
                "このクリニックの価格はリジュランヒーラーではなく、同じPN 2%で目元向けの"
                "リジュランiの価格です。"
            ),
            ko=(
                "이 병원 가격은 리쥬란 힐러가 아니라 같은 성분 농도(PN 2%)의 눈가용 "
                "리쥬란 i 가격이에요."
            ),
        ),
    ),
    JpName(
        id="skin-booster-injection",
        # 水光注射는 주입 방식 이름이다. 플랜명 '水光注射（リジュランHB）'처럼 약 이름이 함께 오면 그 약으로 본다.
        names=("水光注射", "水光", "물광 주사", "물광주사"),
        menu_ids=("rejuran-2cc", "juvelook-2cc"),
        reason_ja="水光注射は注入方法の名前で、入れる薬によって韓国の施術が変わります。",
        reason_ko="물광 주사는 주입 방식 이름이라 넣는 약에 따라 한국 시술이 달라요.",
        kind="broad",
    ),
    JpName(
        id="potenza",
        names=("ポテンツァ", "ポテンツア", "Potenza", "포텐자"),
        menu_ids=("potenza",),
        reason_ja=SAME_NAME_JA,
        reason_ko=SAME_NAME_KO,
    ),
    JpName(
        id="jaw-botox",
        names=(
            "エラボトックス",
            "エラボト",
            "エラ ボトックス",
            "사각턱 보톡스",
            "사각턱보톡스",
            "턱 보톡스",
            "턱보톡스",
        ),
        menu_ids=("jaw-botox",),
        reason_ja="日本は部位の名前（エラ）を付けて、韓国は「四角あごボトックス」と呼びます。",
        reason_ko="일본은 부위 이름(エラ)을 붙여 부르고, 한국은 '사각턱 보톡스'라고 해요.",
    ),
    JpName(
        id="botox",
        # 플랜명: ボツラックス, ニューロノックス. 부위가 없으면 사각턱인지 확인한다.
        names=(
            "ボトックス",
            "ボツラックス",
            "ニューロノックス",
            "ボツリヌス",
            "Botox",
            "보톡스",
        ),
        menu_ids=("jaw-botox",),
        reason_ja="データにあるのはエラ（四角あご）ボトックスだけです。額・目元など他の部位はありません。",
        reason_ko="데이터에는 사각턱 보톡스만 있어요. 이마·눈가 같은 다른 부위는 없어요.",
        kind="broad",
    ),
    JpName(
        id="jaw",
        names=("エラ", "사각턱"),
        menu_ids=("jaw-botox",),
        reason_ja="日本は部位の名前（エラ）を付けて、韓国は「四角あごボトックス」と呼びます。",
        reason_ko="일본은 부위 이름(エラ)을 붙여 부르고, 한국은 '사각턱 보톡스'라고 해요.",
        kind="part",
    ),
    JpName(
        id="juvelook",
        names=(
            "ジュベルック",
            "ジュベルックスキン",
            "Juvelook",
            "쥬베룩",
            "쥬베룩 스킨",
            "쥬베룩스킨",
        ),
        menu_ids=("juvelook-2cc",),
        reason_ja=SAME_NAME_JA,
        reason_ko=SAME_NAME_KO,
    ),
    JpName(
        id="juvelook-volume",
        # 데이터의 쥬베룩은 스킨이다. 볼륨은 다른 제품이라 바꿔 답하지 않는다.
        names=(
            "ジュベルックボリューム",
            "ジュベルック ボリューム",
            "Juvelook Volume",
            "쥬베룩 볼륨",
            "쥬베룩볼륨",
        ),
        menu_ids=("juvelook-2cc",),
        reason_ja="ジュベルックボリュームは別の製品で、データにありません。データのジュベルックはスキンです。",
        reason_ko="쥬베룩 볼륨은 다른 제품이라 데이터에 없어요. 데이터의 쥬베룩은 스킨이에요.",
        kind="other",
    ),
    JpName(
        id="re2o",
        # 메뉴 이름: ブナジュ 1本 (韓国名: リトゥオ / Re2O). 플랜명: BNAJU（ブナジュ）
        names=("ブナジュ", "BNAJU", "リトゥオ", "Re2O", "리투오", "부나주"),
        menu_ids=("re2o-5cc",),
        reason_ja="同じ製品を日本では「ブナジュ」、韓国では「リトゥオ」と呼びます。",
        reason_ko="같은 제품을 일본에서는 '부나주(ブナジュ)', 한국에서는 '리투오'라고 불러요.",
    ),
    JpName(
        id="inmode-fx",
        # 플랜명: インモード ミニFX, Mini FX, miniFX
        names=(
            "ミニFX",
            "Mini FX",
            "MiniFX",
            "インモードFX",
            "インモード FX",
            "InMode FX",
            "인모드 FX",
            "인모드FX",
            "미니 FX",
            "미니FX",
        ),
        menu_ids=("inmode-fx",),
        reason_ja="同じ機器です。日本は「ミニFX」とハンドピースの名前で呼ぶクリニックが多いです。",
        reason_ko="같은 기기예요. 일본은 '미니FX'처럼 핸드피스 이름으로 부르는 곳이 많아요.",
    ),
    JpName(
        id="inmode",
        names=("インモード", "InMode", "인모드"),
        menu_ids=("inmode-fx",),
        reason_ja="インモードは機器の名前で、FX・フォーマなど施術がいくつかあります。データにあるのはFXだけです。",
        reason_ko="인모드는 기기 이름이라 FX·포르마 등 시술이 여럿이에요. 데이터에는 FX만 있어요.",
        kind="broad",
    ),
    JpName(
        id="lalapeel",
        # 플랜명: ララドクター
        names=("ララピール", "ララドクター", "LaLaPeel", "라라필"),
        menu_ids=("lalapeel",),
        reason_ja="同じピーリングです。日本では製品名「ララドクター」と呼ぶクリニックもあります。",
        reason_ko="같은 필링이에요. 일본에서는 제품 이름 '라라닥터(ララドクター)'로 부르는 곳도 있어요.",
    ),
    JpName(
        id="aquapeel",
        # 플랜명: ハイドラジェントル
        names=("アクアピール", "ハイドラジェントル", "AquaPeel", "아쿠아필"),
        menu_ids=("aquapeel",),
        reason_ja="日本では機器の名前「ハイドラジェントル」と呼ぶクリニックが多いです。",
        reason_ko="일본에서는 기기 이름 '하이드라젠틀(ハイドラジェントル)'로 부르는 곳이 많아요.",
    ),
    JpName(
        id="hydrafacial",
        names=("ハイドラフェイシャル", "HydraFacial", "하이드라페이셜"),
        menu_ids=("aquapeel",),
        reason_ja="ハイドラフェイシャルは別の機器で、データにありません。似た水流ピーリングのアクアピールならあります。",
        reason_ko="하이드라페이셜은 다른 기기라 데이터에 없어요. 비슷한 물 필링인 아쿠아필은 있어요.",
        kind="other",
    ),
    JpName(
        id="peeling",
        names=("ピーリング", "필링"),
        menu_ids=("lalapeel", "aquapeel"),
        reason_ja="ピーリングは種類が多く、データにはララピールとアクアピールがあります。",
        reason_ko="필링은 종류가 많아요. 데이터에는 라라필과 아쿠아필이 있어요.",
        kind="broad",
    ),
)


def normalize(text: str) -> str:
    """비교용: NFKC + 소문자, 장음 부호(ー) 삭제, 공백은 하나로 줄여 남긴다."""
    text = unicodedata.normalize("NFKC", text).lower().replace("ー", "")
    return re.sub(r"\s+", " ", text).strip()


Script = Literal["katakana", "latin", "digit", "hangul", "kanji"]


def _script(char: str) -> Script | None:
    """이름의 일부가 될 수 있는 글자의 종류. None이면 경계(공백, 기호, 히라가나).

    숫자는 영문과 따로 본다. 'HIFU300'처럼 단위 숫자가 붙어도 이름으로 찾기 위해서다.
    """
    if "ァ" <= char <= "ヺ":
        return "katakana"
    if char.isascii() and char.isdigit():
        return "digit"
    if char.isascii() and char.isalpha():
        return "latin"
    if "가" <= char <= "힣":
        return "hangul"
    if "一" <= char <= "鿿":
        return "kanji"
    return None


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    entry: JpName


@dataclass(frozen=True)
class JpMatch:
    status: Literal["found", "ambiguous", "unknown"]
    # found: 대응 시술 하나. ambiguous: 사용자에게 확인할 후보(하나일 수도 있다).
    menu_ids: tuple[str, ...]
    # 맞은 대응표 줄. 이유를 설명할 때 쓴다.
    entries: tuple[JpName, ...]


class JpNameIndex:
    def __init__(self, entries: tuple[JpName, ...] = JP_NAMES) -> None:
        self.entries = entries
        self.aliases: dict[str, JpName] = {}
        for entry in entries:
            for name in entry.names:
                alias = normalize(name)
                other = self.aliases.get(alias)
                if other is not None and other.id != entry.id:
                    raise ValueError(f"{name!r} is in both {other.id} and {entry.id}")
                self.aliases[alias] = entry

    def _matches(self, text: str) -> list[_Match]:
        matches = []
        for alias, entry in self.aliases.items():
            start = text.find(alias)
            while start != -1:
                end = start + len(alias)
                before = _script(text[start - 1]) if start > 0 else None
                after = _script(text[end]) if end < len(text) else None
                head, tail = _script(alias[0]), _script(alias[-1])
                if not (before and before == head) and not (
                    tail in ("katakana", "latin", "kanji") and after == tail
                ):
                    matches.append(_Match(start, end, entry))
                start = text.find(alias, start + 1)
        return matches

    def match(self, query: str) -> JpMatch:
        """일본 시술명 → 한국 시술. 같은 표현에는 항상 같은 결과다.

        - 맞은 이름이 없으면 unknown.
        - other 이름이 있으면 ambiguous(다른 제품이라 항상 확인).
        - direct 이름이 서로 다른 시술을 가리키면 ambiguous('ハイフとポテンツァ').
        - direct 이름이 한 시술을 가리키고 broad 이름이 모두 그 시술을 후보로 두면 found.
          예: '水光注射（リジュランHB）' → 리쥬란, 'インモード ミニFX' → 인모드 FX.
        - direct 없이 broad·part 이름이 둘 이상이고 공통 후보가 하나면 found('ボツラックス エラ').
        - 그 밖에는 ambiguous. 후보는 맞은 이름이 가리키는 시술 전부다.
        - part 이름은 broad 이름이 없으면 대응에 쓰지 않는다. part만 있으면 ambiguous로 확인한다.
        """
        matches = self._matches(normalize(query))
        kept = [
            m
            for m in matches
            if not any(
                o.start <= m.start
                and m.end <= o.end
                and o.end - o.start > m.end - m.start
                for o in matches
            )
        ]
        kept.sort(key=lambda m: m.start)
        entries = tuple(dict.fromkeys(m.entry for m in kept))
        if not entries:
            return JpMatch(status="unknown", menu_ids=(), entries=())
        kinds = {e.kind for e in entries}
        if "part" in kinds and kinds != {"part"} and "broad" not in kinds:
            entries = tuple(e for e in entries if e.kind != "part")

        def result(status: Literal["found", "ambiguous"], menu_ids) -> JpMatch:
            return JpMatch(status=status, menu_ids=tuple(menu_ids), entries=entries)

        candidates = dict.fromkeys(i for e in entries for i in e.menu_ids)
        if any(e.kind == "other" for e in entries):
            return result("ambiguous", candidates)
        direct = dict.fromkeys(
            i for e in entries if e.kind == "direct" for i in e.menu_ids
        )
        narrowing = [e for e in entries if e.kind in ("broad", "part")]
        if len(direct) == 1:
            (menu_id,) = direct
            if all(menu_id in e.menu_ids for e in narrowing):
                return result("found", direct)
            return result("ambiguous", candidates)
        if not direct and len(narrowing) >= 2:
            common = set.intersection(*(set(e.menu_ids) for e in narrowing))
            if len(common) == 1:
                return result("found", common)
        return result("ambiguous", candidates)


@lru_cache
def get_jp_names() -> JpNameIndex:
    return JpNameIndex()


@dataclass(frozen=True)
class JpClinicMatch:
    status: Literal["found", "ambiguous", "unknown"]
    benchmarks: tuple[JpBenchmark, ...]


# 병원 이름에 흔히 붙는 말. 이 말만으로는 병원을 가리지 않는다('皮膚科', 'A CLINIC'의 'clinic').
_CLINIC_GENERIC = (
    "美容外科",
    "美容皮膚科",
    "美容クリニック",
    "スキンクリニック",
    "クリニック",
    "皮膚科",
    "外科",
    "美容",
    "clinic",
    "skin",
    "미용외과",
    "미용피부과",
    "미용클리닉",
    "스킨클리닉",
    "클리닉",
    "피부과",
    "미용",
    "의원",
)


def _clinic_normalize(text: str) -> str:
    """병원 이름 비교용. 괄호는 공백으로 바꾼다('湘南美容クリニック（SBC）' = '湘南美容クリニック (SBC)')."""
    return normalize(re.sub(r"[()（）]", " ", unicodedata.normalize("NFKC", text)))


# 데이터에서 같은 병원 그룹이 시술마다 다른 이름으로 적힌 경우의 별칭. 'SBC NEO Skin Clinic'은
# 湘南美容クリニック(SBC)와 같은 병원으로 본다(사용자 결정, ADR 0012).
_CLINIC_ALIASES: dict[str, str] = {
    "SBC NEO Skin Clinic": "湘南美容クリニック 쇼난미용클리닉",
}


def _clinic_text(benchmark: JpBenchmark) -> str:
    alias = _CLINIC_ALIASES.get(benchmark.name_ja, "")
    return _clinic_normalize(f"{benchmark.name_ja} {benchmark.name_ko} {alias}")


def compare_note(benchmark: JpBenchmark) -> UnitNote | None:
    """이 일본 병원 플랜이 한국 시술과 다른 제품 이름이면, 직접 비교할 때 함께 알릴 문장."""
    for entry in get_jp_names().match(benchmark.plan_ja).entries:
        if entry.compare_note:
            return entry.compare_note
    return None


def _strip_generic(token: str) -> str:
    """'湘南美容外科' → '湘南', 'ビアンカクリニック' → 'ビアンカ'. 흔한 말만 남으면 빈 문자열."""
    changed = True
    while changed:
        changed = False
        for word in _CLINIC_GENERIC:
            if token.endswith(word):
                token = token[: -len(word)]
                changed = True
    return token


def _clinic_tokens(name: str) -> list[tuple[str, str]]:
    """사용자가 말한 병원 이름 → (단어, 흔한 말을 뗀 단어) 목록.

    영문과 다른 글자 사이도 나눈다('SBC湘南美容クリニック' → sbc, 湘南美容クリニック). 두 글자보다 짧은
    단어와 흔한 말뿐인 단어는 버린다('A CLINIC', 'Aクリニック'은 단어가 남지 않는다).
    """
    tokens = []
    for word in _clinic_normalize(name).split():
        for part in re.findall(r"[a-z]+|[^a-z]+", word):
            stripped = _strip_generic(part)
            if len(stripped) >= 2:
                tokens.append((part, stripped))
    return tokens


def _starts_word(text: str, token: str) -> bool:
    """token이 text 안의 단어 시작에 있는지. 영문 단어는 끝도 단어 경계여야 한다('a'는 'bianca'에 맞지 않음)."""
    start = text.find(token)
    while start != -1:
        end = start + len(token)
        before = _script(text[start - 1]) if start > 0 else None
        after = _script(text[end]) if end < len(text) else None
        if before != _script(token[0]) and not (
            _script(token[-1]) == "latin" and after == "latin"
        ):
            return True
        start = text.find(token, start + 1)
    return False


def find_jp_clinic(menu: Menu, name: str) -> JpClinicMatch:
    """사용자가 말한 일본 병원을 이 시술의 일본 벤치마크 병원에서 찾는다(설계 4.7).

    - 사용자는 '湘南', 'SBC', '品川'처럼 이름의 앞부분만 말하는 일이 많다. 단어가 병원 이름(일본어·한국어)의
      단어 시작에 있으면 맞는 것으로 본다. 단어 중간('ビアンカ'의 'a')에는 맞지 않는다.
    - 단어 그대로 맞지 않으면 흔한 말을 뗀 단어로 찾는다('湘南美容外科' → 湘南).
    - 모든 단어에 맞는 병원이 없으면 한 단어라도 맞는 병원으로 찾는다. 지점 이름('品川美容外科 新宿院')은
      데이터에 없기 때문이다.
    - 두 곳 이상 맞으면 ambiguous다.
    """
    tokens = _clinic_tokens(name)

    def hits(token: tuple[str, str]) -> set[int]:
        full, stripped = token
        for word in dict.fromkeys((full, stripped)):
            found = {
                i
                for i, b in enumerate(menu.jp_benchmarks)
                if _starts_word(_clinic_text(b), word)
            }
            if found:
                return found
        return set()

    per_token = [hits(t) for t in tokens]
    indexes = set.intersection(*per_token) if per_token else set()
    if not indexes:
        indexes = set().union(*per_token)
    found = tuple(b for i, b in enumerate(menu.jp_benchmarks) if i in indexes)
    if not found:
        return JpClinicMatch(status="unknown", benchmarks=())
    if len(found) > 1:
        return JpClinicMatch(status="ambiguous", benchmarks=found)
    return JpClinicMatch(status="found", benchmarks=found)


def menus_with_jp_clinic(name: str) -> list[Menu]:
    """이 이름의 일본 병원 가격이 있는 시술들. 이번 시술에 그 병원 가격이 없을 때 안내용."""
    return [
        m for m in get_catalog().menus if find_jp_clinic(m, name).status != "unknown"
    ]
