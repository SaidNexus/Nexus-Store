from fastapi import APIRouter, HTTPException, status, Depends, Query, UploadFile, File, Form, Request
from pydantic import BaseModel, Field
from typing import Optional
import math
import cloudinary
import cloudinary.uploader
import cloudinary.api
from app.database_postgres import get_db_connection
from psycopg2.extras import RealDictCursor
from app.routes.auth import get_current_user

router = APIRouter(
    prefix="/categories",
    tags=["Categories"]
)

cloudinary.config(
    cloud_name="ddzk9wuye",
    api_key="722337771623846",
    api_secret="8OdvM4h-d8MYdu1ciey5c_wITH8"
)

CLOUDINARY_FOLDER = "categories"


# ============================================================
# Helper Functions
# ============================================================

def get_admin_user(current_user: dict = Depends(get_current_user)):
    """Verify current user has Admin role."""
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ليس لديك صلاحية لتنفيذ هذا الإجراء"
        )
    return current_user


def extract_public_id(image_url: str) -> Optional[str]:
    """
    Extract Cloudinary public_id from a secure_url.
    Example URL: https://res.cloudinary.com/<cloud>/image/upload/v123/<folder>/<public_id>.jpg
    Returns: "<folder>/<public_id>"
    """
    if not image_url:
        return None
    try:
        # Split on '/upload/' and take everything after it
        after_upload = image_url.split("/upload/")[1]
        # Remove version segment if present (e.g. "v1234567890/")
        parts = after_upload.split("/")
        if parts[0].startswith("v") and parts[0][1:].isdigit():
            parts = parts[1:]
        # Remove file extension
        path_without_ext = "/".join(parts)
        public_id = path_without_ext.rsplit(".", 1)[0]
        return public_id
    except (IndexError, AttributeError):
        return None


def delete_image_from_cloudinary(image_url: str) -> bool:
    """
    Delete an image from Cloudinary using its URL.
    Returns True on success, False on failure (does NOT raise exceptions).
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
        # Log the error but do not break the main flow
        logger.warning(f"[Cloudinary] Warning: Failed to delete image '{public_id}': {e}")
        return False


import logging

logger = logging.getLogger(__name__)

def upload_image_to_cloudinary(file: UploadFile) -> str:
    """
    Upload an image file to Cloudinary under the categories folder.
    Returns the secure_url string.
    Raises HTTPException on failure.
    """
    try:
        contents = file.file.read()
        result = cloudinary.uploader.upload(
            contents,
            folder=CLOUDINARY_FOLDER,
            resource_type="image"
        )
        return result["secure_url"]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"فشل رفع الصورة إلى Cloudinary: {str(e)}"
        )
    finally:
        file.file.seek(0)  # reset for safety


# ============================================================
# Schemas
# ============================================================

class CategoryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    image_url: Optional[str]
    is_active: bool
    created_at: str
    product_count: int = 0


# ============================================================
# 1. Create Category (Admin only)
# ============================================================

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_category(
    name: str = Form(..., min_length=1, max_length=100),
    description: Optional[str] = Form(None),
    is_active: bool = Form(True),
    image: Optional[UploadFile] = File(None),
    admin_user: dict = Depends(get_admin_user)
):
    """Create a new category. Accepts multipart/form-data with optional image upload."""

    image_url: Optional[str] = None

    # Upload image if provided
    if image and image.filename:
        image_url = upload_image_to_cloudinary(image)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Check for duplicate name
        cursor.execute("SELECT id FROM categories WHERE name = %s", (name,))
        if cursor.fetchone():
            # Rollback uploaded image to avoid orphans
            if image_url:
                delete_image_from_cloudinary(image_url)
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="اسم الفئة موجود بالفعل"
            )

        cursor.execute("""
            INSERT INTO categories (name, description, image_url, is_active, created_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP) RETURNING id
        """, (name, description, image_url, is_active))

        category_id = cursor.fetchone()['id']

        cursor.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
        new_category = cursor.fetchone()
        new_category["product_count"] = 0
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم إنشاء الفئة بنجاح",
        "data": new_category
    }


# ============================================================
# 2. List All Categories (Public)
# ============================================================

@router.get("/")
def get_categories(
    page: int = Query(1, ge=1, description="رقم الصفحة"),
    page_size: int = Query(20, ge=1, le=100, description="عدد الفئات في الصفحة"),
    search: Optional[str] = Query(None, description="البحث في اسم الفئة"),
    is_active: Optional[bool] = Query(None, description="تصفية حسب الحالة"),
    include_products: bool = Query(True, description="تضمين عدد المنتجات")
):
    """Get a paginated list of categories."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        query = "SELECT * FROM categories WHERE 1=1"
        params = []

        if search:
            query += " AND name LIKE %s"
            params.append(f"%{search}%")

        if is_active is not None:
            query += " AND is_active = %s"
            params.append(is_active)

        # Total count
        count_query = query.replace("SELECT *", "SELECT COUNT(*) as count")
        cursor.execute(count_query, params)
        total = cursor.fetchone()['count']

        total_pages = math.ceil(total / page_size) if page_size else 1

        # Paginated data
        query += " ORDER BY name LIMIT %s OFFSET %s"
        params.extend([page_size, (page - 1) * page_size])

        cursor.execute(query, params)
        categories = cursor.fetchall()

        # 🔥 التعديل هنا بس
        query_names = """
            SELECT c.id, c.name
            FROM categories c
            WHERE 1=1
        """
        params_names = []

        if search:
            query_names += " AND c.name LIKE %s"
            params_names.append(f"%{search}%")

        if is_active is not None:
            query_names += " AND c.is_active = %s"
            params_names.append(is_active)

        query_names += """
            AND EXISTS (
                SELECT 1 FROM products p
                WHERE p.category_id = c.id AND p.is_active = TRUE
            )
        """

        cursor.execute(query_names, params_names)

        category_names = [
            {"id": row['id'], "name": row['name']}
            for row in cursor.fetchall()
        ]

        if include_products:
            for category in categories:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM products WHERE category_id = %s AND is_active = TRUE",
                    (category["id"],)
                )
                category["product_count"] = cursor.fetchone()['count']
        
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "categories": categories,
            "category_names": category_names
        }
    }

