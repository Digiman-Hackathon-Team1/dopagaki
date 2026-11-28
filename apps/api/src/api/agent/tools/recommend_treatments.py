"""recommend_treatments: 고민으로 맞는 시술 후보와 제외 이유를 돌려준다(설계 문서 4.5, 9장).

사용자 표현을 고민 분류로 바꾸는 일은 LLM이 하고, 고민을 시술로 잇는 일은 고민별 시술표
(web treatment-rules.ts의 CONCERN_MENUS)가 한다. 회복 기간·바늘 거르기는 search_clinics와 같은 값
(`downtime_max_days`, `uses_needle`)과 같은 비교를 써서 두 도구의 제외 판단이 어긋나지 않게 한다.
출력은 텍스트다(설계 문서 9장). 카드는 다음에 부르는 search_clinics가 만든다.
"""

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from api.domain.catalog import ConcernId, Menu, get_catalog

TOOL_NAME = "recommend_treatments"

UNSUPPORTED = "unsupported"

# LLM이 사용자 표현을 분류할 때 보는 설명과 일본어·한국어 예시 표현.
CONCERN_GUIDE: dict[ConcernId, str] = {
    "pores": "pores, blackheads, acne scars (毛穴, 黒ずみ, ニキビ跡 / 모공, 블랙헤드, 여드름 흉터)",
    "glow": "dullness, lack of glow (ツヤ, 透明感, くすみ / 윤기, 광채, 칙칙함)",
    "lift": "sagging, jawline, double chin, small face (たるみ, 小顔, フェイスライン / 처짐, 탄력, 턱선, 이중턱)",
    "dry": "dryness, fine lines, wrinkles (乾燥, 小じわ / 속건조, 잔주름, 주름)",
    "jaw": "square jaw, teeth clenching (エラ, 食いしばり / 사각턱, 이갈이)",
    "skinbase": "skin density and texture, skin boosters (肌育, ハリ, 肌質改善 / 피부 밀도, 피부결, 스킨부스터)",
}


class Candidate(BaseModel):
    menu: Menu
    concerns: list[ConcernId]


class Excluded(BaseModel):
    menu: Menu
    reason: Literal["needle", "downtime"]


class TreatmentPlan(BaseModel):
    candidates: list[Candidate]
    excluded: list[Excluded]


def plan_treatments(
    concerns: list[ConcernId],
    max_downtime_days: int | None = None,
    avoid_needle: bool = False,
) -> TreatmentPlan:
    """고민별 시술표로 후보를 모으고 회복 기간·바늘 조건으로 거른다.

    고민이 여럿이면 web `candidateMenuIds`처럼 고민마다 가장 직접적인 시술부터 번갈아 넣는다.
    한 시술이 바늘과 회복 기간에 모두 걸리면 search_clinics와 같은 순서로 바늘을 먼저 센다.
    """
    catalog = get_catalog()
    lists = [catalog.concern(c).menu_ids for c in concerns]
    order: list[str] = []
    for i in range(max((len(ids) for ids in lists), default=0)):
        for ids in lists:
            if i < len(ids) and ids[i] not in order:
                order.append(ids[i])

    candidates: list[Candidate] = []
    excluded: list[Excluded] = []
    for menu_id in order:
        menu = catalog.menu(menu_id)
        if avoid_needle and menu.uses_needle:
            excluded.append(Excluded(menu=menu, reason="needle"))
        elif (
            max_downtime_days is not None and menu.downtime_max_days > max_downtime_days
        ):
            excluded.append(Excluded(menu=menu, reason="downtime"))
        else:
            matched = [
                c for c, ids in zip(concerns, lists, strict=True) if menu_id in ids
            ]
            candidates.append(Candidate(menu=menu, concerns=matched))
    return TreatmentPlan(candidates=candidates, excluded=excluded)


