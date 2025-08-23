# -*- coding: utf-8 -*-
# mock_api.py
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, constr

app = FastAPI(
    title="AI Demo Agents — Mock API",
    version="1.0.0",
    description="Статические, предсказуемые моки для e-com, недвижимости и клиники.",
)

# --- Middleware (CORS для удобства) ---
app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# Common / Health
# =========================
class Health(BaseModel):
    ok: bool = True


@app.get("/healthz", response_model=Health, tags=["health"], summary="Healthcheck")
def healthz() -> Health:
    return Health()

# =========================
# 1) E-COMMERCE
# =========================
class OrderItem(BaseModel):
    sku: constr(strip_whitespace=True, min_length=1)
    name: str


class OrderStatusResponse(BaseModel):
    order_id: constr(strip_whitespace=True, min_length=1)
    status: str = Field(examples=["В пути", "Доставлен", "Оформлен"])
    eta: str = Field(description="YYYY-MM-DD", examples=["2025-08-23"])
    items: List[OrderItem]


class ReturnCondition(str, Enum):
    new = "new"
    used = "used"
    damaged = "damaged"


class CreateReturnRequest(BaseModel):
    order_id: constr(strip_whitespace=True, min_length=1)
    item_sku: constr(strip_whitespace=True, min_length=1)
    reason: constr(strip_whitespace=True, min_length=1)
    condition: ReturnCondition


class CreateReturnResponse(BaseModel):
    rma: str
    label_url: str


class RelatedItem(BaseModel):
    sku: str
    name: str


_CATALOG = {
    "HOO-XL": "Худи XL",
    "CAP-01": "Кепка",
    "SCK-02": "Носки",
    "GLV-03": "Перчатки",
}


@app.get(
    "/mock/ecom/order",
    response_model=OrderStatusResponse,
    tags=["ecom"],
    summary="Статус заказа",
)
def get_order(order_id: str = Query(..., min_length=1)) -> OrderStatusResponse:
    return OrderStatusResponse(
        order_id=order_id,
        status="В пути",
        eta="2025-08-23",
        items=[OrderItem(sku="HOO-XL", name=_CATALOG["HOO-XL"])],
    )


@app.post(
    "/mock/ecom/return",
    response_model=CreateReturnResponse,
    tags=["ecom"],
    summary="Создать возврат (RMA)",
    status_code=status.HTTP_201_CREATED,
)
def create_return(payload: CreateReturnRequest) -> CreateReturnResponse:
    if payload.item_sku not in _CATALOG:
        raise HTTPException(status_code=404, detail="SKU not found")
    return CreateReturnResponse(
        rma="RMA-7890",
        label_url="https://example/label/RMA-7890.pdf",
    )


@app.get(
    "/mock/ecom/related",
    response_model=List[RelatedItem],
    tags=["ecom"],
    summary="Релевантные товары (апселл)",
)
def related_items(
    _sku: str = Query(..., min_length=1),  # underscore → чтобы IDE не ругалась
    limit: int = Query(3, ge=1, le=10),
) -> List[RelatedItem]:
    pool = [
        RelatedItem(sku="CAP-01", name=_CATALOG["CAP-01"]),
        RelatedItem(sku="SCK-02", name=_CATALOG["SCK-02"]),
        RelatedItem(sku="GLV-03", name=_CATALOG["GLV-03"]),
    ]
    return pool[:limit]

# =========================
# 2) REALTY
# =========================
class Listing(BaseModel):
    id: str
    price: int = Field(ge=0)
    address: str
    rooms: int = Field(ge=1, le=10)
    area: float = Field(ge=1)


class BookViewingRequest(BaseModel):
    listing_id: str
    datetime: str = Field(description="ISO 8601", examples=["2025-08-21T19:00"])
    name: constr(strip_whitespace=True, min_length=1)
    phone: constr(strip_whitespace=True, min_length=3)


class BookViewingResponse(BaseModel):
    status: str
    calendar_invite: str


_LISTINGS: list[Listing] = [
    Listing(id="APT-101", price=14_900_000, address="ul. Akademicheskaya, 12", rooms=2, area=54),
    Listing(id="APT-202", price=14_500_000, address="ul. Novatorov, 7", rooms=2, area=50),
    Listing(id="APT-303", price=13_900_000, address="pr. Vernadskogo, 19", rooms=2, area=48),
]


@app.get(
    "/mock/realty/search",
    response_model=List[Listing],
    tags=["realty"],
    summary="Поиск лотов",
)
def search_listings(
    budget_max: int = Query(..., ge=0),
    _district: Optional[str] = Query(None, description="Заглушка"),
    rooms: Optional[int] = Query(None, ge=1, le=10),
    _mortgage: Optional[bool] = Query(None, description="Заглушка"),
) -> List[Listing]:
    result = [x for x in _LISTINGS if x.price <= budget_max]
    if rooms:
        result = [x for x in result if x.rooms == rooms]
    return result


@app.post(
    "/mock/realty/book",
    response_model=BookViewingResponse,
    tags=["realty"],
    summary="Бронь показа",
    status_code=status.HTTP_201_CREATED,
)
def book_viewing(payload: BookViewingRequest) -> BookViewingResponse:
    if payload.listing_id not in {x.id for x in _LISTINGS}:
        raise HTTPException(status_code=404, detail="Listing not found")
    return BookViewingResponse(
        status="booked",
        calendar_invite="https://example/invite/abc.ics",
    )

# =========================
# 3) CLINIC
# =========================
class Speciality(str, Enum):
    lor = "лор"
    therapist = "терапевт"
    pediatrician = "педиатр"
    cardiologist = "кардиолог"


class SlotsResponse(BaseModel):
    slots: List[str] = Field(
        examples=[["2025-08-20T15:30", "2025-08-20T18:00", "2025-08-21T10:00"]]
    )


class BookAppointmentRequest(BaseModel):
    speciality: Speciality
    datetime: str = Field(description="ISO 8601", examples=["2025-08-20T18:00"])
    name: constr(strip_whitespace=True, min_length=1)
    phone: constr(strip_whitespace=True, min_length=3)


class BookAppointmentResponse(BaseModel):
    status: str
    ticket: str


@app.get(
    "/mock/clinic/slots",
    response_model=SlotsResponse,
    tags=["clinic"],
    summary="Свободные слоты на приём",
)
def clinic_slots(
    _speciality: Speciality = Query(...),
    _date_from: Optional[str] = Query(None, description="Заглушка"),
) -> SlotsResponse:
    return SlotsResponse(slots=["2025-08-20T15:30", "2025-08-20T18:00", "2025-08-21T10:00"])


@app.post(
    "/mock/clinic/book",
    response_model=BookAppointmentResponse,
    tags=["clinic"],
    summary="Запись к врачу",
    status_code=status.HTTP_201_CREATED,
)
def clinic_book(_payload: BookAppointmentRequest) -> BookAppointmentResponse:
    return BookAppointmentResponse(status="booked", ticket="CLN-5521")

# =========================
# Root helper
# =========================
@app.get("/", tags=["health"], include_in_schema=False)
def root() -> Health:
    return Health()