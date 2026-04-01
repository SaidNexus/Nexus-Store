from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import secrets
from app.database_postgres import get_db_connection
from psycopg2.extras import RealDictCursor
from app.routes.auth import get_current_user

router = APIRouter(
    prefix="/orders",
    tags=["Orders"]
)


# ============================================
# Helper Function - التحقق من صلاحية الأدمن
# ============================================
def get_admin_user(current_user: dict = Depends(get_current_user)):
    """التحقق من أن المستخدم الحالي هو Admin"""
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ليس لديك صلاحية لتنفيذ هذا الإجراء"
        )
    return current_user


# ============================================
# Schemas
# ============================================

class OrderCreate(BaseModel):
    shipping_address: str = Field(..., min_length=10, description="عنوان الشحن")
    shipping_city: str = Field(..., description="المدينة")
    shipping_country: str = Field(..., description="الدولة")
    shipping_postal_code: Optional[str] = None
    phone: str = Field(..., description="رقم الهاتف")
    notes: Optional[str] = None
    payment_method: str = Field(default="cash_on_delivery", description="طريقة الدفع")


class OrderItemResponse(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int
    price: float
    total: float


class OrderResponse(BaseModel):
    id: int
    order_number: str
    status: str
    total_amount: float
    shipping_address: str
    shipping_city: str
    phone: str
    payment_method: str
    is_paid: bool
    created_at: str
    items: List[OrderItemResponse]


class OrderStatusUpdate(BaseModel):
    status: str = Field(..., description="الحالة الجديدة")


# ============================================
# Helper Functions
# ============================================

def generate_order_number():
    """توليد رقم طلب فريد"""
    timestamp = datetime.now().strftime("%Y%m%d")
    random_part = secrets.token_hex(4).upper()
    return f"ORD-{timestamp}-{random_part}"


def get_order_details(order_id: int, cursor):
    cursor.execute("""
        SELECT 
            o.*,
            u.full_name AS user_name,
            u.email     AS user_email
        FROM orders o
        JOIN users u ON o.user_id = u.id
        WHERE o.id = %s
    """, (order_id,))
    
    order = cursor.fetchone()
    if not order:
        return None

    cursor.execute("""
        SELECT 
            oi.id,
            oi.product_id,
            p.name      AS product_name,
            p.image_url AS product_image,
            oi.quantity,
            oi.price,
            oi.total
        FROM order_items oi
        JOIN products p ON oi.product_id = p.id
        WHERE oi.order_id = %s
    """, (order_id,))
    
    items = []
    for row in cursor.fetchall():
        items.append({
            "id": row['id'],
            "product_id": row['product_id'],
            "product_name": row['product_name'],
            "product_image": row['product_image'],
            "quantity": row['quantity'],
            "price": row['price'],
            "total": row['total'],
        })

    order["items"] = items
    return order


# ============================================
# 1. إنشاء طلب من السلة (CREATE)
# ============================================
@router.post("/", status_code=status.HTTP_201_CREATED)
def create_order(
    order_data: OrderCreate,
    current_user: dict = Depends(get_current_user)
):
    """إنشاء طلب جديد من محتويات السلة"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # الحصول على السلة
        cursor.execute("SELECT id FROM carts WHERE user_id = %s", (current_user["id"],))
        cart = cursor.fetchone()
        
        if not cart:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="السلة فارغة"
            )
        
        cart_id = cart['id']
        
        # جلب منتجات السلة
        cursor.execute("""
            SELECT 
                ci.product_id,
                ci.quantity,
                p.name,
                COALESCE(p.discount_price, p.price) as price,
                p.stock_quantity
            FROM cart_items ci
            JOIN products p ON ci.product_id = p.id
            WHERE ci.cart_id = %s AND p.is_active = TRUE
        """, (cart_id,))
        
        cart_items = cursor.fetchall()
        
        if not cart_items:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="السلة فارغة"
            )
        
        # التحقق من المخزون وحساب الإجمالي
        total_amount = 0
        order_items = []
        
        for item in cart_items:
            product_id = item['product_id']
            quantity = item['quantity']
            name = item['name']
            price = item['price']
            stock = item['stock_quantity']
            
            if stock < quantity:
                conn.commit()
                conn.close()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"المنتج '{name}' غير متوفر بالكمية المطلوبة. المتوفر: {stock}"
                )
            
            item_total = price * quantity
            total_amount += item_total
            
            order_items.append({
                "product_id": product_id,
                "quantity": quantity,
                "price": price,
                "total": item_total
            })
        
        # إنشاء الطلب
        order_number = generate_order_number()
        
        cursor.execute("""
            INSERT INTO orders (
                user_id, order_number, status, total_amount,
                shipping_address, shipping_city, shipping_country,
                shipping_postal_code, phone, notes, payment_method,
                is_paid, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
        """, (
            current_user["id"],
            order_number,
            "pending",
            total_amount,
            order_data.shipping_address,
            order_data.shipping_city,
            order_data.shipping_country,
            order_data.shipping_postal_code,
            order_data.phone,
            order_data.notes,
            order_data.payment_method,
            False  # is_paid
        ))
        
        order_id = cursor.fetchone()['id']
        
        # إضافة منتجات الطلب
        for item in order_items:
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, quantity, price, total)
                VALUES (%s, %s, %s, %s, %s)
            """, (order_id, item["product_id"], item["quantity"], item["price"], item["total"]))
            
            # تقليل المخزون
            cursor.execute(
                "UPDATE products SET stock_quantity = stock_quantity - %s WHERE id = %s",
                (item["quantity"], item["product_id"])
            )
        
        # تفريغ السلة
        cursor.execute("DELETE FROM cart_items WHERE cart_id = %s", (cart_id,))
        
        # جلب تفاصيل الطلب
        order = get_order_details(order_id, cursor)
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "message": "تم إنشاء الطلب بنجاح",
        "data": order
    }


