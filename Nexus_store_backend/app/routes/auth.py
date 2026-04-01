from fastapi import APIRouter, HTTPException, status, Depends, Form, Response, Request
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime, timedelta, timezone
import jwt
import secrets 
import math
import hashlib
from passlib.context import CryptContext
from app.database_postgres import get_db_connection
from app.core.security_helpers import get_refresh_token_cookie_params
from psycopg2.extras import RealDictCursor
from psycopg2 import IntegrityError

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


# ============================================
# Security Schemes
# ============================================
# Define oauth2_scheme locally for auth routes to prevent circular imports
# and NameError exceptions. tokenUrl="login" points to the /auth/login route.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

from app.schemas.auth_schemas import UserResponse, UserRegister, UserLogin, Token, UserUpdate, ChangePassword
from app.core.security import SECRET_KEY, ALGORITHM, pwd_context, ACCESS_TOKEN_EXPIRE_MINUTES

REFRESH_TOKEN_EXPIRE_DAYS = 7

# ============================================
# Helper Functions
# ============================================
def _build_pagination(total: int, page: int, page_size: int) -> dict:
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """التحقق من كلمة المرور"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """تشفير كلمة المرور"""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """إنشاء JWT Token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# ============================================
# ✅ NEW - Refresh Token Helper Functions
# ============================================
def generate_refresh_token() -> str:
    """Generate a secure random refresh token"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a token for secure storage"""
    return hashlib.sha256(token.encode()).hexdigest()


def create_refresh_token(user_id: int) -> str:
    """Create and store a refresh token in the database"""
    refresh_token = generate_refresh_token()
    token_hash = hash_token(refresh_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            INSERT INTO refresh_tokens 
            (user_id, token_hash, expires_at, is_revoked, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            user_id, 
            token_hash, 
            expires_at.isoformat(), 
            False, 
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()
    finally:
        conn.close()
    
    return refresh_token


def verify_refresh_token(refresh_token: str) -> Optional[dict]:
    """Verify refresh token and return user if valid"""
    token_hash = hash_token(refresh_token)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT rt.*, u.id as user_id, u.username, u.role, u.is_active
            FROM refresh_tokens rt
            JOIN users u ON rt.user_id = u.id
            WHERE rt.token_hash = %s
        """, (token_hash,))
        
        result = cursor.fetchone()
        conn.commit()
    finally:
        conn.close()
        
    if not result:
        return None
        
        # Check if token is revoked
        if result["is_revoked"]:
            return None
        
        # Check if token is expired (Postgres TIMESTAMP is already datetime)
        expires_at = result["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        
        # Ensure timezone-aware comparison
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
            
        now = datetime.now(timezone.utc)
        
        if now > expires_at:
            return None
        
        # Check if user is active
        if not result["is_active"]:
            return None
        
        return result


def revoke_refresh_token(refresh_token: str):
    """Revoke a refresh token"""
    token_hash = hash_token(refresh_token)
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            UPDATE refresh_tokens 
            SET is_revoked = TRUE 
            WHERE token_hash = %s
        """, (token_hash,))
        conn.commit()
    finally:
        conn.close()


def revoke_all_user_tokens(user_id: int):
    """Revoke all refresh tokens for a user"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            UPDATE refresh_tokens 
            SET is_revoked = TRUE 
            WHERE user_id = %s AND is_revoked = FALSE
        """, (user_id,))
        conn.commit()
    finally:
        conn.close()


