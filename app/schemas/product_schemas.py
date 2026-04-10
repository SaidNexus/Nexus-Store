from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="اسم المنتج")
    description: Optional[str] = Field(None, description="وصف المنتج")
    price: float = Field(..., gt=0, description="سعر المنتج")
    discount_price: Optional[float] = Field(None, ge=0, description="سعر المنتج بعد الخصم")
    stock_quantity: int = Field(default=0, ge=0, description="الكمية المتوفرة")
    category_id: int = Field(..., description="معرف الفئة")
    image_url: Optional[str] = Field(None, max_length=500, description="رابط صورة المنتج")
    images: Optional[str] = Field(None, description="صور إضافية (JSON)")
    brand: Optional[str] = Field(None, max_length=100, description="العلامة التجارية")
    sku: Optional[str] = Field(None, max_length=100, description="رمز المنتج")
    weight: Optional[float] = Field(None, ge=0, description="الوزن")
    dimensions: Optional[str] = Field(None, max_length=100, description="الأبعاد")
    is_active: bool = Field(default=True, description="المنتج نشط؟")
    is_featured: bool = Field(default=False, description="منتج مميز؟")


class ProductCreate(ProductBase):
    """Schema لإنشاء منتج جديد"""
    pass


class ProductUpdate(BaseModel):
    """Schema لتحديث منتج موجود - كل الحقول اختيارية"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    price: Optional[float] = Field(None, gt=0)
    discount_price: Optional[float] = Field(None, ge=0)
    stock_quantity: Optional[int] = Field(None, ge=0)
    category_id: Optional[int] = None
    image_url: Optional[str] = Field(None, max_length=500)
    images: Optional[str] = None
    brand: Optional[str] = Field(None, max_length=100)
    sku: Optional[str] = Field(None, max_length=100)
    weight: Optional[float] = Field(None, ge=0)
    dimensions: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None


class ProductResponse(ProductBase):
    """Schema للرد بمعلومات المنتج"""
    id: int
    seller_id: int
    rating: float
    review_count: int
    status: list[str] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProductListResponse(BaseModel):
    """Schema لعرض قائمة المنتجات مع pagination"""
    total: int
    page: int
    page_size: int
    products: list[ProductResponse]