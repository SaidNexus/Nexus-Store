from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles 
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.routes import reviews
from app.database_postgres import get_db_connection
from app.routes import (
    auth,
    products,
    categories,
    cart,
    orders,
    upload 
)

from app.core.security_helpers import get_cors_settings
from psycopg2.extras import RealDictCursor

# إنشاء التطبيق
app = FastAPI()

# إعداد CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")



@app.on_event("startup")
def startup_event():
    """تنفذ عند بدء تشغيل التطبيق"""

# تسجيل Routes
app.include_router(auth.router)
app.include_router(products.router)
app.include_router(reviews.router) 
app.include_router(categories.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(upload.router)


# ============================================
# الصفحة الرئيسية
# ============================================
@app.get("/", tags=["Home"])
def read_root():
    """الصفحة الرئيسية للـ API"""
    return {
        "message": "مرحباً بك في E-Commerce API 🛒 (PostgreSQL)",
        "version": "2.1.0",
        "status": "running",
        "features": {
            "database": "PostgreSQL (Neon)",
            "authentication": "JWT-based auth with role-based access control",
            "products": "Full CRUD operations with search and filtering",
            "categories": "Category management with product grouping",
            "cart": "Shopping cart management",
            "orders": "Order processing and tracking",
            "upload": "Image upload with drag & drop support"
        },
        "endpoints": {
            "docs": "/docs",
            "redoc": "/redoc",
            "health": "/health",
            "stats": "/stats"
        }
    }


# ============================================
# Health Check
# ============================================
@app.get("/health", tags=["Home"])
def health_check():
    """فحص صحة التطبيق"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
            db_status = "connected"
        finally:
            conn.close()
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "database": db_status,
        "version": "2.1.0"
    }


# ============================================
# إحصائيات عامة
# ============================================
@app.get("/stats", tags=["Home"])
def get_statistics():
    """الحصول على إحصائيات عامة عن النظام"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # عدد المستخدمين
            cursor.execute("SELECT COUNT(*) as count FROM users")
            total_users = cursor.fetchone()['count']
            
            # عدد المنتجات
            cursor.execute("SELECT COUNT(*) as count FROM products WHERE is_active = TRUE")
            active_products = cursor.fetchone()['count']
            
            cursor.execute("SELECT COUNT(*) as count FROM products")
            total_products = cursor.fetchone()['count']
            
            # عدد الفئات
            cursor.execute("SELECT COUNT(*) as count FROM categories WHERE is_active = TRUE")
            active_categories = cursor.fetchone()['count']
            
            # عدد الطلبات
            cursor.execute("SELECT COUNT(*) as count FROM orders")
            total_orders = cursor.fetchone()['count']
            
            cursor.execute("SELECT COUNT(*) as count FROM orders WHERE status = 'pending'")
            pending_orders = cursor.fetchone()['count']
            
            # إجمالي المبيعات
            cursor.execute("""
                SELECT SUM(total_amount) as total_revenue
                FROM orders 
                WHERE status != 'cancelled'
            """)
            result = cursor.fetchone()
            total_revenue = result['total_revenue'] if result['total_revenue'] is not None else 0
            
            return {
                "status": "success",
                "data": {
                    "users": {
                        "total": total_users
                    },
                    "products": {
                        "total": total_products,
                        "active": active_products
                    },
                    "categories": {
                        "active": active_categories
                    },
                    "orders": {
                        "total": total_orders,
                        "pending": pending_orders
                    },
                    "revenue": {
                        "total": round(float(total_revenue), 2)
                    }
                }
            }
        finally:
            conn.close()
    except Exception as e:
        return {
            "status": "error",
            "message": f"خطأ في جلب الإحصائيات: {str(e)}"
        }


# ============================================
# معلومات عن الـ API
# ============================================
@app.get("/api-info", tags=["Home"])
def api_information():
    """معلومات تفصيلية عن الـ API"""
    return {
        "name": "E-Commerce API",
        "version": "2.0.0",
        "description": "نظام متكامل لإدارة التجارة الإلكترونية",
        "author": "Your Name",
        "endpoints": {
            "authentication": {
                "register": "POST /auth/register",
                "login": "POST /auth/login",
                "profile": "GET /auth/me",
                "update_profile": "PUT /auth/me",
                "change_password": "POST /auth/change-password"
            },
            "products": {
                "list": "GET /products/",
                "get_one": "GET /products/{id}",
                "create": "POST /products/ (Admin)",
                "update": "PUT /products/{id} (Admin)",
                "delete": "DELETE /products/{id} (Admin)",
                "featured": "GET /products/featured/list"
            },
            "categories": {
                "list": "GET /categories/",
                "get_one": "GET /categories/{id}",
                "create": "POST /categories/ (Admin)",
                "update": "PUT /categories/{id} (Admin)",
                "delete": "DELETE /categories/{id} (Admin)",
                "products": "GET /categories/{id}/products"
            },
            "cart": {
                "view": "GET /cart/",
                "add_item": "POST /cart/items",
                "update_item": "PUT /cart/items/{id}",
                "remove_item": "DELETE /cart/items/{id}",
                "clear": "DELETE /cart/clear",
                "count": "GET /cart/count"
            },
            "orders": {
                "create": "POST /orders/",
                "my_orders": "GET /orders/my-orders",
                "get_one": "GET /orders/{id}",
                "cancel": "POST /orders/{id}/cancel",
                "admin_all": "GET /orders/admin/all (Admin)",
                "update_status": "PATCH /orders/{id}/status (Admin)",
                "statistics": "GET /orders/admin/statistics (Admin)"
            },
            "upload": {  # ✅ إضافة
                "product_image": "POST /upload/product-image (Admin)",
                "product_images": "POST /upload/product-images (Admin)",
                "category_image": "POST /upload/category-image (Admin)",
                "user_avatar": "POST /upload/user-avatar",
                "delete_image": "DELETE /upload/image (Admin)",
                "serve_image": "GET /upload/serve/{folder}/{filename}"
            }
        },
        "authentication": {
            "type": "JWT Bearer Token",
            "header": "Authorization: Bearer YOUR_TOKEN_HERE",
            "token_expiry": "30 minutes"
        },
        "roles": {
            "customer": "عميل عادي - يمكنه التسوق وإدارة طلباته",
            "admin": "مدير النظام - صلاحيات كاملة"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    errors = exc.errors()

    for err in errors:
        field = err.get("loc")[-1]

        if field == "email":
            return JSONResponse(
                status_code=400,
                content={
                  "detail": {
                    "type": "invalid_email",
                    "message": "Please enter a valid email address"
                  }
                }
            )

        if field == "username":
            return JSONResponse(
                status_code=400,
                content={
                    "type": "invalid_username",
                    "message": "Invalid username"
                },
            )

    return JSONResponse(
        status_code=400,
        content={
            "type": "validation_error",
            "message": "Invalid input"
        },
    )