# ============================================
# 2. عرض طلبات المستخدم (READ ALL - User)
# ============================================
@router.get("/my-orders")
def get_my_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
    search: Optional[str] = Query(None),

):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        base_query = """
            FROM orders o
            JOIN users u ON o.user_id = u.id
            WHERE o.user_id = %s
        """
        params = [current_user["id"]]

        if status:
            base_query += " AND o.status = %s"
            params.append(status)
        if search:
            base_query += " AND LOWER(o.order_number) LIKE %s"
            params.append(f"%{search.lower()}%")
        

        # count
        cursor.execute(f"SELECT COUNT(*) as count {base_query}", params)
        total = cursor.fetchone()['count']

        import math
        total_pages = math.ceil(total / page_size) if page_size else 1
        has_next = page < total_pages
        has_prev = page > 1

        # data
        data_query = f"""
            SELECT o.*, u.full_name as user_name
            {base_query}
            ORDER BY o.created_at DESC
            LIMIT %s OFFSET %s
        """
        params2 = params + [page_size, (page - 1) * page_size]
        cursor.execute(data_query, params2)
        orders = cursor.fetchall()

        for order in orders:
            cursor.execute("""
                SELECT oi.id, oi.product_id, p.name as product_name,
                       p.image_url, oi.quantity, oi.price, oi.total
                FROM order_items oi
                JOIN products p ON oi.product_id = p.id
                WHERE oi.order_id = %s
            """, (order["id"],))
            order["items"] = cursor.fetchall()

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
            "has_next": has_next,
            "has_prev": has_prev,
            "orders": orders
        }
    }

# ============================================
# 3. عرض طلب واحد (READ ONE)
# ============================================



# ============================================
# 4. عرض جميع الطلبات (READ ALL - Admin)
# ============================================
@router.get("/admin/all")
def get_all_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None), 
    admin_user: dict = Depends(get_admin_user)
):

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        base_query = """
            FROM orders o
            JOIN users u ON o.user_id = u.id
            WHERE 1=1
        """
        params = []

        if status:
            base_query += " AND o.status = %s"
            params.append(status)

        if user_id:
            base_query += " AND o.user_id = %s"
            params.append(user_id)

        if search:
            base_query += " AND (LOWER(o.order_number) LIKE %s OR LOWER(u.full_name) LIKE %s)"
            like_value = f"%{search.lower()}%"
            params.extend([like_value, like_value])


        # count
        cursor.execute(f"SELECT COUNT(*) as count {base_query}", params)
        total = cursor.fetchone()['count']

        import math
        total_pages = math.ceil(total / page_size) if page_size else 1
        has_next = page < total_pages
        has_prev = page > 1

        # data
        data_query = f"""
            SELECT o.*, u.full_name as user_name
            {base_query}
            ORDER BY o.created_at DESC
            LIMIT %s OFFSET %s
        """
        params2 = params + [page_size, (page - 1) * page_size]
        cursor.execute(data_query, params2)
        orders = cursor.fetchall()
        
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
            "has_next": has_next,
            "has_prev": has_prev,
            "orders": orders
        }
    }


