"""match_jp_treatment: 일본 시술명을 한국 시술로 바꾼다(설계 문서 4.3, 9장, FR-09, ADR 0012).

대응표(`domain/jp_names.py`)에 있는 이름만 대응한다. 결과는 찾음 / 확인 필요(ambiguous) / 대응 없음이고,
LLM은 대응을 지어내지 않는다. 찾은 대응은 jpMatch 카드로 화면에 나간다(finalize가 artifact로 조립).
사용자가 다니던 일본 병원을 말하면 그 병원의 이 시술 가격도 함께 돌려준다. 그 가격과의 절약액은
search_clinics(jp_clinic)가 병원마다 계산한다.
"""

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from api.agent.schemas import JpClinicPrice, JpMatchCardData
from api.domain.catalog import JpBenchmark, Menu, get_catalog
from api.domain.jp_names import (
    UNIT_NOTES,
    compare_note,
    find_jp_clinic,
    get_jp_names,
    menus_with_jp_clinic,
)

TOOL_NAME = "match_jp_treatment"


class MatchJpTreatmentInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=50,
        description=(
            "사용자가 말한 일본 시술명 그대로. 예: 'ハイフ', 'HIFU', 'ブナジュ', 'エラボトックス', '하이후'. "
            "번역하거나 한국 이름으로 바꾸지 않는다. 시술이 여러 개면 하나씩 따로 부른다"
        ),
    )
    jp_clinic: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "사용자가 다니던(가격을 아는) 일본 병원 이름. 지점 이름은 빼고 병원 이름만 넣는다. "
            "예: '品川', 'SBC', '湘南美容クリニック'. 말하지 않았으면 null"
        ),
    )


class MatchJpTreatmentArtifact(BaseModel):
    """화면용 결과. found일 때만 jpMatch 카드가 된다."""

    status: Literal["found", "ambiguous", "unknown"]
    match: JpMatchCardData | None = None
    candidates: list[str] = Field(default_factory=list)


def jp_clinic_price(benchmark: JpBenchmark) -> JpClinicPrice:
    note = compare_note(benchmark)
    return JpClinicPrice(
        name_ja=benchmark.name_ja,
        name_ko=benchmark.name_ko,
        plan_ja=benchmark.plan_ja,
        plan_ko=benchmark.plan_ko,
        price_jpy=benchmark.price_jpy,
        source_url=benchmark.url,
        product_note_ja=note.ja if note else None,
        product_note_ko=note.ko if note else None,
    )


def product_note_line(benchmark: JpBenchmark) -> str:
    """직접 비교할 일본 병원 가격이 다른 제품이면 LLM이 답변에 함께 말하도록 알린다."""
    note = compare_note(benchmark)
    if note is None:
        return ""
    return f" Product difference, tell the user: {note.ko} / {note.ja}"


def _menu_label(menu: Menu) -> str:
    return f"menu_id={menu.id} ({menu.name_ko} / {menu.name_ja})"


def _clinic_label(benchmark: JpBenchmark) -> str:
    return (
        f"{benchmark.name_ja} / {benchmark.name_ko}: {benchmark.plan_ja} "
        f"¥{benchmark.price_jpy:,} (tax incl.)"
    )


def _jp_clinic_lines(menu: Menu, name: str) -> tuple[list[str], JpClinicPrice | None]:
    """사용자가 말한 일본 병원을 찾아 LLM용 문장과 카드용 가격을 만든다."""
    result = find_jp_clinic(menu, name)
    if result.status == "found":
        benchmark = result.benchmarks[0]
        return [
            f"User's Japanese clinic: {_clinic_label(benchmark)}."
            + product_note_line(benchmark),
            (
                f"Pass jp_clinic={benchmark.name_ja!r} to search_clinics. Each clinic card will "
                "then show savings against this clinic, calculated by the app. Do not calculate "
                "savings yourself."
            ),
            (
                f"The Japanese plan unit may differ from the Korean menu ({menu.name_ko}). "
                "If it does, say so."
            ),
        ], jp_clinic_price(benchmark)
    if result.status == "ambiguous":
        listed = "; ".join(_clinic_label(b) for b in result.benchmarks)
        return [
            (
                f"jp_clinic {name!r} matches several Japanese clinics with a price for this "
                f"treatment: {listed}. Ask which one the user went to. Until then, compare with "
                "the Japan average."
            )
        ], None
    return [jp_clinic_not_found(menu, name)], None


def jp_clinic_not_found(menu: Menu, name: str) -> str:
    """사용자가 말한 일본 병원이 이 시술의 일본 벤치마크 병원에 없을 때 LLM에 줄 문장."""
    listed = ", ".join(b.name_ja for b in menu.jp_benchmarks)
    line = (
        f"No Japanese clinic matching {name!r} is among the clinics with a {menu.name_ko} price "
        f"in the data ({listed}), so a direct comparison is not possible. Compare with the Japan "
        "average and tell the user. If the user may mean one of these clinics, ask."
    )
    others = [m for m in menus_with_jp_clinic(name) if m.id != menu.id]
    if others:
        line += (
            f" A clinic matching {name!r} has prices for: "
            + ", ".join(m.name_ko for m in others)
            + "."
        )
    return line


