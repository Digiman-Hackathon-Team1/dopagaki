"""가격 조회 API. 요청을 받아 `PricingService`를 부르고 결과를 돌려준다."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.domain.calc import SortId
from api.domain.catalog import AreaId, GeoPoint
from api.pricing.schemas import ClinicDetail, ClinicPriceList, FxRate, MenuList
from api.pricing.service import (
    PricingService,
    UnknownClinicError,
    UnknownMenuError,
    get_pricing_service,
)

router = APIRouter(prefix="/api", tags=["pricing"])

Service = Annotated[PricingService, Depends(get_pricing_service)]


@router.get("/fx", operation_id="getFx")
def get_fx(service: Service) -> FxRate:
    return service.fx()


@router.get("/menus", operation_id="listMenus")
def list_menus(service: Service) -> MenuList:
    return service.menus()


@router.get(
    "/clinics",
    operation_id="listClinicPrices",
    responses={404: {"description": "없는 시술"}},
)
def list_clinic_prices(
    service: Service,
    menu_id: Annotated[str, Query(alias="menuId", description="시술 ID")],
    sort: Annotated[SortId, Query(description="정렬 기준")] = "savings",
    area: Annotated[AreaId | None, Query(description="이 지역 병원만")] = None,
    japanese_only: Annotated[
        bool, Query(alias="japaneseOnly", description="일본어 상담 가능 병원만")
    ] = False,
    q: Annotated[
        str | None,
        Query(max_length=50, description="병원 이름·브랜드·주소·역 검색어"),
    ] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> ClinicPriceList:
    if (lat is None) != (lng is None):
        raise HTTPException(422, "lat과 lng은 함께 보내야 한다")
    origin = GeoPoint(lat=lat, lng=lng) if lat is not None and lng is not None else None
    try:
        return service.clinic_prices(
            menu_id,
            sort=sort,
            area=area,
            japanese_only=japanese_only,
            query=q,
            origin=origin,
        )
    except UnknownMenuError:
        raise HTTPException(404, "없는 시술") from None


@router.get(
    "/clinics/{clinic_id}",
    operation_id="getClinic",
    responses={404: {"description": "없는 병원"}},
)
def get_clinic(clinic_id: str, service: Service) -> ClinicDetail:
    try:
        return service.clinic(clinic_id)
    except UnknownClinicError:
        raise HTTPException(404, "없는 병원") from None
