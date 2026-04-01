from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form
from typing import Optional, List
import cloudinary
import cloudinary.uploader
import cloudinary.api
import json
from app.database_postgres import get_db_connection
from psycopg2.extras import RealDictCursor
from app.routes.auth import get_current_user

router = APIRouter(
    prefix="/upload",
    tags=["File Upload"]
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

# ============================================================
# Constants
# ============================================================
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_GALLERY_IMAGES = 5

CLOUDINARY_FOLDERS = {
    "products": "products",
    "categories": "categories",
    "users": "users",
}


# ============================================================
# Helper Functions
# ============================================================

import logging

logger = logging.getLogger(__name__)

def get_admin_user(current_user: dict = Depends(get_current_user)):
    """Verify current user has Admin role."""
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ليس لديك صلاحية لتنفيذ هذا الإجراء"
        )
    return current_user


def parse_images_field(product: dict) -> dict:
    """
    Parse the `images` field from a JSON string (or legacy comma-separated string)
    into a Python list. Always ensures `images` is a list in the response.
    """
    if product.get("images"):
        try:
            val = product["images"]
            if isinstance(val, list):
                # Already a list — nothing to do
                product["images"] = val
            else:
                product["images"] = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            # FIX: Backward-compatible fallback for old comma-separated strings
            product["images"] = [
                img.strip() for img in product["images"].split(",") if img.strip()
            ]
    else:
        # FIX: Always return a list, never None or missing key
        product["images"] = []
    return product


def get_product_or_404(cursor, product_id: int) -> dict:
    """Fetch a product row or raise 404. Returns dict result."""
    cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"المنتج برقم {product_id} غير موجود"
        )
    return dict(row)


def parse_existing_images(raw: Optional[str]) -> List[str]:
    """
    Safely parse a raw images DB value (JSON string or legacy comma-separated)
    into a Python list. Returns empty list if None or invalid.
    """
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        # FIX: Backward-compatible fallback
        return [img.strip() for img in raw.split(",") if img.strip()]


def validate_image(file: UploadFile) -> None:
    """
    Validate file type by extension.
    Raises HTTPException if the file type is not allowed.
    """
    from pathlib import Path
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"نوع الملف غير مدعوم. الأنواع المسموحة: {', '.join(ALLOWED_EXTENSIONS)}"
        )


def validate_file_size(file: UploadFile) -> bytes:
    """
    Read file content and validate it does not exceed MAX_FILE_SIZE_BYTES.
    Returns the raw bytes so we don't re-read the stream.
    """
    file.file.seek(0)
    contents = file.file.read()
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"حجم الملف يتجاوز الحد المسموح ({MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB)"
        )
    return contents


def extract_public_id(image_url: str) -> Optional[str]:
    """
    Extract Cloudinary public_id from a secure_url.

    Example:
        Input:  https://res.cloudinary.com/<cloud>/image/upload/v123456/products/abc.jpg
        Output: products/abc

    Returns None if parsing fails.
    """
    if not image_url:
        return None
    try:
        after_upload = image_url.split("/upload/")[1]
        parts = after_upload.split("/")
        # Strip version segment (e.g. "v1234567890")
        if parts[0].startswith("v") and parts[0][1:].isdigit():
            parts = parts[1:]
        public_id_with_ext = "/".join(parts)
        public_id = public_id_with_ext.rsplit(".", 1)[0]
        return public_id
    except (IndexError, AttributeError):
        return None


def delete_from_cloudinary(image_url: str) -> bool:
    """
    Delete an image from Cloudinary by its URL.
    Returns True on success, False on any failure.
    Does NOT raise exceptions — safe to call in cleanup paths.
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


def upload_to_cloudinary(contents: bytes, folder: str, filename: str) -> str:
    """
    Upload raw image bytes to Cloudinary under the given folder.
    Returns the secure_url string.
    Raises HTTPException(502) if the upload fails.
    """
    try:
        result = cloudinary.uploader.upload(
            contents,
            folder=folder,
            resource_type="image",
            use_filename=False,
            unique_filename=True,
        )
        return result["secure_url"]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"فشل رفع الصورة إلى Cloudinary: {str(e)}"
        )


def process_and_upload(file: UploadFile, folder: str) -> str:
    """
    Full pipeline: validate → read → size-check → upload to Cloudinary.
    Returns the secure_url.
    """
    validate_image(file)
    contents = validate_file_size(file)
    return upload_to_cloudinary(contents, folder, file.filename)


# ============================================================
# 1. Upload / Replace Product Main Image (Admin only)
# ============================================================

@router.post("/product/{product_id}/image", status_code=status.HTTP_200_OK)
async def upload_product_main_image(
    product_id: int,
    file: UploadFile = File(...),
    admin_user: dict = Depends(get_admin_user)
):
    """Upload or replace the main image of a product."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id, image_url FROM products WHERE id = %s", (product_id,))
        product = cursor.fetchone()
        if not product:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )
        old_image_url = product['image_url']
        
        # Upload new image first — before touching the DB or deleting the old one
        new_image_url = process_and_upload(file, CLOUDINARY_FOLDERS["products"])

        # Delete old image from Cloudinary (non-blocking)
        delete_from_cloudinary(old_image_url)

        cursor.execute(
            "UPDATE products SET image_url = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (new_image_url, product_id)
        )
        # FIX: Fetch updated row after UPDATE, not old row
        updated_product = get_product_or_404(cursor, product_id)
        updated_product = parse_images_field(updated_product)
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم رفع الصورة الرئيسية بنجاح",
        "data": {
            "image_url": new_image_url,
            "product": updated_product
        }
    }


