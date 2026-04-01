from fastapi import APIRouter, HTTPException, Query, status, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Optional, List
import math
import json
import cloudinary
import cloudinary.uploader
from app.routes.auth import get_optional_user, get_current_user
from fastapi import Request
import cloudinary.api
from app.database_postgres import get_db_connection
from psycopg2.extras import RealDictCursor
from app.core.product_helpers import get_product_status

router = APIRouter(
    prefix="/products",
    tags=["Products"]
)

# ============================================================
# Cloudinary Configuration
# ⚠️ SECURITY: Move secrets to environment variables in production
# ============================================================
cloudinary.config(
    cloud_name="ddzk9wuye",
    api_key="722337771623846",
    api_secret="8OdvM4h-d8MYdu1ciey5c_wITH8"
)

CLOUDINARY_FOLDER = "products"


# ============================================================
# Auth Helpers
# ============================================================

def get_admin_user(current_user: dict = Depends(get_current_user)):
    """Verify current user has Admin role."""
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ليس لديك صلاحية لتنفيذ هذا الإجراء. يجب أن تكون Admin"
        )
    return current_user


# ============================================================
# Cloudinary Helper Functions
# ============================================================

def extract_public_id(image_url: str) -> Optional[str]:
    """
    Extract Cloudinary public_id from a secure_url.
    Returns None if URL is invalid or parsing fails.
    """
    if not image_url:
        return None
    try:
        after_upload = image_url.split("/upload/")[1]
        parts = after_upload.split("/")
        if parts[0].startswith("v") and parts[0][1:].isdigit():
            parts = parts[1:]
        public_id = "/".join(parts).rsplit(".", 1)[0]
        return public_id
    except (IndexError, AttributeError):
        return None


def delete_from_cloudinary(image_url: str) -> bool:
    """
    Delete a single image from Cloudinary by its URL.
    Returns True on success, False on failure.
    Never raises — safe to call in cleanup paths.
    """
    if not image_url:
        return True
    public_id = extract_public_id(image_url)
    if not public_id:
        return False
    try:
        result = cloudinary.uploader.destroy(public_id)
        return result.get("result") == "ok"
    except Exception as e:
        logger.warning(f"[Cloudinary] Warning: Could not delete '{public_id}': {e}")
        return False


import logging

logger = logging.getLogger(__name__)

def delete_multiple_images(image_urls: List[str]) -> None:
    """
    Delete a list of Cloudinary image URLs.
    Failures are logged but never raised.
    """
    for url in image_urls:
        delete_from_cloudinary(url)


def upload_to_cloudinary(file: UploadFile) -> str:
    """
    Read an UploadFile and upload its contents to Cloudinary.
    Returns the secure_url string.
    Raises HTTPException(502) if the upload fails.
    """
    try:
        contents = file.file.read()
        result = cloudinary.uploader.upload(
            contents,
            folder=CLOUDINARY_FOLDER,
            resource_type="image",
            unique_filename=True,
            use_filename=False,
        )
        return result["secure_url"]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"فشل رفع الصورة إلى Cloudinary: {str(e)}"
        )


# ============================================================
# FIX: Images Field Helpers
# Centralized parsing/serialization — used by ALL endpoints.
# Handles both new JSON format and legacy comma-separated strings.
# ============================================================

def parse_images_field(product: dict) -> dict:
    """
    Normalize the `images` field on a product dict to always be a Python list.

    Handles three cases:
      1. Already a list (e.g. in-memory after create) — returned as-is.
      2. JSON string  → parsed into a list.
      3. Legacy comma-separated string → split into a list (backward compat).
      4. None / empty → returns empty list.
    """
    raw = product.get("images")

    if not raw:
        product["images"] = []
        return product

    if isinstance(raw, list):
        # Already a list — nothing to do
        product["images"] = raw
        return product

    # Try JSON first (new format)
    try:
        parsed = json.loads(raw)
        product["images"] = parsed if isinstance(parsed, list) else []
        return product
    except (json.JSONDecodeError, TypeError):
        pass

    # FIX: Fallback — legacy comma-separated string (backward compatibility)
    product["images"] = [url.strip() for url in raw.split(",") if url.strip()]
    return product


