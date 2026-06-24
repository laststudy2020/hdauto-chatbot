"""
LS iG5A 인버터 카탈로그(표 2-1 외형 치수) 데이터를 NAS MariaDB에 등록한다.

규칙:
  -1 접미사 = 단상 220V
  -2 접미사 = 삼상 220V
  -4 접미사 = 삼상 380V

기존에 접미사 없이 등록되어 있던 12개 제품(SV040IG5 등)은
"-2"(삼상 220V) 버전으로 간주하여 모델명만 갱신하고, 스펙을 채운다.
나머지(-1, -4) 모델은 신규로 추가한다.

사용법:
    pip install sqlalchemy pymysql --break-system-packages
    set MARIADB_HOST=192.168.0.34
    set MARIADB_USER=hdauto
    set MARIADB_PASSWORD=qwg09128@#
    python load_ig5a_catalog.py
"""

import os
import sys

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, JSON,
    ForeignKey, Enum as SQLEnum, create_engine, select
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.engine import URL
from sqlalchemy.sql import func
import enum

# ---- 설정 -------------------------------------------------------------
MARIADB_HOST = os.getenv("MARIADB_HOST", "127.0.0.1")
MARIADB_PORT = os.getenv("MARIADB_PORT", "3306")
MARIADB_USER = os.getenv("MARIADB_USER", "hdauto")
MARIADB_PASSWORD = os.getenv("MARIADB_PASSWORD", "")
MARIADB_DB = os.getenv("MARIADB_DB", "hdauto_chatbot")

MANUFACTURER = "LS"
SERIES = "SV-iG5A"
CATEGORY = "inverter"

# ---- 모델 (app/db/models.py 와 동일한 테이블 구조, sync 버전) -----------
Base = declarative_base()