# ============================================================
# 3. Get Products by Category (Public)
# ============================================================

@router.get("/{category_id}/products")
def get_category_products(
    category_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("newest", description="newest | price_low | price_high | popular")
):
    """Get all active products belonging to a specific category."""

    sort_map = {
        "price_low": "COALESCE(discount_price, price) ASC",
        "price_high": "COALESCE(discount_price, price) DESC",
        "popular": "rating DESC, review_count DESC",
    }
    order_by = sort_map.get(sort, "created_at DESC")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT name FROM categories WHERE id = %s AND is_active = TRUE",
            (category_id,)
        )
        category = cursor.fetchone()

        if not category:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الفئة غير موجودة"
            )

        cursor.execute(
            "SELECT COUNT(*) as count FROM products WHERE category_id = %s AND is_active = TRUE",
            (category_id,)
        )
        total = cursor.fetchone()['count']
        total_pages = math.ceil(total / page_size) if page_size else 1

        cursor.execute(f"""
            SELECT * FROM products
            WHERE category_id = %s AND is_active = TRUE
            ORDER BY {order_by}
            LIMIT %s OFFSET %s
        """, (category_id, page_size, (page - 1) * page_size))

        products = cursor.fetchall()
        
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {
            "category_name": category['name'],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "products": products
        }
    }


# ============================================================
# 4. Update Category (Admin only)
# ============================================================