def serialize_gallery(image_urls: List[str]) -> Optional[str]:
    """
    Serialize a list of image URLs to a JSON string for DB storage.
    Returns None if the list is empty.

    FIX: Changed from comma-separated to JSON for consistency.
    """
    return json.dumps(image_urls) if image_urls else None


def parse_gallery(raw: Optional[str]) -> List[str]:
    """
    Parse a raw DB images value (JSON string or legacy comma-separated)
    into a clean Python list. Returns [] if empty or invalid.

    FIX: Now handles JSON format in addition to legacy comma-separated.
    """
    if not raw:
        return []

    # Try JSON first (new format)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: legacy comma-separated string
    return [url.strip() for url in raw.split(",") if url.strip()]


def attach_product_extras(product: dict) -> dict:
    """
    Convenience wrapper that applies both status calculation and images
    normalization to a single product dict.
    """
    product["status"] = get_product_status(product)
    return parse_images_field(product)


# ============================================================
# Shared Response Builders
# ============================================================

import uuid
import datetime
from decimal import Decimal

def safe_convert(obj):
    """
    Recursively converts database objects into JSON-safe formats.
    Handles Decimal -> float, datetime -> ISO string, and UUID -> string
    to prevent FastAPI 500 serialization errors.
    """
    if isinstance(obj, dict):
        return {k: safe_convert(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [safe_convert(v) for v in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, datetime.datetime):
        return obj.isoformat()
    elif isinstance(obj, datetime.date):
        return obj.isoformat()
    elif isinstance(obj, uuid.UUID):
        return str(obj)
    # Fallback to string for unknown memoryviews or binary objects
    elif type(obj).__name__ == 'memoryview':
        try:
            return obj.tobytes().hex()
        except BaseException:
            return str(obj)
    return obj


def _attach_status(products: List[dict]) -> List[dict]:
    """
    Attach dynamic status AND normalize images field for a list of products.
    FIX: Safely iterate over products and handle errors individually so one failure 
    doesn't break the response.
    """
    safe_products = []
    
    if not isinstance(products, list):
        return safe_products

    for p in products:
        if not p or not isinstance(p, dict):
            continue
            
        try:
            # Operate on a copy to prevent partial mutations 
            safe_p = attach_product_extras(p.copy())
            safe_products.append(safe_p)
        except Exception as e:
            logger.error(f"Error attaching status/images for product {p.get('id', 'unknown')}: {e}")
            
            # Defensive fallback ensures the product is returned as valid JSON
            fallback_p = p.copy()
            fallback_p["status"] = fallback_p.get("status", [])
            
            raw_img = fallback_p.get("images")
            if isinstance(raw_img, list):
                pass
            elif isinstance(raw_img, str):
                try:
                    parsed = json.loads(raw_img)
                    fallback_p["images"] = parsed if isinstance(parsed, list) else []
                except Exception:
                    fallback_p["images"] = [u.strip() for u in raw_img.split(",") if u.strip()]
            else:
                fallback_p["images"] = []
                
            safe_products.append(fallback_p)

    return safe_products


def _build_pagination(total: int, page: int, page_size: int) -> dict:
    """Build reusable pagination metadata."""
    total_pages = math.ceil(total / page_size) if page_size else 1
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


# ============================================================
# Schemas
# ============================================================

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    discount_price: Optional[float] = None
    stock_quantity: Optional[int] = None
    category_id: Optional[int] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    weight: Optional[float] = None
    dimensions: Optional[str] = None
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None


# ============================================================
# 0. Search Products
# ============================================================

@router.get("/search")
def search_products(
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    """Enhanced search across multiple product fields."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        params = []
        base_query = """
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.is_active = TRUE
        """

        if q:
            words = q.lower().split()  # دعم multi-keywords
            conditions = []

            for word in words:
                like_word = f"%{word}%"
                conditions.append("""
                    (
                        LOWER(p.name) LIKE %s OR
                        LOWER(COALESCE(p.description, '')) LIKE %s OR
                        LOWER(COALESCE(c.name, '')) LIKE %s OR
                        LOWER(COALESCE(p.brand, '')) LIKE %s OR
                        LOWER(COALESCE(p.sku, '')) LIKE %s OR
                        LOWER(COALESCE(p.dimensions, '')) LIKE %s
                    )
                """)
                params.extend([like_word] * 6)

            base_query += " AND " + " AND ".join(conditions)

        # total count
        cursor.execute(f"SELECT COUNT(*) as count {base_query}", params)
        total = cursor.fetchone()['count']

        pagination = _build_pagination(total, page, limit)

        data_params = params + [limit, (page - 1) * limit]

        cursor.execute(
            f"""
            SELECT p.*, c.name as category_name
            {base_query}
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
            """,
            data_params
        )

        products = _attach_status(
            [dict(row) for row in cursor.fetchall()]
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {
            "type": "search" if q else "fallback",
            **pagination,
            "products": products,
        }
    }
# ============================================================
# 1. Create Product (Admin only) — with optional image upload
# ============================================================

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_product(
    name: str = Form(..., min_length=1, max_length=255),
    price: float = Form(..., gt=0),
    category_id: int = Form(...),
    description: Optional[str] = Form(None),
    discount_price: Optional[float] = Form(None),
    stock_quantity: int = Form(0),
    brand: Optional[str] = Form(None),
    sku: Optional[str] = Form(None),
    weight: Optional[float] = Form(None),
    dimensions: Optional[str] = Form(None),
    is_active: bool = Form(True),
    is_featured: bool = Form(False),
    image: Optional[UploadFile] = File(None),
    gallery_images: Optional[List[UploadFile]] = File(None),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Create a new product.
    Accepts multipart/form-data with optional main image and gallery images.
    """

    # Upload main image
    image_url: Optional[str] = None
    if image and image.filename:
        image_url = upload_to_cloudinary(image)

    # Upload gallery images
    gallery_urls: List[str] = []
    if gallery_images:
        for gfile in gallery_images:
            if gfile and gfile.filename:
                url = upload_to_cloudinary(gfile)
                gallery_urls.append(url)

    # FIX: Serialize as JSON instead of comma-separated
    images_str = serialize_gallery(gallery_urls)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Guard: duplicate SKU
        if sku:
            cursor.execute("SELECT id FROM products WHERE sku = %s", (sku,))
            if cursor.fetchone():
                delete_from_cloudinary(image_url)
                delete_multiple_images(gallery_urls)
                conn.commit()
                conn.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="رمز المنتج (SKU) موجود بالفعل"
                )

        cursor.execute("""
            INSERT INTO products (
                name, description, price, discount_price, stock_quantity,
                category_id, image_url, images, brand, sku, weight,
                dimensions, is_active, is_featured
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            name, description, price, discount_price, stock_quantity,
            category_id, image_url, images_str, brand, sku, weight,
            dimensions, is_active, is_featured
        ))

        product_id = cursor.fetchone()['id']
        cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        new_product = attach_product_extras(dict(cursor.fetchone()))
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم إنشاء المنتج بنجاح",
        "data": new_product
    }


# ============================================================
# 2. List All Products (Public)
# ============================================================

@router.get("/")
def get_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    is_active: Optional[bool] = Query(None),
    is_featured: Optional[bool] = Query(None)
):
    """Get a paginated, filterable list of products."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        base_query = "FROM products WHERE 1=1"
        params = []

        if search:
            base_query += " AND name LIKE %s"
            params.append(f"%{search}%")
        if category_id:
            base_query += " AND category_id = %s"
            params.append(category_id)
        if min_price is not None:
            base_query += " AND price >= %s"
            params.append(min_price)
        if max_price is not None:
            base_query += " AND price <= %s"
            params.append(max_price)
        if is_active is not None:
            base_query += " AND is_active = %s"
            params.append(is_active)
        if is_featured is not None:
            base_query += " AND is_featured = %s"
            params.append(is_featured)

        cursor.execute(f"SELECT COUNT(*) as count {base_query}", params)
        total = cursor.fetchone()['count']

        pagination = _build_pagination(total, page, page_size)

        data_params = params + [page_size, (page - 1) * page_size]
        cursor.execute(f"SELECT * {base_query} ORDER BY created_at DESC LIMIT %s OFFSET %s", data_params)
        
        products = _attach_status([dict(row) for row in cursor.fetchall()])
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {**pagination, "products": products}
    }


# ============================================================
# 3. New Arrivals (Public)
# ============================================================
import traceback

@router.get("/new-arrivals")
def get_new_arrivals(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100)
):
    """
    Returns products added in the last 7 days.
    Falls back to most recent products if none found.
    """

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # SQL FIX: Use safe casting for TEXT date columns to TIMESTAMP
        # COALESCE(NULLIF(updated_at, '')::timestamp, created_at::timestamp) handles NULL, empty, and inconsistent data
        cursor.execute("""
            SELECT COUNT(*) as count FROM products
            WHERE is_active = TRUE 
            AND COALESCE(NULLIF(updated_at, '')::timestamp, created_at::timestamp) >= NOW() - INTERVAL '7 days'
        """)
        
        count_row = cursor.fetchone()
        total = count_row['count'] if count_row else 0

        if total == 0:
            cursor.execute("SELECT COUNT(*) as count FROM products WHERE is_active = TRUE")
            count_row = cursor.fetchone()
            total = count_row['count'] if count_row else 0

            query_type = "fallback_newest"
            data_query = """
                SELECT * FROM products
                WHERE is_active = TRUE
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """
        else:
            query_type = "new_arrivals"
            # SQL FIX: Apply the same safe casting here for consistent results
            data_query = """
                SELECT * FROM products
                WHERE is_active = TRUE
                AND COALESCE(NULLIF(updated_at, '')::timestamp, created_at::timestamp) >= NOW() - INTERVAL '7 days'
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """

        pagination = _build_pagination(total, page, page_size)

        cursor.execute(data_query, (page_size, (page - 1) * page_size))
        
        rows = cursor.fetchall()
        products = [safe_convert(dict(row)) for row in rows if row]
        products = _attach_status(products)

        conn.commit()

        return {
            "status": "success",
            "data": {
                "type": query_type,
                **pagination,
                "products": products
            }
        }

    except Exception as e:
        # Improved error handling: replaced print with logger
        logger.error(f"Critical Error in /new-arrivals: {str(e)}")
        logger.error(traceback.format_exc())
        conn.rollback()
        raise  # Do not mask the exception; let it natively log and trigger a real 500

    finally:
        conn.close()

# ============================================================
# 4. Best Deals (Public)
# ============================================================

@router.get("/best-deals")
def get_best_deals(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100)
):
    """Products with a discount of 14% or more, sorted by highest discount."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        discount_filter = """
            FROM products
            WHERE is_active = TRUE
            AND discount_price IS NOT NULL
            AND price > 0
            AND ((price - discount_price) / price * 100) >= 14
        """

        cursor.execute(f"SELECT COUNT(*) as count {discount_filter}")
        total = cursor.fetchone()['count']

        pagination = _build_pagination(total, page, page_size)

        cursor.execute(f"""
            SELECT *, ((price - discount_price) / price * 100) as discount_percent
            {discount_filter}
            ORDER BY discount_percent DESC
            LIMIT %s OFFSET %s
        """, (page_size, (page - 1) * page_size))

        products = _attach_status([dict(row) for row in cursor.fetchall()])
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {**pagination, "products": products}
    }


# ============================================================
# 5. Featured Products (Public)
# ============================================================

@router.get("/featured")
def get_featured(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    """
    Featured products first, then sorted by rating and recency.
    Naturally falls back to highest-rated active products.
    """

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT * FROM products
            WHERE is_active = TRUE
            ORDER BY is_featured DESC, rating DESC, created_at DESC
            LIMIT %s OFFSET %s
        """, (limit, (page - 1) * limit))

        products = _attach_status([dict(row) for row in cursor.fetchall()])
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {"products": products}
    }


