from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status
from app.database import get_db
from app.models.Users import User
from app.models.enums import UserRole
from app.core.security import oauth2_scheme, decode_access_token


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    الحصول على المستخدم الحالي من الـ Token
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="لا يمكن التحقق من بيانات الاعتماد",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # فك تشفير الـ Token
    payload = decode_access_token(token)
    user_id: int = payload.get("sub")
    
    if user_id is None:
        raise credentials_exception
    
    # البحث عن المستخدم في قاعدة البيانات
    user = db.query(User).filter(User.id == user_id).first()
    
    if user is None:
        raise credentials_exception
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="الحساب غير نشط"
        )
    
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    التحقق من أن المستخدم نشط
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="المستخدم غير نشط"
        )
    return current_user


def require_role(*allowed_roles: UserRole):
    """
    Decorator للتحقق من صلاحيات المستخدم
    مثال: require_role(UserRole.ADMIN, UserRole.SELLER)
    """
    def role_checker(current_user: User = Depends(get_current_active_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ليس لديك صلاحية للوصول لهذا المورد"
            )
        return current_user
    
    return role_checker


def get_current_admin(
    current_user: User = Depends(require_role(UserRole.ADMIN))
) -> User:
    """الحصول على المستخدم الحالي (يجب أن يكون Admin فقط)"""
    return current_user


def get_current_seller(
    current_user: User = Depends(require_role(UserRole.SELLER, UserRole.ADMIN))
) -> User:
    """الحصول على المستخدم الحالي (Seller أو Admin)"""
    return current_user