# ============================================
# 5. تحديث حالة الطلب (UPDATE - Admin)
# ============================================
@router.patch("/{order_id}/status")
def update_order_status(
    order_id: int,
    status_update: OrderStatusUpdate,
    admin_user: dict = Depends(get_admin_user)
):
    """تحديث حالة الطلب - للأدمن فقط"""
    
    valid_statuses = ["pending", "processing", "shipped", "delivered", "cancelled"]
    
    if status_update.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"الحالة غير صحيحة. الحالات المتاحة: {', '.join(valid_statuses)}"
        )
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # التحقق من وجود الطلب
        cursor.execute("SELECT id, status FROM orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الطلب غير موجود"
            )
        
        # تحديث الحالة
        update_fields = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
        params = [status_update.status]
        
        # تحديث التواريخ حسب الحالة
        if status_update.status == "shipped":
            update_fields.append("shipped_at = CURRENT_TIMESTAMP")
        elif status_update.status == "delivered":
            update_fields.append("delivered_at = CURRENT_TIMESTAMP")
        
        query = f"UPDATE orders SET {', '.join(update_fields)} WHERE id = %s"
        params.append(order_id)
        
        cursor.execute(query, params)
        
        # جلب الطلب المحدث
        order = get_order_details(order_id, cursor)
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "message": "تم تحديث حالة الطلب بنجاح",
        "data": order
    }


# ============================================
# 6. إلغاء الطلب (UPDATE - User)
# ============================================
@router.post("/{order_id}/cancel")
def cancel_order(
    order_id: int,
    current_user: dict = Depends(get_current_user)
):
    """إلغاء الطلب - للمستخدم صاحب الطلب فقط"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # التحقق من وجود الطلب
        cursor.execute("SELECT user_id, status FROM orders WHERE id = %s", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الطلب غير موجود"
            )
        
        # التحقق من الصلاحية
        if order['user_id'] != current_user["id"]:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ليس لديك صلاحية لإلغاء هذا الطلب"
            )
        
        # التحقق من إمكانية الإلغاء
        if order['status'] not in ["pending", "processing"]:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="لا يمكن إلغاء الطلب في هذه المرحلة"
            )
        
        # إلغاء الطلب
        cursor.execute(
            "UPDATE orders SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (order_id,)
        )
        
        # إرجاع المنتجات للمخزون
        cursor.execute("""
            SELECT product_id, quantity FROM order_items WHERE order_id = %s
        """, (order_id,))
        
        for item in cursor.fetchall():
            cursor.execute(
                "UPDATE products SET stock_quantity = stock_quantity + %s WHERE id = %s",
                (item['quantity'], item['product_id'])
            )
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "message": "تم إلغاء الطلب بنجاح"
    }


# ============================================
# 7. حذف الطلب (DELETE - Admin فقط)
# ============================================
@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(
    order_id: int,
    admin_user: dict = Depends(get_admin_user)
):
    """حذف الطلب نهائياً - للأدمن فقط"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id FROM orders WHERE id = %s", (order_id,))
        if not cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الطلب غير موجود"
            )
        
        # حذف منتجات الطلب
        cursor.execute("DELETE FROM order_items WHERE order_id = %s", (order_id,))
        
        # حذف الطلب
        cursor.execute("DELETE FROM orders WHERE id = %s", (order_id,))
        conn.commit()
    finally:
        conn.close()
        
    return None


# ============================================
# 8. إحصائيات الطلبات (Admin)
# ============================================
@router.get("/admin/statistics")
def get_orders_statistics(admin_user: dict = Depends(get_admin_user)):
    """الحصول على إحصائيات الطلبات - للأدمن فقط"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # عدد الطلبات حسب الحالة
        cursor.execute("""
            SELECT status, COUNT(*) as count, SUM(total_amount) as total_amount
            FROM orders
            GROUP BY status
        """)
        
        status_stats = {}
        for row in cursor.fetchall():
            status_stats[row['status']] = {
                "count": row['count'],
                "total_amount": row['total_amount'] or 0
            }
        
        # إجمالي الطلبات والإيرادات
        cursor.execute("""
            SELECT 
                COUNT(*) as total_orders,
                SUM(total_amount) as total_revenue,
                AVG(total_amount) as average_order
            FROM orders
            WHERE status != 'cancelled'
        """)
        
        totals = cursor.fetchone()
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "data": {
            "status_breakdown": status_stats,
            "totals": totals
        }
    }
    
@router.get("/{order_id}")
def get_order(
    order_id: int,
    current_user: dict = Depends(get_current_user)
):
    """الحصول على تفاصيل طلب معين"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        order = get_order_details(order_id, cursor)
        
        if not order:
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="الطلب غير موجود"
            )
        
        # التحقق من الصلاحية (المستخدم صاحب الطلب أو الأدمن)
        if order["user_id"] != current_user["id"] and current_user["role"] != "admin":
            conn.commit()
            conn.close()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ليس لديك صلاحية لعرض هذا الطلب"
            )
        conn.commit()
    finally:
        conn.close()
        
    return {
        "status": "success",
        "data": order
    }