# ============================================================
# 2. Delete Product Main Image (Admin only)
# ============================================================

@router.delete("/product/{product_id}/image", status_code=status.HTTP_200_OK)
async def delete_product_main_image(
    product_id: int,
    admin_user: dict = Depends(get_admin_user)
):
    """Delete the main image of a product."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT image_url FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()

        if not row:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )
        if not row['image_url']:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="لا توجد صورة رئيسية لهذا المنتج"
            )

        image_url = row['image_url']
        delete_from_cloudinary(image_url)

        cursor.execute(
            "UPDATE products SET image_url = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (product_id,)
        )
        # FIX: Fetch updated row after UPDATE, not old row
        updated_product = get_product_or_404(cursor, product_id)
        updated_product = parse_images_field(updated_product)
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم حذف الصورة الرئيسية بنجاح",
        "data": {"product": updated_product}
    }


# ============================================================
# 3. Upload Product Gallery Images (Admin only)
# ============================================================

@router.post("/product/{product_id}/images", status_code=status.HTTP_200_OK)
async def upload_product_gallery_images(
    product_id: int,
    files: List[UploadFile] = File(...),
    admin_user: dict = Depends(get_admin_user)
):
    """Upload multiple images to a product's gallery (max 5 total)."""

    if len(files) > MAX_GALLERY_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"لا يمكن رفع أكثر من {MAX_GALLERY_IMAGES} صور في المرة الواحدة"
        )

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT images FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()
        if not row:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )
        # FIX: Use shared helper for consistent parsing
        existing_images = parse_existing_images(row['images'])

        # Pre-validate capacity before any uploads
        if len(existing_images) + len(files) > MAX_GALLERY_IMAGES:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"الحد الأقصى {MAX_GALLERY_IMAGES} صور. "
                    f"لديك حالياً {len(existing_images)} صور، "
                    f"ولا يمكن إضافة {len(files)} أخرى"
                )
            )

        # Upload all new images
        uploaded_urls: List[str] = []
        for file in files:
            url = process_and_upload(file, CLOUDINARY_FOLDERS["products"])
            uploaded_urls.append(url)

        all_images = existing_images + uploaded_urls
        images_json = json.dumps(all_images)

        cursor.execute(
            "UPDATE products SET images = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (images_json, product_id)
        )
        # FIX: Fetch updated row after UPDATE (was wrongly using old `row`)
        updated_product = get_product_or_404(cursor, product_id)
        updated_product = parse_images_field(updated_product)
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": f"تم رفع {len(uploaded_urls)} صورة بنجاح",
        "data": {
            # FIX: Return newly uploaded URLs separately + full product
            "uploaded_images": uploaded_urls,
            "product": updated_product
        }
    }


# ============================================================
# 4. Delete a Product Gallery Image (Admin only)
# ============================================================

@router.delete("/product/{product_id}/images", status_code=status.HTTP_200_OK)
async def delete_product_gallery_image(
    product_id: int,
    image_url: str = Form(...),
    admin_user: dict = Depends(get_admin_user)
):
    """Delete a specific image from a product's gallery."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT images FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()

        if not row:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"المنتج برقم {product_id} غير موجود"
            )
        if not row['images']:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="لا توجد صور في الجالاري"
            )

        # FIX: Use shared helper for consistent parsing
        images = parse_existing_images(row['images'])

        if image_url not in images:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الصورة غير موجودة في الجالاري"
            )

        delete_from_cloudinary(image_url)
        images.remove(image_url)

        new_images_json = json.dumps(images) if images else None
        cursor.execute(
            "UPDATE products SET images = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (new_images_json, product_id)
        )
        # FIX: Fetch updated row after UPDATE (was wrongly using old `row`)
        updated_product = get_product_or_404(cursor, product_id)
        updated_product = parse_images_field(updated_product)
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم حذف الصورة من الجالاري بنجاح",
        "data": {"product": updated_product}
    }


# ============================================================
# 5. Upload / Replace Category Image (Admin only)
# ============================================================

@router.post("/category/{category_id}/image", status_code=status.HTTP_200_OK)
async def upload_category_image(
    category_id: int,
    file: UploadFile = File(...),
    admin_user: dict = Depends(get_admin_user)
):
    """Upload or replace a category's image."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id, image_url FROM categories WHERE id = %s", (category_id,))
        category = cursor.fetchone()
        if not category:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"الفئة برقم {category_id} غير موجودة"
            )
        old_image_url = category['image_url']

        new_image_url = process_and_upload(file, CLOUDINARY_FOLDERS["categories"])
        delete_from_cloudinary(old_image_url)

        cursor.execute(
            "UPDATE categories SET image_url = %s WHERE id = %s",
            (new_image_url, category_id)
        )
        cursor.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
        updated_category = dict(cursor.fetchone())
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم رفع صورة الفئة بنجاح",
        "data": {
            "image_url": new_image_url,
            "category": updated_category
        }
    }


