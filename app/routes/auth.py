from fastapi import APIRouter, HTTPException, status, Depends, Form, Response, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Optional
from datetime import datetime, timedelta, timezone
import jwt
import secrets
import math
import hashlib
from app.database_postgres import get_db_connection
from app.core.security_helpers import get_refresh_token_cookie_params
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/auth", tags=["Authentication"])

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
        "total": total, "page": page, "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ✅ معرفين قبل أي حاجة تكالهم
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def create_refresh_token(user_id: int, conn, cursor) -> str:
    """يستخدم connection موجود - مش بيفتح connection جديد"""
    refresh_token = generate_refresh_token()
    token_hash = hash_token(refresh_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    cursor.execute("""
        INSERT INTO refresh_tokens (user_id, token_hash, expires_at, is_revoked, created_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, token_hash, expires_at.isoformat(), False, datetime.now(timezone.utc).isoformat()))
    return refresh_token

def verify_refresh_token(refresh_token: str) -> Optional[dict]:
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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if not result or result["is_revoked"] or not result["is_active"]:
        return None
    expires_at = result["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        return None
    return result

def revoke_refresh_token(refresh_token: str):
    token_hash = hash_token(refresh_token)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("UPDATE refresh_tokens SET is_revoked = TRUE WHERE token_hash = %s", (token_hash,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def revoke_all_user_tokens(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            UPDATE refresh_tokens SET is_revoked = TRUE
            WHERE user_id = %s AND is_revoked = FALSE
        """, (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="انتهت صلاحية الـ Token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token غير صالح")

def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="بيانات المصادقة غير صحيحة")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
        user = cursor.fetchone()
        conn.commit()
    finally:
        conn.close()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="المستخدم غير موجود")
    return user

def get_optional_user(token: str = Depends(oauth2_scheme_optional)) -> Optional[dict]:
    if not token:
        return None
    try:
        return get_current_user(token)
    except:
        return None

def get_admin_user(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ليس لديك صلاحية لتنفيذ هذا الإجراء")
    return current_user


# ============================================
# 1. Register
# ============================================
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserRegister):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT id FROM users WHERE email = %s", (user_data.email,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"type": "email_exists", "message": "This email is already in use"}
            )
        cursor.execute("SELECT id FROM users WHERE username = %s", (user_data.username,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"type": "username_exists", "message": "This username is already in use"}
            )
        hashed_password = get_password_hash(user_data.password)
        current_time = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            INSERT INTO users (
                email, username, hashed_password, full_name,
                phone, role, is_active, is_verified, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, FALSE, %s, %s)
            RETURNING id
        """, (
            user_data.email, user_data.username, hashed_password,
            user_data.full_name, user_data.phone, user_data.role,
            current_time, current_time
        ))
        user_id = cursor.fetchone()['id']
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        new_user = cursor.fetchone()
        conn.commit()
        return new_user
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================
# 2. Login
# ============================================
@router.post("/login", response_model=Token)
def login(credentials: UserLogin, response: Response):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT * FROM users WHERE username = %s OR email = %s
        """, (credentials.username, credentials.username))
        user = cursor.fetchone()

        if not user or not verify_password(credentials.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"type": "invalid_credentials", "message": "Invalid username or password"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not user["is_active"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="الحساب غير نشط")

        access_token = create_access_token(
            data={"sub": str(user["id"]), "username": user["username"], "role": user["role"]},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        refresh_token = create_refresh_token(user["id"], conn, cursor)  # ✅ نفس الـ connection
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    cookie_params = get_refresh_token_cookie_params(max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    response.set_cookie(value=refresh_token, **cookie_params)
    return {"access_token": access_token, "token_type": "bearer"}


# ============================================
# 2b. OAuth2 Login (Swagger UI)
# ============================================
@router.post("/token", response_model=Token)
def login_oauth2(response: Response, username: str = Form(...), password: str = Form(...)):
    return login(UserLogin(username=username, password=password), response)


# ============================================
# 2c. Refresh Access Token ✅ FIXED
# ============================================
@router.post("/refresh", response_model=Token)
def refresh_access_token(request: Request, response: Response):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token missing")

    token_data = verify_refresh_token(refresh_token)
    if not token_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token غير صالح أو منتهي الصلاحية")

    # Token Rotation - revoke old, create new
    revoke_refresh_token(refresh_token)

    # ✅ فتح connection جديد للـ insert
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        new_refresh_token = create_refresh_token(token_data["user_id"], conn, cursor)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    cookie_params = get_refresh_token_cookie_params(max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    response.set_cookie(value=new_refresh_token, **cookie_params)

    access_token = create_access_token(
        data={"sub": str(token_data["user_id"]), "username": token_data["username"], "role": token_data["role"]},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": access_token, "token_type": "bearer"}


# ============================================
# 3. Me
# ============================================
@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: dict = Depends(get_current_user)):
    return current_user


# ============================================
# 4. Update Profile
# ============================================
@router.put("/me", response_model=UserResponse)
def update_user_profile(user_update: UserUpdate, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        update_fields = []
        params = []
        for field, value in user_update.model_dump(exclude_unset=True).items():
            if value is not None:
                update_fields.append(f"{field} = %s")
                params.append(value)
        if update_fields:
            update_fields.append("updated_at = CURRENT_TIMESTAMP")
            params.append(current_user["id"])
            cursor.execute(f"UPDATE users SET {', '.join(update_fields)} WHERE id = %s", params)
        cursor.execute("SELECT * FROM users WHERE id = %s", (current_user["id"],))
        updated_user = cursor.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return updated_user


# ============================================
# 5. Change Password
# ============================================
@router.post("/change-password")
def change_password(password_data: ChangePassword, current_user: dict = Depends(get_current_user)):
    if not verify_password(password_data.old_password, current_user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="كلمة المرور القديمة غير صحيحة")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "UPDATE users SET hashed_password = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (get_password_hash(password_data.new_password), current_user["id"])
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"status": "success", "message": "تم تغيير كلمة المرور بنجاح"}


# ============================================
# 6. Logout ✅ مرة واحدة بس
# ============================================
@router.post("/logout")
def logout(request: Request, response: Response, current_user: dict = Depends(get_current_user)):
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        revoke_refresh_token(refresh_token)
    response.delete_cookie(key="refresh_token", httponly=True, secure=True, samesite="none", path="/")
    return {"status": "success", "message": "تم تسجيل الخروج بنجاح"}


# ============================================
# 7. Logout All Devices
# ============================================
@router.post("/logout-all")
def logout_all_devices(current_user: dict = Depends(get_current_user)):
    revoke_all_user_tokens(current_user["id"])
    return {"status": "success", "message": "تم تسجيل الخروج من جميع الأجهزة بنجاح"}


# ============================================
# 8. Admin - Get All Users
# ============================================
@router.get("/users")
def get_all_users(page: int = 1, page_size: int = 10, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ليس لديك صلاحية الوصول لهذا المورد")
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
        pagination = _build_pagination(total, page, page_size)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"status": "success", "data": {**pagination, "users": users}}
