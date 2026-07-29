from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    ForeignKey, JSON, Enum as SQLEnum
)
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy.sql import func
import enum


class Base(DeclarativeBase):
    pass


class ProductStatus(str, enum.Enum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    EOL_SOON = "eol_soon"  # 단종 예정


class AlertChannel(str, enum.Enum):
    KAKAO = "kakao"
    SLACK = "slack"
    EMAIL = "email"


# ─── 1. 제품 마스터 테이블 ───
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(100), unique=True, nullable=False, index=True)
    series = Column(String(50), index=True)
    manufacturer = Column(String(50), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)  # PLC, 인버터, 서보드라이브 등
    status = Column(SQLEnum(ProductStatus), default=ProductStatus.ACTIVE)
    discontinued_date = Column(String(20))
    description = Column(Text)
    smartstore_product_id = Column(String(50))       # 채널상품번호 (스마트스토어 URL 기준)
    origin_product_no = Column(String(50))           # 원상품번호 (네이버 커머스API 조회용)
    inventory_sync_enabled = Column(Boolean, default=True)  # 재고연동 대상 여부
    our_price = Column(Integer)                      # 자사 판매가
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 관계
    specs = relationship("Specification", back_populates="product", uselist=False)
    inventory = relationship("Inventory", back_populates="product", uselist=False)
    old_replacements = relationship(
        "Replacement", foreign_keys="Replacement.old_model_id", back_populates="old_product"
    )
    new_replacements = relationship(
        "Replacement", foreign_keys="Replacement.new_model_id", back_populates="new_product"
    )


# ─── 2. 단종 → 대체품 매핑 테이블 ───
class Replacement(Base):
    __tablename__ = "replacements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    old_model_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    new_model_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    compatibility_notes = Column(Text)  # 호환성 설명
    program_convertible = Column(Boolean, default=False)  # 프로그램 변환 가능 여부
    terminal_compatible = Column(Boolean, default=False)  # 단자대 호환 여부
    dimension_compatible = Column(Boolean, default=False)  # 외형 치수 호환
    source_url = Column(String(500))  # 근거 자료 URL
    created_at = Column(DateTime, server_default=func.now())

    old_product = relationship("Product", foreign_keys=[old_model_id], back_populates="old_replacements")
    new_product = relationship("Product", foreign_keys=[new_model_id], back_populates="new_replacements")


# ─── 3. 제품 규격/스펙 테이블 ───
class Specification(Base):
    __tablename__ = "specifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, unique=True)
    dimension_w = Column(Float)  # 가로 mm
    dimension_h = Column(Float)  # 세로 mm
    dimension_d = Column(Float)  # 깊이 mm
    weight_kg = Column(Float)
    input_voltage = Column(String(50))   # AC100-240V, DC24V 등
    output_type = Column(String(50))     # 트랜지스터, 릴레이
    io_points = Column(String(50))       # 입출력 점수
    comm_protocol = Column(String(200))  # RS-485, Ethernet, CC-Link 등
    operating_temp = Column(String(50))  # 0~55℃
    protection_class = Column(String(20))  # IP20 등
    mounting_type = Column(String(50))   # DIN 레일, 벽면
    rated_power = Column(String(50))     # 정격 출력 (인버터/서보)
    extra_specs = Column(JSON)           # 추가 스펙 (유연한 구조)
    drawing_url = Column(String(500))    # 도면 이미지 링크
    catalog_page = Column(String(50))    # 카탈로그 페이지 번호

    product = relationship("Product", back_populates="specs")


# ─── 4. 재고 테이블 ───
class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, unique=True)
    current_stock = Column(Integer, default=0)
    min_threshold = Column(Integer, default=3)  # 최소 재고 임계값
    warehouse_location = Column(String(50))
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="inventory")


# ─── 5. 재고 알림 기록 ───
class StockAlert(Base):
    __tablename__ = "stock_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    alert_type = Column(String(20))  # low_stock, out_of_stock
    channel = Column(SQLEnum(AlertChannel))
    sent_at = Column(DateTime, server_default=func.now())
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime)