# ============================================================
# 6. Get Single Product (Public)
# ============================================================

@router.get("/{product_id}")
def get_product(product_id: int):
    """Get full details of a single product."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()

        if not row:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )

        product = attach_product_extras(dict(row))
        conn.commit()
    finally:
        conn.close()

    return {"status": "success", "data": product}


# ============================================================
# 7. Update Product (Admin only) — with optional image replace
# ============================================================

@router.put("/{product_id}")
async def update_product(
    product_id: int,
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    price: Optional[float] = Form(None),
    discount_price: Optional[float] = Form(None),
    stock_quantity: Optional[int] = Form(None),
    category_id: Optional[int] = Form(None),
    brand: Optional[str] = Form(None),
    sku: Optional[str] = Form(None),
    weight: Optional[float] = Form(None),
    dimensions: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    is_featured: Optional[bool] = Form(None),
    image: Optional[UploadFile] = File(None),
    new_gallery_images: Optional[List[UploadFile]] = File(None),
    remove_gallery_urls: Optional[str] = Form(None),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Update a product. Accepts multipart/form-data.

    Image handling:
    - `image`: If provided, replaces the main image (old one deleted from Cloudinary).
    - `new_gallery_images`: Appended to the existing gallery.
    - `remove_gallery_urls`: Comma-separated URLs to remove from the gallery.
    """

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        existing = cursor.fetchone()

        if not existing:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )
        existing = dict(existing)

        # ── Main image replacement ──────────────────────────────
        new_image_url: Optional[str] = None
        if image and image.filename:
            new_image_url = upload_to_cloudinary(image)
            delete_from_cloudinary(existing.get("image_url"))

        # ── Gallery management ──────────────────────────────────
        current_gallery = parse_gallery(existing.get("images"))

        # Remove requested URLs
        urls_to_remove = parse_gallery(remove_gallery_urls)
        if urls_to_remove:
            delete_multiple_images(urls_to_remove)
            current_gallery = [u for u in current_gallery if u not in urls_to_remove]

        # Upload and append new gallery images
        if new_gallery_images:
            for gfile in new_gallery_images:
                if gfile and gfile.filename:
                    url = upload_to_cloudinary(gfile)
                    current_gallery.append(url)

        updated_images_str = serialize_gallery(current_gallery)

        # ── Build dynamic UPDATE query ──────────────────────────
        update_fields: List[str] = []
        params: List = []

        field_map = {
            "name": name,
            "description": description,
            "price": price,
            "discount_price": discount_price,
            "stock_quantity": stock_quantity,
            "category_id": category_id,
            "brand": brand,
            "sku": sku,
            "weight": weight,
            "dimensions": dimensions,
            "is_active": is_active,
            "is_featured": is_featured,
        }

        for field, value in field_map.items():
            if value is not None:
                update_fields.append(f"{field} = %s")
                params.append(value)

        if new_image_url is not None:
            update_fields.append("image_url = %s")
            params.append(new_image_url)

        # Always persist gallery state when gallery was touched
        if urls_to_remove or (new_gallery_images and any(f.filename for f in new_gallery_images if f)):
            update_fields.append("images = %s")
            params.append(updated_images_str)

        if not update_fields:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="لا توجد حقول للتحديث"
            )

        update_fields.append("updated_at = NOW() + INTERVAL '2 hours'")
        params.append(product_id)

        cursor.execute(
            f"UPDATE products SET {', '.join(update_fields)} WHERE id = %s",
            params
        )
        cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        updated_product = attach_product_extras(dict(cursor.fetchone()))
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم تحديث المنتج بنجاح",
        "data": updated_product
    }


