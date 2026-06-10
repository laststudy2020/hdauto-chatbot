from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="사용자 메시지")
    user_id: str = Field(default="anonymous", description="사용자 ID (톡톡/웹)")
    channel: str = Field(default="web", description="채널 (talktalk/web/kakao)")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="챗봇 응답 메시지")
    intent: str = Field(..., description="감지된 의도")
    model_name: Optional[str] = Field(None, description="감지된 모델명")
    confidence: float = Field(..., description="의도 분류 신뢰도")
    source: str = Field(default="db", description="데이터 출처 (db/manual/fixed)")


class ProductCreate(BaseModel):
    model_name: str
    series: Optional[str] = None
    manufacturer: str
    category: str
    status: str = "active"
    discontinued_date: Optional[str] = None
    description: Optional[str] = None
    our_price: Optional[int] = None


class SpecCreate(BaseModel):
    product_id: int
    dimension_w: Optional[float] = None
    dimension_h: Optional[float] = None
    dimension_d: Optional[float] = None
    weight_kg: Optional[float] = None
    input_voltage: Optional[str] = None
    output_type: Optional[str] = None
    io_points: Optional[str] = None
    comm_protocol: Optional[str] = None
    operating_temp: Optional[str] = None
    protection_class: Optional[str] = None
    mounting_type: Optional[str] = None
    rated_power: Optional[str] = None


class ReplacementCreate(BaseModel):
    old_model_name: str
    new_model_name: str
    compatibility_notes: Optional[str] = None
    program_convertible: bool = False
    terminal_compatible: bool = False
    dimension_compatible: bool = False
    source_url: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    app_name: str
    products_count: int
    timestamp: datetime
