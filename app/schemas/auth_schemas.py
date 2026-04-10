from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional
from datetime import datetime
from app.models.enums import UserRole


class UserRegister(BaseModel):
    """Schema لتسجيل مستخدم جديد"""
    email: EmailStr = Field(..., description="البريد الإلكتروني")
    username: str = Field(..., min_length=3, max_length=100, description="اسم المستخدم")
    password: str = Field(..., min_length=6, description="كلمة المرور")
    full_name: Optional[str] = Field(None, max_length=255, description="الاسم الكامل")
    phone: Optional[str] = Field(None, max_length=20, description="رقم الهاتف")
    role: UserRole = Field(default=UserRole.CUSTOMER, description="نوع الحساب")
    
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError('كلمة المرور يجب أن تكون 6 أحرف على الأقل')
        return v


class UserLogin(BaseModel):
    """Schema لتسجيل الدخول"""
    username: str = Field(..., description="اسم المستخدم أو البريد الإلكتروني")
    password: str = Field(..., description="كلمة المرور")


class Token(BaseModel):
    """Schema للـ Token"""
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """البيانات المخزنة في الـ Token"""
    user_id: Optional[int] = None
    username: Optional[str] = None


class UserResponse(BaseModel):
    """Schema للرد بمعلومات المستخدم"""
    id: int
    email: str
    username: str
    full_name: Optional[str]
    phone: Optional[str]
    address: Optional[str]
    city: Optional[str]
    country: Optional[str]
    postal_code: Optional[str]
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    avatar: Optional[str] = None
    
    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """Schema لتحديث بيانات المستخدم"""
    full_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)


class ChangePassword(BaseModel):
    """Schema لتغيير كلمة المرور"""
    old_password: str = Field(..., description="كلمة المرور القديمة")
    new_password: str = Field(..., min_length=6, description="كلمة المرور الجديدة")
    
    @validator('new_password')
    def validate_password(cls, v, values):
        if 'old_password' in values and v == values['old_password']:
            raise ValueError('كلمة المرور الجديدة يجب أن تكون مختلفة عن القديمة')
        if len(v) < 6:
            raise ValueError('كلمة المرور يجب أن تكون 6 أحرف على الأقل')
        return v