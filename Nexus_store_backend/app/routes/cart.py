from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from app.database_postgres import get_db_connection
from psycopg2.extras import RealDictCursor
from app.routes.auth import get_current_user

router = APIRouter(
    prefix="/cart",
    tags=["Cart"]
)


# ============================================
# Schemas
# ============================================

class CartItemCreate(BaseModel):
    product_id: int = Field(..., description="معرف المنتج")
    quantity: int = Field(default=1, ge=1, description="الكمية")


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1, description="الكمية الجديدة")


class CartItemResponse(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_price: float
    product_image: Optional[str]
    quantity: int
    subtotal: float
    stock_quantity: int

class CartResponse(BaseModel):
    items: List[CartItemResponse]
    total_items: int
    total_price: float


# ============================================
# Helper Functions
# ============================================

def get_or_create_cart(user_id: int, cursor):
    """الحصول على السلة أو إنشاؤها إذا لم تكن موجودة"""
    cursor.execute("SELECT id FROM carts WHERE user_id = %s", (user_id,))
    cart = cursor.fetchone()
    
    if not cart:
        cursor.execute(
            "INSERT INTO carts (user_id, created_at, updated_at) VALUES (%s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id",
            (user_id,)
        )
        cart_id = cursor.fetchone()['id']
    else:
        cart_id = cart['id']
    
    return cart_id


def get_cart_details(cart_id: int, cursor):
    """الحصول على تفاصيل السلة"""

    cursor.execute("""
        SELECT 
            ci.id,
            ci.product_id,
            p.name AS product_name,
            COALESCE(p.discount_price, p.price) AS product_price,
            p.image_url AS product_image,
            ci.quantity,
            (COALESCE(p.discount_price, p.price) * ci.quantity) AS subtotal,
            p.stock_quantity
        FROM cart_items ci
        JOIN products p ON ci.product_id = p.id
        WHERE ci.cart_id = %s
          AND p.is_active = TRUE
        ORDER BY ci.added_at DESC
    """, (cart_id,))

    items = []
    total_price = 0.0

    for row in cursor.fetchall():
        item = {
            "id": row['id'],
            "product_id": row['product_id'],
            "product_name": row['product_name'],
            "product_price": row['product_price'],
            "product_image": row['product_image'],
            "quantity": row['quantity'],
            "subtotal": round(row['subtotal'], 2),
            "stock_quantity": row['stock_quantity']
        }
        items.append(item)
        total_price += row['subtotal']

    return {
        "items": items,
        "total_items": len(items),
        "total_price": round(total_price, 2)
    }


# ============================================
# 1. عرض السلة (READ)
# ============================================
@router.get("/", response_model=CartResponse)
def get_cart(current_user: dict = Depends(get_current_user)):
    """عرض محتويات السلة للمستخدم الحالي"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cart_id = get_or_create_cart(current_user["id"], cursor)
        cart_details = get_cart_details(cart_id, cursor)
        conn.commit()
    finally:
        conn.close()
        
    return cart_details


# ============================================
# 2. إضافة منتج للسلة (CREATE)
# ============================================
@router.post("/items", status_code=status.HTTP_201_CREATED)
def add_to_cart(
    item: CartItemCreate,
    current_user: dict = Depends(get_current_user)
):
    """إضافة منتج للسلة"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # التحقق من وجود المنتج وتوفره
        cursor.execute(
            "SELECT id, name, stock_quantity, is_active FROM products WHERE id = %s",
            (item.product_id,)
        )
        product = cursor.fetchone()
        
        if not product:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="المنتج غير موجود"
            )
        
        if not product['is_active']:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="المنتج غير متاح حالياً"
            )
        
        if product['stock_quantity'] < item.quantity:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"الكمية المتوفرة: {product['stock_quantity']} فقط"
            )
        
        # الحصول على السلة أو إنشاؤها
        cart_id = get_or_create_cart(current_user["id"], cursor)
        
        # التحقق من وجود المنتج في السلة
        cursor.execute(
            "SELECT id, quantity FROM cart_items WHERE cart_id = %s AND product_id = %s",
            (cart_id, item.product_id)
        )
        existing_item = cursor.fetchone()
        
        if existing_item:
            # تحديث الكمية
            new_quantity = existing_item['quantity'] + item.quantity
            
            if product['stock_quantity'] < new_quantity:
                conn.commit()
                conn.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"الكمية المتوفرة: {product['stock_quantity']} فقط"
                )
            
            cursor.execute(
                "UPDATE cart_items SET quantity = %s WHERE id = %s",
                (new_quantity, existing_item['id'])
            )
            message = "تم تحديث الكمية في السلة"
        else:
            # إضافة المنتج للسلة
            cursor.execute(
                "INSERT INTO cart_items (cart_id, product_id, quantity, added_at) VALUES (%s, %s, %s, CURRENT_TIMESTAMP)",
                (cart_id, item.product_id, item.quantity)
            )
            message = "تم إضافة المنتج للسلة"
        
        # تحديث وقت السلة
        cursor.execute(
            "UPDATE carts SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (cart_id,)
        )
        
        # جلب تفاصيل السلة المحدثة
        cart_details = get_cart_details(cart_id, cursor)
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "message": message,
        "cart": cart_details
    }


