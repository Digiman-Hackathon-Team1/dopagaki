"""가격 조회 서비스. 시술·병원·환율 조회와 가격 비교를 라우터 대신 맡는다.

계산은 `domain/calc.py`(web calc.ts와 같은 식)를 그대로 쓰고, 여기서는 조회 조건을 맞추고 응답
형식으로 옮기기만 한다. 라우터는 요청을 받아 이 서비스를 부르고 결과를 돌려준다.
"""

from collections import Counter
from typing import Annotated

from fastapi import Depends

from api.domain.calc import (
    DEFAULT_SEOUL_CENTER,
    PriceRow,
    SortId,
    compute_row,
    rank_rows,
)
from api.domain.catalog import AreaId, Catalog, Fx, GeoPoint, Place, get_catalog
from api.domain.fx import FxProvider
from api.domain.price_tags import price_tags
from api.pricing import schemas as s


class UnknownMenuError(LookupError):
    pass


class UnknownClinicError(LookupError):
    pass


def _fx(fx: Fx) -> s.FxRate:
    return s.FxRate.model_validate(fx.model_dump())


def _clinic(place: Place) -> s.ClinicSummary:
    return s.ClinicSummary.model_validate(
        place.model_dump(include=set(s.ClinicSummary.model_fields))
    )


def _price(row: PriceRow) -> s.PriceComparison:
    return s.PriceComparison(
        menu_id=row.menu.id,
        base_krw=row.base_krw,
        vat_krw=row.vat_krw,
        total_krw=row.total_krw,
        total_jpy=row.total_jpy,
        jp_benchmark_jpy=row.jp_benchmark_jpy,
        savings_jpy=row.savings_jpy,
        savings_pct=row.savings_pct,
        note_ja=row.offer.note_ja,
        note_ko=row.offer.note_ko,
        price_tags=price_tags(row.place, row.menu.id, row.offer),
    )


class PricingService:
    def __init__(self, catalog: Catalog, fx_provider: FxProvider) -> None:
        self._catalog = catalog
        self._fx_provider = fx_provider

    def fx(self) -> s.FxRate:
        return _fx(self._fx_provider.current())

    def menus(self) -> s.MenuList:
        counts = Counter(
            menu_id for place in self._catalog.places for menu_id in place.prices
        )
        return s.MenuList(
            items=[
                s.MenuSummary.model_validate(
                    {
                        **menu.model_dump(
                            include=set(s.MenuSummary.model_fields) - {"jp_benchmarks"}
                        ),
                        "jp_benchmarks": [b.model_dump() for b in menu.jp_benchmarks],
                        "clinic_count": counts[menu.id],
                    }
                )
                for menu in self._catalog.menus
            ]
        )

    def clinic_prices(
        self,
        menu_id: str,
        sort: SortId = "savings",
        area: AreaId | None = None,
        japanese_only: bool = False,
        query: str | None = None,
        origin: GeoPoint | None = None,
    ) -> s.ClinicPriceList:
        """시술 하나의 병원 순위표. web 순위표(calc.ts buildRankedRows)와 같은 거르기·정렬이다."""
        self._menu(menu_id)
        fx = self._fx_provider.current()
        rows = rank_rows(
            self._catalog,
            menu_id,
            sort=sort,
            origin=origin,
            japanese_only=japanese_only,
            area=area,
            krw_per_100_jpy=fx.krw_per_100_jpy,
            search_query=query,
        )
        return s.ClinicPriceList(
            menu_id=menu_id,
            sort=sort,
            fx=_fx(fx),
            items=[
                s.ClinicPrice(
                    clinic=_clinic(row.place),
                    price=_price(row),
                    distance_meters=row.distance_meters,
                    walk_minutes=row.walk_minutes,
                )
                for row in rows
            ],
        )

    def clinic(self, clinic_id: str) -> s.ClinicDetail:
        try:
            place = self._catalog.place(clinic_id)
        except KeyError:
            raise UnknownClinicError(clinic_id) from None
        fx = self._fx_provider.current()
        rows = (
            compute_row(
                place,
                menu,
                fx.krw_per_100_jpy,
                self._catalog.meta.vat_rate,
                DEFAULT_SEOUL_CENTER,
            )
            for menu in self._catalog.menus
        )
        return s.ClinicDetail(
            clinic=_clinic(place),
            address_ko=place.address_ko,
            source_url=place.source_url,
            source_type=place.source_type,
            vat_excluded=place.vat_excluded,
            caveat_ja=place.caveat_ja,
            caveat_ko=place.caveat_ko,
            channels=[
                s.ClinicChannel.model_validate(c.model_dump()) for c in place.channels
            ],
            central_channel=place.central_channel,
            fx=_fx(fx),
            prices=[_price(row) for row in rows if row is not None],
        )

    def _menu(self, menu_id: str) -> None:
        try:
            self._catalog.menu(menu_id)
        except KeyError:
            raise UnknownMenuError(menu_id) from None


def get_fx_provider() -> FxProvider:
    # 에이전트 도구와 같은 조회기를 써서 화면과 상담 답변의 환율·금액이 같게 한다.
    from api.agent.tools.search_clinics import get_fx_provider as agent_fx_provider

    return agent_fx_provider()


def get_pricing_service(
    fx_provider: Annotated[FxProvider, Depends(get_fx_provider)],
) -> PricingService:
    return PricingService(get_catalog(), fx_provider)