class RecommendTreatmentsInput(BaseModel):
    concerns: list[ConcernId | Literal["unsupported"]] = Field(
        min_length=1,
        max_length=4,
        description=(
            "사용자의 고민을 도구 설명의 분류로 바꾼 값. 여러 개면 사용자가 더 신경 쓰는 순. "
            "어느 분류에도 맞지 않는 고민은 'unsupported'로 넣는다"
        ),
    )
    max_downtime_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "겉으로 보이는 회복 기간을 며칠까지 허용하는지. 여행자는 시술 날부터 귀국까지 남은 날 수 "
            "(예: 2박 3일 여행 첫날 시술이면 2, 내일 귀국이면 0). 시기를 모르면 null"
        ),
    )
    avoid_needle: bool = Field(
        default=False, description="사용자가 바늘·주사를 피하고 싶다고 했을 때만 true"
    )


def _menu_label(menu: Menu) -> str:
    return f"{menu.id} ({menu.name_ko} / {menu.name_ja})"


DESCRIPTION = f"""\
사용자의 피부 고민을 고민별 시술표로 시술 후보와 제외 이유로 바꾼다. 시술명을 말하지 않고 고민만 말했을 때 쓴다.
필수 입력: concerns. 기본값: 회복 기간 제한 없음, 바늘 상관없음.
고민 분류:
{chr(10).join(f"- {cid}: {text}" for cid, text in CONCERN_GUIDE.items())}
어느 분류에도 맞지 않는 고민(예: 탈모, 점·문신 제거, 체형)은 지어내 분류하지 말고 'unsupported'로 넣는다.
결과를 받은 뒤: 후보를 고른 이유와 제외된 시술의 이유를 먼저 짧게 설명하고, 첫 번째 후보(사용자가 고르면 그것)로
search_clinics를 같은 max_downtime_days·avoid_needle로 부른다.
쓰면 안 되는 경우: 사용자가 시술명을 이미 말했을 때(바로 search_clinics), 고민을 아직 모를 때, 회복 기간을
며칠까지 둘 수 있는지(여행자는 귀국까지 남은 날)를 모를 때. 모르면 부르지 말고 질문 묶음으로 먼저 묻는다.
사용자가 회복 기간은 상관없다고 했으면 max_downtime_days를 null로 두고 부른다."""


@tool(TOOL_NAME, description=DESCRIPTION, args_schema=RecommendTreatmentsInput)
def recommend_treatments(
    concerns: list[ConcernId | Literal["unsupported"]],
    max_downtime_days: int | None = None,
    avoid_needle: bool = False,
) -> str:
    supported: list[ConcernId] = []
    for c in concerns:
        if c != UNSUPPORTED and c not in supported:
            supported.append(c)
    allowed = "no limit" if max_downtime_days is None else f"{max_downtime_days} days"
    needle = "yes" if avoid_needle else "no"
    head = f"recommend_treatments: concerns={', '.join(concerns)}, "
    lines = [head + f"allowed downtime={allowed}, avoid needle={needle}."]
    if UNSUPPORTED in concerns:
        lines.append(
            "Some of the user's concerns are not supported: Dopagaki has no treatment data for them. "
            "Tell the user plainly that the concern is not supported, without suggesting a treatment for it. "
            "Supported concerns: " + ", ".join(CONCERN_GUIDE) + "."
        )
    if not supported:
        return "\n".join(lines)

    plan = plan_treatments(supported, max_downtime_days, avoid_needle)
    if plan.candidates:
        lines.append("Candidates, best first (use menu_id with search_clinics):")
        lines += [
            f"{i}. {_menu_label(c.menu)} | for: {', '.join(c.concerns)} | "
            f"visible downtime up to {c.menu.downtime_max_days} days | "
            f"needle: {'yes' if c.menu.uses_needle else 'no'}"
            for i, c in enumerate(plan.candidates, start=1)
        ]
    else:
        lines.append(
            "No treatment fits these conditions. Tell the user which condition excluded each one "
            "and ask whether they can relax it. Do not call search_clinics for an excluded treatment."
        )
    if plan.excluded:
        lines.append("Excluded:")
        for e in plan.excluded:
            if e.reason == "needle":
                why = "uses a needle and the user wants to avoid needles"
            else:
                why = (
                    f"visible downtime can last up to {e.menu.downtime_max_days} days, "
                    f"more than the allowed {max_downtime_days} days"
                )
            lines.append(f"- {_menu_label(e.menu)}: {why}")
    return "\n".join(lines)