def _pending_clinic(jp_clinic: str | None) -> str:
    """시술이 정해지지 않아 일본 병원 가격을 아직 찾지 않았을 때, 다음 호출에 다시 넘기도록 알린다."""
    if not jp_clinic or not jp_clinic.strip():
        return ""
    return (
        f"\nThe user's Japanese clinic {jp_clinic!r} was not looked up because the treatment is "
        "not settled. Pass it again as jp_clinic once the treatment is confirmed."
    )


DESCRIPTION = """\
일본에서 쓰는 시술 이름(ハイフ·하이후, HIFU, ブナジュ, エラボトックス 등)을 서울 가격 데이터의 한국 시술로 바꾼다.
필수 입력: query(사용자가 말한 일본 시술명 그대로). 사용자가 다니던 일본 병원을 말했으면 jp_clinic에 넣는다(기본 null).
시술명이 search_clinics 시술 목록의 이름과 다르면 search_clinics보다 먼저 부른다. 대응을 직접 짐작하지 않는다.
결과가 found면 한국 이름과 이름이 다른 이유를 먼저 말하고, 결과의 menu_id로 search_clinics를 부른다.
찾은 대응은 화면에 카드로 나간다. ambiguous면 search_clinics를 부르지 말고 후보를 빠른 답변 버튼으로
"혹시 ○○를 말씀하시는 거예요?"라고 한 번 묻는다. 다른 시술 병원을 함께 보여 주는 턴이면 답변 끝 문장에
후보 이름을 넣어 묻는다. 한 문장에 시술이 여러 개면 시술마다 따로 부른다. unknown이면 대응 시술이 없다고 말하고, 고민 기준 추천으로
넘어갈지 묻는다.
쓰면 안 되는 경우: 시술명이 아닌 고민만 말했을 때(recommend_treatments를 쓴다), 이미 이번 대화에서 확인한 시술."""


@tool(
    TOOL_NAME,
    description=DESCRIPTION,
    args_schema=MatchJpTreatmentInput,
    response_format="content_and_artifact",
)
def match_jp_treatment(query: str, jp_clinic: str | None = None) -> tuple[str, dict]:
    catalog = get_catalog()
    result = get_jp_names().match(query)
    head = f"match_jp_treatment: {query!r}"

    if result.status == "unknown":
        message = (
            f"{head} → unknown. No Korean treatment in the data matches this name. Do not guess a "
            "match or name a similar treatment as if it were the same. Tell the user there is no "
            "matching treatment in Dopagaki's price data, and ask whether to recommend treatments by "
            "their concern instead (recommend_treatments)."
        )
        return message + _pending_clinic(jp_clinic), MatchJpTreatmentArtifact(
            status="unknown"
        ).model_dump()

    if result.status == "ambiguous":
        candidates = [catalog.menu(i) for i in result.menu_ids]
        listed = "Candidates: " + "; ".join(_menu_label(m) for m in candidates) + "."
        direct = {i for e in result.entries if e.kind == "direct" for i in e.menu_ids}
        if len(direct) > 1:
            lines = [
                f"{head} → ambiguous, it names several different treatments. {listed}",
                (
                    "Call match_jp_treatment once per treatment name, or ask which one the "
                    "user wants."
                ),
            ]
        else:
            lines = [
                (
                    f"{head} → ambiguous. Confirm with the user before searching. Do not call "
                    "search_clinics for these yet."
                ),
                listed,
                *(
                    f"Note for {', '.join(e.names[:2])}: {e.reason_ko} / {e.reason_ja}"
                    for e in result.entries
                ),
                (
                    "Ask once, e.g. 'Do you mean <Korean treatment>?', with each candidate as a "
                    "quick answer option. If this response also shows clinics for another "
                    "treatment, ask it in the last sentence of answer instead, naming each candidate."
                ),
            ]
        return "\n".join(lines) + _pending_clinic(jp_clinic), MatchJpTreatmentArtifact(
            status="ambiguous", candidates=list(result.menu_ids)
        ).model_dump()

    menu = catalog.menu(result.menu_ids[0])
    # 이유는 같은 시술의 이름(direct)에서, 없으면 부위 이름(part, 'ボツラックス エラ')에서 가져온다.
    entry = min(result.entries, key=lambda e: ("direct", "part", "broad").index(e.kind))
    unit = UNIT_NOTES[menu.id]
    lines = [
        f"{head} → found {_menu_label(menu)}.",
        f"Why the names differ: {entry.reason_ko} / {entry.reason_ja}",
        f"Units: {unit.ko} / {unit.ja}",
        f"Next: call search_clinics with menu_id={menu.id!r}.",
    ]
    clinic = None
    if jp_clinic and jp_clinic.strip():
        clinic_lines, clinic = _jp_clinic_lines(menu, jp_clinic)
        lines += clinic_lines
    card = JpMatchCardData(
        query=query,
        menu_id=menu.id,
        menu_name_ja=menu.name_ja,
        menu_name_ko=menu.name_ko,
        reason_ja=entry.reason_ja,
        reason_ko=entry.reason_ko,
        unit_note_ja=unit.ja,
        unit_note_ko=unit.ko,
        jp_clinic=clinic,
    )
    return "\n".join(lines), MatchJpTreatmentArtifact(
        status="found", match=card, candidates=[menu.id]
    ).model_dump()