def decode_token(token: str):
    """فك تشفير الـ Token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="انتهت صلاحية الـ Token"
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token غير صالح"
        )


def get_current_user(token: str = Depends(oauth2_scheme)):
    """الحصول على المستخدم الحالي من الـ Token"""
    payload = decode_token(token)
    user_id = payload.get("sub")
    
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="بيانات المصادقة غير صحيحة"
        )
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
        user = cursor.fetchone()
        conn.commit()
    finally:
        conn.close()
        
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="المستخدم غير موجود"
        )
    
    return user

def get_optional_user(token: str = Depends(oauth2_scheme_optional)) -> Optional[dict]:
    if not token:
        return None
    try:
        return get_current_user(token)
    except:
        return None
    
def get_admin_user(current_user: dict = Depends(get_current_user)):
    """التحقق من أن المستخدم الحالي هو Admin"""
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ليس لديك صلاحية لتنفيذ هذا الإجراء. يجب أن تكون Admin"
        )
    return current_user


# ============================================
# 1. تسجيل مستخدم جديد (Register)
# ============================================
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserRegister):
    """تسجيل مستخدم جديد في النظام"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        
        # التحقق من عدم تكرار البريد الإلكتروني
        cursor.execute("SELECT id FROM users WHERE email = %s", (user_data.email,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "email_exists",
                    "message": "This email is already in use"
            }
            )
        
        # التحقق من عدم تكرار اسم المستخدم
        cursor.execute("SELECT id FROM users WHERE username = %s", (user_data.username,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "username_exists",
                    "message": "This username is already in use"
            }
            )
        
        # تشفير كلمة المرور
        hashed_password = get_password_hash(user_data.password)
        
        # إدخل المستخدم الجديد
        current_time = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            INSERT INTO users (
                email, username, hashed_password, full_name, 
                phone, role, is_active, is_verified, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, FALSE, %s, %s)
            RETURNING id
        """, (
            user_data.email,
            user_data.username,
            hashed_password,
            user_data.full_name,
            user_data.phone,
            user_data.role,
            current_time,
            current_time
        ))
        
        user_id = cursor.fetchone()['id']
        
        # جلب المستخدم الجديد
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        new_user = cursor.fetchone()
        
        conn.commit()
    finally:
        conn.close()
        
    return new_user


# ============================================
# 2. تسجيل الدخول (Login) - ✅ MODIFIED
# ============================================
@router.post("/login", response_model=Token)
def login(credentials: UserLogin, response: Response):
    """
    تسجيل الدخول والحصول على Access Token و Refresh Token
    يمكن استخدام البريد الإلكتروني أو اسم المستخدم
    """
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        
        # البحث عن المستخدم (بالاسم أو البريد)
        cursor.execute("""
            SELECT * FROM users 
            WHERE username = %s OR email = %s
        """, (credentials.username, credentials.username))
        
        user = cursor.fetchone()
        
        # التحقق من وجود المستخدم وكلمة المرور
        if not user or not verify_password(credentials.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "type": "invalid_credentials",
                    "message": "Invalid username or password"
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # التحقق من أن الحساب نشط
        if not user["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="الحساب غير نشط"
            )
        
        # إنشاء Access Token
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={
                "sub": str(user["id"]),
                "username": user["username"],
                "role": user["role"]
            },
            expires_delta=access_token_expires
        )
        
        # ✅ NEW - Create Refresh Token
        refresh_token = create_refresh_token(user["id"])
        
        # ✅ SET COOKIE USING CENTRAL HELPER
        cookie_params = get_refresh_token_cookie_params(
            max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        )
        response.set_cookie(value=refresh_token, **cookie_params)
        
        conn.commit()
    finally:
        conn.close()
        
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


# ============================================
# 2b. تسجيل الدخول OAuth2 Compatible (للـ Swagger UI) - ✅ MODIFIED
# ============================================
@router.post("/token", response_model=Token)
def login_oauth2(response: Response, username: str = Form(...), password: str = Form(...)):
    """
    OAuth2 compatible login endpoint (للـ Swagger UI)
    """
    credentials = UserLogin(username=username, password=password)
    return login(credentials, response)


# ============================================
# ✅ NEW - 2c. Refresh Access Token
# ============================================
@router.post("/refresh", response_model=Token)
def refresh_access_token(request: Request, response: Response):
    """
    تجديد الـ Access Token باستخدام Refresh Token من الكوكيز
    """
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing"
        )
        
    # Verify refresh token
    token_data = verify_refresh_token(refresh_token)
    
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token غير صالح أو منتهي الصلاحية"
        )
    
    # ✅ Token Rotation: Invalidate old token and create new one
    revoke_refresh_token(refresh_token)
    
    new_refresh_token = create_refresh_token(token_data["user_id"])
    
    # ✅ Set new cookie using central helper
    cookie_params = get_refresh_token_cookie_params(
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    )
    response.set_cookie(value=new_refresh_token, **cookie_params)
    
    # Create new access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={
            "sub": str(token_data["user_id"]),
            "username": token_data["username"],
            "role": token_data["role"]
        },
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer"
    }


# ============================================
# 3. الحصول على معلومات المستخدم الحالي
# ============================================
@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """الحصول على معلومات المستخدم المسجل حالياً"""
    return current_user


# ============================================
# 4. تحديث معلومات المستخدم
# ============================================
@router.put("/me", response_model=UserResponse)
def update_user_profile(
    user_update: UserUpdate,
    current_user: dict = Depends(get_current_user)
):
    """تحديث معلومات الملف الشخصي للمستخدم"""
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        
        # بناء استعلام التحديث
        update_fields = []
        params = []
        
        for field, value in user_update.model_dump(exclude_unset=True).items():
            if value is not None:
                update_fields.append(f"{field} = %s")
                params.append(value)
        
        if update_fields:
            update_fields.append("updated_at = CURRENT_TIMESTAMP")
            params.append(current_user["id"])

            query = f"UPDATE users SET {', '.join(update_fields)} WHERE id = %s"
            cursor.execute(query, params)
        
        # جلب المستخدم المحدث
        cursor.execute("SELECT * FROM users WHERE id = %s", (current_user["id"],))
        updated_user = cursor.fetchone()
        
        conn.commit()
    finally:
        conn.close()
        
    return updated_user


# ============================================
# 5. تغيير كلمة المرور
# ============================================
@router.post("/change-password")
def change_password(
    password_data: ChangePassword,
    current_user: dict = Depends(get_current_user)
):
    """تغيير كلمة المرور للمستخدم الحالي"""
    
    # التحقق من كلمة المرور القديمة
    if not verify_password(password_data.old_password, current_user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="كلمة المرور القديمة غير صحيحة"
        )
    
    # تحديث كلمة المرور
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        new_hashed_password = get_password_hash(password_data.new_password)
        
        cursor.execute(
            "UPDATE users SET hashed_password = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (new_hashed_password, current_user["id"])
        )
        conn.commit()
    finally:
        conn.close()
    
    return {
        "status": "success",
        "message": "تم تغيير كلمة المرور بنجاح"
    }


# ============================================
# 6. تسجيل الخروج - ✅ MODIFIED
# ============================================
@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    """
    تسجيل الخروج وإلغاء صلاحية الـ Refresh Token
    """
    refresh_token = request.cookies.get("refresh_token")
    
    if refresh_token:
        # Revoke the refresh token
        revoke_refresh_token(refresh_token)
    
    # Clear cookie using central helper
    cookie_params = get_refresh_token_cookie_params(max_age=0)
    response.delete_cookie(
        key=cookie_params.pop("key"),
        **cookie_params
    )
    
    return {
        "status": "success",
        "message": "تم تسجيل الخروج بنجاح"
    }


# ============================================
# ✅ NEW - Logout from all devices
# ============================================
@router.post("/logout-all")
def logout_all_devices(current_user: dict = Depends(get_current_user)):
    """
    تسجيل الخروج من جميع الأجهزة - إلغاء جميع الـ Refresh Tokens
    """
    revoke_all_user_tokens(current_user["id"])
    
    return {
        "status": "success",
        "message": "تم تسجيل الخروج من جميع الأجهزة بنجاح"
    }


# ============================================
# 7. APIs إدارية (للمستقبل)
# ============================================
@router.get("/users")
def get_all_users(
    page: int = 1,
    page_size: int = 10,
    current_user: dict = Depends(get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ليس لديك صلاحية الوصول لهذا المورد"
        )
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()['count']
        
        cursor.execute(
            "SELECT id, email, username, avatar, full_name, role, is_active, created_at FROM users ORDER BY COALESCE(NULLIF(created_at, '')::timestamp, '1970-01-01'::timestamp) DESC LIMIT %s OFFSET %s",
            (page_size, (page - 1) * page_size)
        )
        
        users = cursor.fetchall()
        
        # pagination
        pagination = _build_pagination(total, page, page_size)
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "success",
        "data": {
            "total": pagination["total"],
            "page": pagination["page"],
            "page_size": pagination["page_size"],
            "total_pages": pagination["total_pages"],
            "has_next": pagination["has_next"],
            "has_prev": pagination["has_prev"],
            "users": users
        }
    }
    