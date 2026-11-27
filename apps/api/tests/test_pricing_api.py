"""가격 조회 API(`/api/fx`, `/api/menus`, `/api/clinics`)."""

import importlib

import pytest
from fastapi.testclient import TestClient

from api.domain.calc import rank_rows
from api.domain.catalog import Fx, GeoPoint, get_catalog
from api.domain.fx import FxProvider
from api.main import app

search_clinics_module = importlib.import_module("api.agent.tools.search_clinics")

client = TestClient(app)


def test_fx_is_the_rate_the_agent_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    fx = Fx.model_validate({**get_catalog().fx.model_dump(), "krw_per_100_jpy": 900.0})
    provider = FxProvider(None, fx)
    monkeypatch.setattr(search_clinics_module, "get_fx_provider", lambda: provider)

    response = client.get("/api/fx")

    assert response.status_code == 200
    assert response.json()["krwPer100Jpy"] == 900.0


def test_menus_list_every_menu_in_catalog_order() -> None:
    catalog = get_catalog()

    body = client.get("/api/menus").json()

    assert [m["id"] for m in body["items"]] == [m.id for m in catalog.menus]
    first = body["items"][0]
    menu = catalog.menus[0]
    assert first["jpBenchmarkJpy"] == menu.jp_benchmark_jpy
    assert len(first["jpBenchmarks"]) == len(menu.jp_benchmarks)
    assert first["clinicCount"] == sum(menu.id in p.prices for p in catalog.places)


@pytest.mark.parametrize("sort", ["savings", "distance", "rating"])
def test_clinic_prices_match_rank_rows(sort: str) -> None:
    catalog = get_catalog()
    origin = GeoPoint(lat=37.5563, lng=126.9236)
    rows = rank_rows(catalog, "rejuran-2cc", sort=sort, origin=origin)

    body = client.get(
        "/api/clinics",
        params={"menuId": "rejuran-2cc", "sort": sort, "lat": 37.5563, "lng": 126.9236},
    ).json()

    assert body["menuId"] == "rejuran-2cc"
    assert body["fx"]["krwPer100Jpy"] == catalog.fx.krw_per_100_jpy
    assert [i["clinic"]["id"] for i in body["items"]] == [r.place.id for r in rows]
    for item, row in zip(body["items"], rows, strict=True):
        assert item["price"]["totalKrw"] == row.total_krw
        assert item["price"]["totalJpy"] == row.total_jpy
        assert item["price"]["savingsJpy"] == row.savings_jpy
        assert item["distanceMeters"] == row.distance_meters


def test_clinic_prices_filters() -> None:
    catalog = get_catalog()

    body = client.get(
        "/api/clinics",
        params={"menuId": "shrink-300", "area": "hongdae", "japaneseOnly": True},
    ).json()

    assert body["items"]
    for item in body["items"]:
        place = catalog.place(item["clinic"]["id"])
        assert place.area == "hongdae"
        assert place.japanese_support


def test_clinic_prices_search_query() -> None:
    body = client.get(
        "/api/clinics", params={"menuId": "rejuran-2cc", "q": "밴스 홍대"}
    ).json()

    assert body["items"]
    assert all("밴스" in i["clinic"]["nameKo"] for i in body["items"])


def test_clinic_prices_use_current_fx(monkeypatch: pytest.MonkeyPatch) -> None:
    fx = Fx.model_validate({**get_catalog().fx.model_dump(), "krw_per_100_jpy": 1000.0})
    provider = FxProvider(None, fx)
    monkeypatch.setattr(search_clinics_module, "get_fx_provider", lambda: provider)
    rows = rank_rows(get_catalog(), "potenza", krw_per_100_jpy=1000.0)

    body = client.get("/api/clinics", params={"menuId": "potenza"}).json()

    assert body["fx"]["krwPer100Jpy"] == 1000.0
    assert [i["price"]["totalJpy"] for i in body["items"]] == [
        r.total_jpy for r in rows
    ]


def test_unknown_menu_is_404() -> None:
    assert client.get("/api/clinics", params={"menuId": "filler"}).status_code == 404


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"menuId": "potenza", "sort": "price"},
        {"menuId": "potenza", "area": "busan"},
        {"menuId": "potenza", "lat": 37.5},
        {"menuId": "potenza", "lat": 100, "lng": 127},
    ],
)
def test_invalid_query_is_422(params: dict) -> None:
    assert client.get("/api/clinics", params=params).status_code == 422


def test_clinic_detail_lists_prices_in_menu_order() -> None:
    catalog = get_catalog()
    place = catalog.places[0]

    body = client.get(f"/api/clinics/{place.id}").json()

    assert body["clinic"]["id"] == place.id
    assert [p["menuId"] for p in body["prices"]] == [
        m.id for m in catalog.menus if m.id in place.prices
    ]
    assert len(body["channels"]) == len(place.channels)


def test_unknown_clinic_is_404() -> None:
    assert client.get("/api/clinics/nope").status_code == 404