# ============================================================
# 6. Upload / Replace User Avatar (Authenticated user)
# ============================================================

@router.post("/user-avatar", status_code=status.HTTP_201_CREATED)
async def upload_user_avatar(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload or replace the authenticated user's avatar."""

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT avatar FROM users WHERE id = %s", (current_user["id"],))
        row = cursor.fetchone()
        old_avatar_url = row['avatar'] if row else None

        new_avatar_url = process_and_upload(file, CLOUDINARY_FOLDERS["users"])
        delete_from_cloudinary(old_avatar_url)

        cursor.execute(
            "UPDATE users SET avatar = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (new_avatar_url, current_user["id"])
        )
        cursor.execute("SELECT * FROM users WHERE id = %s", (current_user["id"],))
        updated_user = dict(cursor.fetchone())
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم تحديث الصورة الشخصية بنجاح",
        "data": {
            "url": new_avatar_url,
            "user": updated_user
        }
    }


# ============================================================
# 7. Delete Image by URL (Admin only)
# ============================================================

@router.delete("/image", status_code=status.HTTP_200_OK)
async def delete_image(
    image_url: str = Form(...),
    admin_user: dict = Depends(get_admin_user)
):
    """Delete any Cloudinary image by its secure_url."""

    public_id = extract_public_id(image_url)
    if not public_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="رابط الصورة غير صحيح أو لا يمكن استخراج المعرف منه"
        )

    try:
        result = cloudinary.uploader.destroy(public_id)
        if result.get("result") != "ok":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الصورة غير موجودة أو تعذر حذفها"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"خطأ في الاتصال بـ Cloudinary: {str(e)}"
        )

    return {
        "status": "success",
        "message": "تم حذف الصورة بنجاح"
    }


# ============================================================
# 8. Get Image Info via Cloudinary API (Authenticated)
# ============================================================

@router.get("/image-info", status_code=status.HTTP_200_OK)
async def get_image_info(
    image_url: str,
    current_user: dict = Depends(get_current_user)
):
    """Retrieve image metadata from Cloudinary using its secure_url."""

    public_id = extract_public_id(image_url)
    if not public_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="رابط الصورة غير صحيح"
        )

    try:
        info = cloudinary.api.resource(public_id)
    except cloudinary.exceptions.NotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="الصورة غير موجودة في Cloudinary"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"خطأ في الاتصال بـ Cloudinary: {str(e)}"
        )

    size_bytes = info.get("bytes", 0)

    return {
        "status": "success",
        "data": {
            "filename": info.get("public_id", "").split("/")[-1],
            "url": info.get("secure_url", image_url),
            "size": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "width": info.get("width"),
            "height": info.get("height"),
            "format": info.get("format"),
            "created_at": info.get("created_at")
        }
    }
@router.put("/product/{product_id}/images")
async def update_product_gallery(
    product_id: int,
    existing_images: str = Form("[]"),  # JSON string
    files: List[UploadFile] = File([]),
    admin_user: dict = Depends(get_admin_user)
):
    existing_images_from_request = json.loads(existing_images)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT images FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()

        if not row:
            conn.commit()
            conn.close()
            raise HTTPException(status_code=404, detail="Product not found")

        existing_images_db = parse_existing_images(row['images'])

        # 🔥 images to delete
        images_to_delete = [
            img for img in existing_images_db if img not in existing_images_from_request
        ]

        for img in images_to_delete:
            delete_from_cloudinary(img)

        # 🔥 upload new images
        uploaded_urls = []
        for file in files:
            url = process_and_upload(file, CLOUDINARY_FOLDERS["products"])
            uploaded_urls.append(url)

        # 🔥 final result
        final_images = existing_images_from_request + uploaded_urls

        cursor.execute(
            "UPDATE products SET images = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (json.dumps(final_images), product_id)
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {
            "images": final_images
        }
    }