import traceback
print("🔥 Starting app...")

try:
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

    print("✅ Imports loaded successfully")

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

    print("✅ CORS configured")

    app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
    print("✅ Static files mounted")

    # تسجيل Routes
    app.include_router(auth.router)
    app.include_router(products.router)
    app.include_router(reviews.router) 
    app.include_router(categories.router)
    app.include_router(cart.router)
    app.include_router(orders.router)
    app.include_router(upload.router)

    print("✅ Routers loaded")

except Exception:
    print("💣 CRASH DURING STARTUP")
    traceback.print_exc()
    raise


# ============================================
# الصفحة الرئيسية
# ============================================
@app.get("/", tags=["Home"])
def read_root():
    return {
        "message": "E-Commerce API running 🚀"
    }


# ============================================
# Health Check
# ============================================
@app.get("/health", tags=["Home"])
def health_check():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============================================
# Exception Handler
# ============================================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"message": "Validation error"}
    )