@router.put("/{category_id}")
async def update_category(
    category_id: int,
    request: Request,
    name: Optional[str] = Form(None, min_length=1, max_length=100),
    is_active: Optional[bool] = Form(None),
    image: Optional[UploadFile] = File(None),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Update a category. Supports empty description properly.
    """

    form = await request.form()
    description = form.get("description")  

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # check exists
        cursor.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
        existing = cursor.fetchone()

        if not existing:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"الفئة برقم {category_id} غير موجودة"
            )

        # duplicate name check
        if name:
            cursor.execute(
                "SELECT id FROM categories WHERE name = %s AND id != %s",
                (name, category_id)
            )
            if cursor.fetchone():
                conn.commit()
                conn.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="اسم الفئة موجود بالفعل"
                )

        # image handling
        new_image_url: Optional[str] = None
        if image and image.filename:
            delete_image_from_cloudinary(existing.get("image_url"))
            new_image_url = upload_image_to_cloudinary(image)

        # build update
        update_fields = []
        params = []

        if name is not None:
            update_fields.append("name = %s")
            params.append(name)

        # 👇 الفرق الحقيقي هنا
        if "description" in form:
            update_fields.append("description = %s")
            params.append(description)  # ممكن تكون "" وده تمام

        if is_active is not None:
            update_fields.append("is_active = %s")
            params.append(is_active)

        if new_image_url is not None:
            update_fields.append("image_url = %s")
            params.append(new_image_url)

        if not update_fields:
            if not new_image_url:
                conn.commit()
                conn.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="لا توجد حقول للتحديث"
                )

        if update_fields:
            params.append(category_id)
            cursor.execute(
                f"UPDATE categories SET {', '.join(update_fields)} WHERE id = %s",
                params
            )

        # get updated
        cursor.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
        updated = cursor.fetchone()

        cursor.execute(
            "SELECT COUNT(*) as count FROM products WHERE category_id = %s",
            (category_id,)
        )
        updated["product_count"] = cursor.fetchone()['count']
        
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم تحديث الفئة بنجاح",
        "data": updated
    }

# ============================================================
# 5. Delete Category (Admin only)
# ============================================================

@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    force: bool = Query(False, description="حذف الفئة حتى لو كانت تحتوي على منتجات"),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Delete a category. If the category has a Cloudinary image,
    it will be deleted before removing the category record.
    """

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
        category = cursor.fetchone()

        if not category:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"الفئة برقم {category_id} غير موجودة"
            )

        cursor.execute(
            "SELECT COUNT(*) as count FROM products WHERE category_id = %s",
            (category_id,)
        )
        product_count = cursor.fetchone()['count']

        if product_count > 0 and not force:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": f"Category has {product_count} products",
                    "code": "CATEGORY_NOT_EMPTY"
                }
            )

        if force and product_count > 0:
            cursor.execute(
                "UPDATE products SET category_id = NULL WHERE category_id = %s",
                (category_id,)
            )

        # Delete image from Cloudinary (non-blocking)
        delete_image_from_cloudinary(category.get("image_url"))

        cursor.execute("DELETE FROM categories WHERE id = %s", (category_id,))
        conn.commit()
    finally:
        conn.close()

    return None


# ============================================================
# 6. Active Categories List (Public - for dropdowns)
# ============================================================

@router.get("/active/list")
def get_active_categories():
    """Get all active categories (lightweight, for dropdowns/filters)."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT c.id, c.name, c.image_url, COUNT(p.id) as product_count
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id AND p.is_active = TRUE
            WHERE c.is_active = TRUE
            GROUP BY c.id, c.name, c.image_url
            ORDER BY c.name
        """)

        categories = [
            {
                "id": row['id'],
                "name": row['name'],
                "image_url": row['image_url'],
                "product_count": row['product_count']
            }
            for row in cursor.fetchall()
        ]
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": categories
    }


# ============================================================
# 7. Toggle Category Status (Admin only)
# ============================================================

@router.patch("/{category_id}/toggle")
def toggle_category_status(
    category_id: int,
    admin_user: dict = Depends(get_admin_user)
):
    """Toggle a category's active/inactive status."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT is_active FROM categories WHERE id = %s", (category_id,))
        row = cursor.fetchone()

        if not row:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الفئة غير موجودة"
            )

        new_status = not bool(row['is_active'])

        cursor.execute(
            "UPDATE categories SET is_active = %s WHERE id = %s",
            (new_status, category_id)
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": f"تم {'تفعيل' if new_status else 'تعطيل'} الفئة بنجاح",
        "is_active": new_status
    }