class ProductStatus(str, enum.Enum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    EOL_SOON = "eol_soon"


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(100), unique=True, nullable=False, index=True)
    series = Column(String(50), index=True)
    manufacturer = Column(String(50), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    status = Column(SQLEnum(ProductStatus), default=ProductStatus.ACTIVE)
    discontinued_date = Column(String(20))
    description = Column(Text)
    smartstore_product_id = Column(String(50))
    our_price = Column(Integer)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    specs = relationship("Specification", back_populates="product", uselist=False)


class Specification(Base):
    __tablename__ = "specifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, unique=True)
    dimension_w = Column(Float)
    dimension_h = Column(Float)
    dimension_d = Column(Float)
    weight_kg = Column(Float)
    input_voltage = Column(String(50))
    output_type = Column(String(50))
    io_points = Column(String(50))
    comm_protocol = Column(String(200))
    operating_temp = Column(String(50))
    protection_class = Column(String(20))
    mounting_type = Column(String(50))
    rated_power = Column(String(50))
    extra_specs = Column(JSON)
    drawing_url = Column(String(500))
    catalog_page = Column(String(50))
    product = relationship("Product", back_populates="specs")


# ---- 표 2-1 외형 치수 데이터 --------------------------------------------
# (코드, 용량kW, W, W1, H, H1, D, phi, A, B, 무게kg)
ROWS_1 = [
    ("004", 0.4, 70, 65.5, 128, 119, 130, 4.0, 4.5, 4.0, 0.76),
    ("008", 0.75, 100, 95.5, 128, 120, 130, 4.5, 4.5, 4.5, 1.12),
    ("015", 1.5, 140, 132, 128, 120.5, 155, 4.5, 4.5, 4.5, 1.84),
]

ROWS_2 = [
    ("004", 0.4, 70, 65.5, 128, 119, 130, 4.0, 4.5, 4.0, 0.76),
    ("008", 0.75, 70, 65.5, 128, 119, 130, 4.0, 4.5, 4.0, 0.77),
    ("015", 1.5, 100, 95.5, 128, 120, 130, 4.5, 4.5, 4.5, 1.12),
    ("022", 2.2, 140, 132, 128, 120.5, 155, 4.5, 4.5, 4.5, 1.84),
    ("037", 3.7, 140, 132, 128, 120.5, 155, 4.5, 4.5, 4.5, 1.89),
    ("040", 4.0, 140, 132, 128, 120.5, 155, 4.5, 4.5, 4.5, 1.89),
    ("055", 5.5, 180, 170, 220, 210, 170, 4.5, 5.0, 4.5, 3.66),
    ("075", 7.5, 180, 170, 220, 210, 170, 4.5, 5.0, 4.5, 3.66),
    ("110", 11.0, 235, 219, 320, 304, 189.5, 7.0, 8.0, 7.0, 9.00),
    ("150", 15.0, 235, 219, 320, 304, 189.5, 7.0, 8.0, 7.0, 9.00),
    ("185", 18.5, 260, 240, 410, 392, 208.5, 10.0, 10.0, 10.0, 13.3),
    ("220", 22.0, 260, 240, 410, 392, 208.5, 10.0, 10.0, 10.0, 13.3),
]

# -4 시리즈는 표에서 -2와 동일한 치수값을 사용
ROWS_4 = ROWS_2

VOLTAGE_BY_SUFFIX = {
    "1": "단상 220V",
    "2": "삼상 220V",
    "4": "삼상 380V",
}


def build_catalog():
    """(model_name, capacity_kw, dims..., voltage, suffix) 전체 목록 생성"""
    catalog = []
    for code, kw, w, w1, h, h1, d, phi, a, b, weight in ROWS_1:
        catalog.append((f"SV{code}iG5A-1", kw, w, w1, h, h1, d, phi, a, b, weight, "1"))
    for code, kw, w, w1, h, h1, d, phi, a, b, weight in ROWS_2:
        catalog.append((f"SV{code}iG5A-2", kw, w, w1, h, h1, d, phi, a, b, weight, "2"))
    for code, kw, w, w1, h, h1, d, phi, a, b, weight in ROWS_4:
        catalog.append((f"SV{code}iG5A-4", kw, w, w1, h, h1, d, phi, a, b, weight, "4"))
    return catalog


# 기존에 접미사 없이 등록되어 있던 모델명 -> 코드 매핑 (이걸 -2로 간주하고 갱신)
LEGACY_NAME_BY_CODE = {
    "004": "SV004IG5",
    "008": "SV008IG5",
    "015": "SV015IG5",
    "022": "SV022IG5",
    "037": "SV037IG5",
    "040": "SV040IG5",
    "055": "SV055IG5",
    "075": "SV075IG5",
    "110": "SV110IG5",
    "150": "SV150IG5",
    "185": "SV185IG5",
    "220": "SV220IG5",
}


def get_or_create_product(session, model_name: str) -> Product:
    product = session.execute(
        select(Product).where(Product.model_name == model_name)
    ).scalars().first()
    if product:
        return product
    product = Product(
        model_name=model_name,
        series=SERIES,
        manufacturer=MANUFACTURER,
        category=CATEGORY,
        status=ProductStatus.ACTIVE,
    )
    session.add(product)
    session.flush()
    return product


def upsert_spec(session, product: Product, kw, w, w1, h, h1, d, phi, a, b, weight, voltage):
    spec = session.execute(
        select(Specification).where(Specification.product_id == product.id)
    ).scalars().first()
    if not spec:
        spec = Specification(product_id=product.id)
        session.add(spec)

    spec.dimension_w = w
    spec.dimension_h = h
    spec.dimension_d = d
    spec.weight_kg = weight
    spec.input_voltage = voltage
    spec.rated_power = f"{kw}kW"
    spec.mounting_type = "벽면"
    spec.extra_specs = {"W1": w1, "H1": h1, "phi": phi, "A": a, "B": b, "source": "표 2-1 외형 치수"}
    spec.catalog_page = "2-1"


def main():
    if not MARIADB_PASSWORD:
        sys.exit("[오류] MARIADB_PASSWORD 환경변수가 비어있습니다.")

    url = URL.create(
        drivername="mysql+pymysql",
        username=MARIADB_USER,
        password=MARIADB_PASSWORD,
        host=MARIADB_HOST,
        port=int(MARIADB_PORT),
        database=MARIADB_DB,
        query={"charset": "utf8mb4"},
    )
    engine = create_engine(url)
    Session = sessionmaker(bind=engine)
    session = Session()

    catalog = build_catalog()
    renamed = 0
    created = 0
    spec_updated = 0

    # 1) 기존 접미사 없는 모델 -> -2 모델명으로 변경
    for code, legacy_name in LEGACY_NAME_BY_CODE.items():
        new_name = f"SV{code}iG5A-2"
        existing = session.execute(
            select(Product).where(Product.model_name == legacy_name)
        ).scalars().first()
        if existing:
            print(f"  [모델명 변경] {legacy_name} -> {new_name}")
            existing.model_name = new_name
            renamed += 1

    session.flush()

    # 2) 카탈로그 27개 전체에 대해 get-or-create + 스펙 upsert
    for model_name, kw, w, w1, h, h1, d, phi, a, b, weight, suffix in catalog:
        before_count = session.execute(select(Product)).scalars().all()
        product = get_or_create_product(session, model_name)
        is_new = product.id is not None and model_name not in [p.model_name for p in before_count]
        voltage = VOLTAGE_BY_SUFFIX[suffix]
        upsert_spec(session, product, kw, w, w1, h, h1, d, phi, a, b, weight, voltage)
        spec_updated += 1
        if is_new:
            created += 1
            print(f"  [신규 등록] {model_name} ({voltage}, {kw}kW)")

    session.commit()
    session.close()

    print("\n--- 완료 ---")
    print(f"기존 모델명 변경: {renamed}개")
    print(f"신규 제품 등록: {created}개")
    print(f"스펙 입력/갱신: {spec_updated}개")


if __name__ == "__main__":
    main()