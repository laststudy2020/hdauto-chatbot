from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.models import Product, Replacement, Specification, ProductStatus, Inventory
from app.models.schemas import ProductCreate, ReplacementCreate, SpecCreate
import csv
import io

router = APIRouter(prefix="/api/products", tags=["products"])


@router.post("/", summary="제품 등록")
async def create_product(data: ProductCreate, db: AsyncSession = Depends(get_db)):
    product = Product(
        model_name=data.model_name,
        series=data.series,
        manufacturer=data.manufacturer,
        category=data.category,
        status=ProductStatus(data.status),
        discontinued_date=data.discontinued_date,
        description=data.description,
        our_price=data.our_price,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return {"id": product.id, "model_name": product.model_name, "status": "created"}


@router.post("/replacement", summary="단종→대체품 매핑 등록")
async def create_replacement(data: ReplacementCreate, db: AsyncSession = Depends(get_db)):
    old = await db.execute(select(Product).where(Product.model_name == data.old_model_name))
    old_product = old.scalar_one_or_none()
    if not old_product:
        raise HTTPException(404, f"단종 모델 '{data.old_model_name}'을 찾을 수 없습니다")

    new = await db.execute(select(Product).where(Product.model_name == data.new_model_name))
    new_product = new.scalar_one_or_none()
    if not new_product:
        raise HTTPException(404, f"대체 모델 '{data.new_model_name}'을 찾을 수 없습니다")

    replacement = Replacement(
        old_model_id=old_product.id,
        new_model_id=new_product.id,
        compatibility_notes=data.compatibility_notes,
        program_convertible=data.program_convertible,
        terminal_compatible=data.terminal_compatible,
        dimension_compatible=data.dimension_compatible,
        source_url=data.source_url,
    )
    db.add(replacement)
    await db.commit()
    return {"status": "mapped", "old": data.old_model_name, "new": data.new_model_name}


@router.post("/specs", summary="제품 스펙 등록 (단건)")
async def create_spec(data: SpecCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(Specification).where(Specification.product_id == data.product_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"product_id {data.product_id} 스펙이 이미 존재합니다. 수정은 PUT /specs를 사용하세요.")
    spec = Specification(**data.model_dump())
    db.add(spec)
    await db.commit()
    return {"status": "created", "product_id": data.product_id}


@router.post("/import/csv", summary="제품 CSV 일괄 등록")
async def import_products_csv(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """
    CSV 컬럼: model_name, series, manufacturer, category, status, discontinued_date, our_price
    status 값: active / discontinued / eol_soon
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp949")

    reader = csv.DictReader(io.StringIO(text))
    created, skipped, errors = 0, 0, []

    for i, row in enumerate(reader, 2):
        model = row.get("model_name", "").strip()
        if not model:
            continue
        existing = await db.execute(select(Product).where(Product.model_name == model))
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        try:
            status_val = row.get("status", "active").strip().lower()
            product = Product(
                model_name=model,
                series=row.get("series", "").strip() or None,
                manufacturer=row.get("manufacturer", "").strip(),
                category=row.get("category", "").strip(),
                status=ProductStatus(status_val),
                discontinued_date=row.get("discontinued_date", "").strip() or None,
                our_price=int(row["our_price"]) if row.get("our_price", "").strip() else None,
            )
            db.add(product)
            created += 1
        except Exception as e:
            errors.append(f"행 {i} ({model}): {str(e)}")

    await db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


@router.post("/import/specs-csv", summary="스펙 CSV 일괄 등록")
async def import_specs_csv(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """
    CSV 컬럼: model_name, dimension_w, dimension_h, dimension_d, weight_kg,
               input_voltage, output_type, io_points, comm_protocol,
               operating_temp, protection_class, mounting_type, rated_power, drawing_url
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp949")

    reader = csv.DictReader(io.StringIO(text))
    created, skipped, errors = 0, 0, []

    for i, row in enumerate(reader, 2):
        model = row.get("model_name", "").strip()
        if not model:
            continue

        product_res = await db.execute(
            select(Product).where(Product.model_name.ilike(f"%{model}%"))
        )
        product = product_res.scalars().first()
        if not product:
            errors.append(f"행 {i}: '{model}' 제품을 찾을 수 없음")
            continue

        existing_spec = await db.execute(
            select(Specification).where(Specification.product_id == product.id)
        )
        if existing_spec.scalar_one_or_none():
            skipped += 1
            continue

        def _f(key):
            v = row.get(key, "").strip()
            return float(v) if v else None

        def _s(key):
            v = row.get(key, "").strip()
            return v or None

        try:
            spec = Specification(
                product_id=product.id,
                dimension_w=_f("dimension_w"),
                dimension_h=_f("dimension_h"),
                dimension_d=_f("dimension_d"),
                weight_kg=_f("weight_kg"),
                input_voltage=_s("input_voltage"),
                output_type=_s("output_type"),
                io_points=_s("io_points"),
                comm_protocol=_s("comm_protocol"),
                operating_temp=_s("operating_temp"),
                protection_class=_s("protection_class"),
                mounting_type=_s("mounting_type"),
                rated_power=_s("rated_power"),
                drawing_url=_s("drawing_url"),
            )
            db.add(spec)
            created += 1
        except Exception as e:
            errors.append(f"행 {i} ({model}): {str(e)}")

    await db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


@router.post("/import/replacements-csv", summary="단종→대체품 매핑 CSV 일괄 등록")
async def import_replacements_csv(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """
    CSV 컬럼: old_model_name, new_model_name, compatibility_notes,
               program_convertible, terminal_compatible, dimension_compatible, source_url
    boolean 값: true / false
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp949")

    reader = csv.DictReader(io.StringIO(text))
    created, skipped, errors = 0, 0, []

    for i, row in enumerate(reader, 2):
        old_name = row.get("old_model_name", "").strip()
        new_name = row.get("new_model_name", "").strip()
        if not old_name or not new_name:
            continue

        old_res = await db.execute(select(Product).where(Product.model_name == old_name))
        old_p = old_res.scalar_one_or_none()
        if not old_p:
            errors.append(f"행 {i}: 단종 모델 '{old_name}' 없음")
            continue

        new_res = await db.execute(select(Product).where(Product.model_name == new_name))
        new_p = new_res.scalar_one_or_none()
        if not new_p:
            errors.append(f"행 {i}: 대체 모델 '{new_name}' 없음")
            continue

        dup = await db.execute(
            select(Replacement).where(
                Replacement.old_model_id == old_p.id,
                Replacement.new_model_id == new_p.id
            )
        )
        if dup.scalar_one_or_none():
            skipped += 1
            continue

        def _bool(key):
            return row.get(key, "false").strip().lower() == "true"

        rep = Replacement(
            old_model_id=old_p.id,
            new_model_id=new_p.id,
            compatibility_notes=row.get("compatibility_notes", "").strip() or None,
            program_convertible=_bool("program_convertible"),
            terminal_compatible=_bool("terminal_compatible"),
            dimension_compatible=_bool("dimension_compatible"),
            source_url=row.get("source_url", "").strip() or None,
        )
        db.add(rep)
        created += 1

    await db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


@router.post("/import/inventory-csv", summary="재고 CSV 일괄 등록")
async def import_inventory_csv(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """
    CSV 컬럼: model_name, current_stock, min_threshold, warehouse_location
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp949")

    reader = csv.DictReader(io.StringIO(text))
    created, updated, errors = 0, 0, []

    for i, row in enumerate(reader, 2):
        model = row.get("model_name", "").strip()
        if not model:
            continue

        product_res = await db.execute(
            select(Product).where(Product.model_name.ilike(f"%{model}%"))
        )
        product = product_res.scalars().first()
        if not product:
            errors.append(f"행 {i}: '{model}' 제품 없음")
            continue

        existing = await db.execute(
            select(Inventory).where(Inventory.product_id == product.id)
        )
        inv = existing.scalar_one_or_none()

        stock = int(row.get("current_stock", 0))
        threshold = int(row.get("min_threshold", 3))
        location = row.get("warehouse_location", "").strip() or None

        if inv:
            inv.current_stock = stock
            inv.min_threshold = threshold
            inv.warehouse_location = location
            updated += 1
        else:
            db.add(Inventory(
                product_id=product.id,
                current_stock=stock,
                min_threshold=threshold,
                warehouse_location=location,
            ))
            created += 1

    await db.commit()
    return {"created": created, "updated": updated, "errors": errors}


@router.get("/search/{model_name}", summary="모델명 검색")
async def search_product(model_name: str, db: AsyncSession = Depends(get_db)):
    stmt = select(Product).where(Product.model_name.ilike(f"%{model_name}%")).limit(10)
    result = await db.execute(stmt)
    products = result.scalars().all()
    return [
        {
            "id": p.id,
            "model_name": p.model_name,
            "manufacturer": p.manufacturer,
            "category": p.category,
            "status": p.status.value,
        }
        for p in products
    ]


@router.get("/stats", summary="DB 통계")
async def get_stats(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Product.id)))).scalar()
    active = (await db.execute(
        select(func.count(Product.id)).where(Product.status == ProductStatus.ACTIVE)
    )).scalar()
    disc = (await db.execute(
        select(func.count(Product.id)).where(Product.status == ProductStatus.DISCONTINUED)
    )).scalar()
    mappings = (await db.execute(select(func.count(Replacement.id)))).scalar()
    specs = (await db.execute(select(func.count(Specification.id)))).scalar()
    inv = (await db.execute(select(func.count(Inventory.id)))).scalar()

    return {
        "total_products": total,
        "active": active,
        "discontinued": disc,
        "replacement_mappings": mappings,
        "specs_registered": specs,
        "inventory_records": inv,
    }