# ============================================================
# 8. Delete Product (Admin only) — with Cloudinary cleanup
# ============================================================

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    admin_user: dict = Depends(get_admin_user)
):
    """
    Delete a product and all its associated Cloudinary images
    (main image + all gallery images).
    """

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT image_url, images FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()

        if not row:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )

        main_image_url = row['image_url']
        gallery_urls = parse_gallery(row['images'])

        delete_from_cloudinary(main_image_url)
        delete_multiple_images(gallery_urls)

        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
        conn.commit()
    finally:
        conn.close()

    return None


# ============================================================
# 9. Update Stock (Admin only)
# ============================================================

@router.patch("/{product_id}/stock")
def update_stock(
    product_id: int,
    quantity: int = Query(..., description="الكمية الجديدة", ge=0),
    admin_user: dict = Depends(get_admin_user)
):
    """Update product stock quantity."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id FROM products WHERE id = %s", (product_id,))
        if not cursor.fetchone():
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )

        cursor.execute(
            "UPDATE products SET stock_quantity = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (quantity, product_id)
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم تحديث المخزون بنجاح",
        "new_stock": quantity
    }

@router.post("/{product_id}/reviews")
async def add_review(
    product_id: int,
    rating: float = Form(..., ge=0, le=5),
    comment: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]

    if rating * 2 != int(rating * 2):
        raise HTTPException(
            status_code=400,
            detail="Rating must be in 0.5 increments (e.g. 1, 1.5, 2, ...)"
        )

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # ✅ check limit (max 3 reviews per user)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM reviews
            WHERE product_id = %s AND user_id = %s
        """, (product_id, user_id))

        count = cursor.fetchone()["count"]

        if count >= 3:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=400,
                detail="مسموح لك بحد أقصى 3 تقييمات لهذا المنتج"
            )

        # ✅ insert review
        cursor.execute("""
            INSERT INTO reviews (product_id, user_id, rating, comment, created_at)
            VALUES (%s, %s, %s, %s, NOW() + INTERVAL '2 hours') RETURNING id
        """, (product_id, user_id, rating, comment))

        review_id = cursor.fetchone()['id']

        # ✅ get review with user data (JOIN)
        cursor.execute("""
            SELECT 
                r.*, 
                COALESCE(u.full_name, u.username) as user_name,
                u.avatar as user_avatar
            FROM reviews r
            LEFT JOIN users u ON r.user_id = u.id
            WHERE r.id = %s
        """, (review_id,))

        review = dict(cursor.fetchone())

        # ✅ permissions
        review["can_edit"] = True
        review["can_delete"] = True

        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم إضافة التقييم",
        "data": review
    }