# ─── 6. 경쟁사 가격 이력 ───
class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    our_price = Column(Integer)
    competitor_min = Column(Integer)   # 경쟁사 최저가
    competitor_avg = Column(Integer)   # 경쟁사 평균가
    competitor_max = Column(Integer)   # 경쟁사 최고가
    competitor_count = Column(Integer) # 비교 업체 수
    diff_percent = Column(Float)       # 차이율 %
    needs_adjustment = Column(Boolean, default=False)
    checked_at = Column(DateTime, server_default=func.now())


# ─── 7. 매뉴얼 알람코드 테이블 (RAG 보조) ───
class AlarmCode(Base):
    __tablename__ = "alarm_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    manufacturer = Column(String(50), nullable=False, index=True)
    product_series = Column(String(50), index=True)
    alarm_code = Column(String(20), nullable=False, index=True)
    alarm_name = Column(String(100))
    cause = Column(Text)
    solution = Column(Text)
    manual_page = Column(String(20))       # 매뉴얼 페이지 번호
    manual_filename = Column(String(200))  # 원본 PDF 파일명


# ─── 8. APEX AB/ABR 감속기 카탈로그 (참고 데이터 — Product/Inventory와 무관) ───
class Reducer(Base):
    __tablename__ = "reducers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    series = Column(String(10), nullable=False, index=True)        # "AB" | "ABR"
    model_name = Column(String(20), nullable=False, index=True)    # "AB042", "AB060A", ...
    stage = Column(Integer, nullable=False)                        # 1 | 2
    ratio_list = Column(JSON)               # 선택 가능 감속비 리스트, 예: [3,4,5,6,7,8,9,10]
    ratio_range_label = Column(String(20))  # 표시용, 예: "3~10"

    input_bore_std_mm = Column(Float)       # 표준 입력홀 최대 허용 축경(mm)
    input_bore_optional_mm = Column(Float)  # 옵션 주문시 최대 허용 축경(mm), 없으면 NULL

    rated_torque_min_nm = Column(Float)     # 감속비 구간 내 정격출력토크 범위
    rated_torque_max_nm = Column(Float)

    rated_input_speed_rpm = Column(Integer)
    max_input_speed_rpm = Column(Integer)

    weight_kg = Column(Float)

    backlash_p0_arcmin = Column(Float)   # nullable — 미생산/특수주문이면 None
    backlash_p1_arcmin = Column(Float)
    backlash_p2_arcmin = Column(Float)
    backlash_note = Column(String(200))  # "P0급 제작안됨" 등 각주 보존

    source_note = Column(String(200))    # 출처, 예: "apex감속기 06AB+Series.pdf p.71(spec)/p.72(dim)"

class KakaoToken(Base):
    """레거시 — 2026-07-29부터 알림 토큰은 AlarmRecipient가 관리한다.
    롤백 대비로 테이블과 모델은 남기되 발송 경로에서는 더 이상 읽지 않는다."""

    __tablename__ = "kakao_tokens"

    id = Column(Integer, primary_key=True, default=1)
    access_token = Column(String(255), nullable=False)
    refresh_token = Column(String(255), nullable=False)
    expires_in = Column(Integer, nullable=False)
    obtained_at = Column(DateTime, nullable=False)


# ─── 9. 알림 수신자 (채널 일반형, 현재는 kakao만 사용) ───
class AlarmRecipient(Base):
    __tablename__ = "alarm_recipients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False, default="kakao")
    channel_token = Column(Text, nullable=False)   # 카카오: refresh_token / 훗날 slack: webhook URL
    # 아래 3개는 카카오 채널만 쓰는 단기 캐시 (다른 채널에서는 NULL)
    access_token = Column(Text)
    token_expires_in = Column(Integer)
    token_obtained_at = Column(DateTime)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


# ─── 10. 경쟁사 단가 제외 키워드 ───
class PriceFilterKeyword(Base):
    __tablename__ = "price_filter_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(50), nullable=False, unique=True)
    is_active = Column(Boolean, default=True, nullable=False)
    note = Column(String(200))    