# ============================================
# 3. تحديث كمية منتج في السلة (UPDATE)
# ============================================
@router.put("/items/{item_id}")
def update_cart_item(
    item_id: int,
    item_update: CartItemUpdate,
    current_user: dict = Depends(get_current_user)
):
    """تحديث كمية منتج في السلة"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # التحقق من أن العنصر يخص المستخدم الحالي
        cursor.execute("""
            SELECT ci.id, ci.product_id, p.stock_quantity, c.user_id
            FROM cart_items ci
            JOIN carts c ON ci.cart_id = c.id
            JOIN products p ON ci.product_id = p.id
            WHERE ci.id = %s
        """, (item_id,))
        
        item = cursor.fetchone()
        
        if not item:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="العنصر غير موجود في السلة"
            )
        
        if item['user_id'] != current_user["id"]:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ليس لديك صلاحية لتعديل هذا العنصر"
            )
        
        # التحقق من توفر الكمية
        if item['stock_quantity'] < item_update.quantity:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"الكمية المتوفرة: {item['stock_quantity']} فقط"
            )
        
        # تحديث الكمية
        cursor.execute(
            "UPDATE cart_items SET quantity = %s WHERE id = %s",
            (item_update.quantity, item_id)
        )
        
        # جلب cart_id
        cursor.execute("SELECT cart_id FROM cart_items WHERE id = %s", (item_id,))
        cart_id = cursor.fetchone()['cart_id']
        
        # تحديث وقت السلة
        cursor.execute(
            "UPDATE carts SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (cart_id,)
        )
        
        cart_details = get_cart_details(cart_id, cursor)
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "message": "تم تحديث الكمية بنجاح",
        "cart": cart_details
    }


# ============================================
# 4. حذف منتج من السلة (DELETE)
# ============================================
@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_cart(
    item_id: int,
    current_user: dict = Depends(get_current_user)
):
    """حذف منتج من السلة"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # التحقق من أن العنصر يخص المستخدم الحالي
        cursor.execute("""
            SELECT ci.cart_id, c.user_id
            FROM cart_items ci
            JOIN carts c ON ci.cart_id = c.id
            WHERE ci.id = %s
        """, (item_id,))
        
        item = cursor.fetchone()
        
        if not item:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="العنصر غير موجود في السلة"
            )
        
        if item['user_id'] != current_user["id"]:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ليس لديك صلاحية لحذف هذا العنصر"
            )
        
        # حذف العنصر
        cursor.execute("DELETE FROM cart_items WHERE id = %s", (item_id,))
        
        # تحديث وقت السلة
        cursor.execute(
            "UPDATE carts SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (item['cart_id'],)
        )
        conn.commit()
    finally:
        conn.close()
        
    return None


# ============================================
# 5. تفريغ السلة (DELETE ALL)
# ============================================
@router.delete("/clear", status_code=status.HTTP_204_NO_CONTENT)
def clear_cart(current_user: dict = Depends(get_current_user)):
    """تفريغ جميع محتويات السلة"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # الحصول على cart_id
        cursor.execute(
            "SELECT id FROM carts WHERE user_id = %s",
            (current_user["id"],)
        )
        cart = cursor.fetchone()
        
        if cart:
            cart_id = cart['id']
            cursor.execute("DELETE FROM cart_items WHERE cart_id = %s", (cart_id,))
            cursor.execute(
                "UPDATE carts SET updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (cart_id,)
            )
        conn.commit()
    finally:
        conn.close()
        
    return None


# ============================================
# 6. عدد المنتجات في السلة
# ============================================
@router.get("/count")
def get_cart_count(current_user: dict = Depends(get_current_user)):
    """الحصول على عدد المنتجات في السلة"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT COUNT(ci.id) as count, COALESCE(SUM(ci.quantity), 0) as sm
            FROM carts c
            LEFT JOIN cart_items ci ON c.id = ci.cart_id
            WHERE c.user_id = %s
        """, (current_user["id"],))
        
        result = cursor.fetchone()
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "data": {
            "unique_items": result['count'],
            "total_quantity": result['sm']
        }
    }