@router.get("/{product_id}/reviews")
async def get_reviews(
    product_id: int,
    request: Request,
    page: int = 1
):
    try:
        token = request.headers.get("Authorization")
        if token:
            token = token.replace("Bearer ", "")
            current_user = get_current_user(token)
        else:
            current_user = None
    except:
        current_user = None

    limit = 3
    offset = (page - 1) * limit 

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
         SELECT 
             r.*, 
             COALESCE(u.full_name, u.username) as user_name,
             u.avatar as user_avatar
         FROM reviews r
         LEFT JOIN users u ON r.user_id = u.id
         WHERE r.product_id = %s
         ORDER BY r.created_at DESC
         LIMIT %s OFFSET %s
        """, (product_id, limit, offset))

        reviews = []
        for row in cursor.fetchall():
            review = dict(row)

            is_owner = current_user is not None and review["user_id"] == current_user["id"]
            is_admin = current_user is not None and current_user["role"] == "admin"

            review["can_edit"] = is_owner
            review["can_delete"] = is_owner or is_admin

            reviews.append(review)

        cursor.execute("""
            SELECT COUNT(*) as count FROM reviews WHERE product_id = %s
        """, (product_id,))
        total = cursor.fetchone()['count']

        cursor.execute("""
            SELECT AVG(rating) as avg_rating FROM reviews WHERE product_id = %s
        """, (product_id,))
        avg_rating = cursor.fetchone()['avg_rating']

        conn.commit()
    finally:
        conn.close()

    return {
        "reviews": reviews,
        "average_rating": round(avg_rating or 0, 1),
        "total_reviews": total,

        "next": offset + limit < total,
        "prev": page > 1,
        "current_page": page
    }


@router.get("/{product_id}/related")
def get_related_products(
    product_id: int,
    limit: int = Query(20, ge=1, le=20)
):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. المنتج الحالي
        cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()

        if not product:
            conn.commit()
            conn.close()
            raise HTTPException(status_code=404, detail="Product not found")

        product = dict(product)

        # 💥 clean brand
        current_brand = (product.get("brand") or "").strip().lower()

        cursor.execute("""
            SELECT *,
            CASE
                WHEN LOWER(TRIM(COALESCE(brand, ''))) = %s THEN 1
                WHEN category_id = %s THEN 2
                ELSE 3
            END as priority
            FROM products
            WHERE id != %s
            AND is_active = TRUE
            ORDER BY priority ASC, created_at DESC
            LIMIT %s
        """, (
            current_brand,
            product["category_id"],
            product_id,
            limit
        ))

        related = cursor.fetchall()

        products = _attach_status([dict(row) for row in related])

        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {
            "products": products
        }
    }