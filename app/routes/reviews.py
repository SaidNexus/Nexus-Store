from fastapi import APIRouter, HTTPException, Depends, Form
from typing import Optional
from app.database_postgres import get_db_connection
from psycopg2.extras import RealDictCursor
from app.routes.auth import get_current_user


router = APIRouter(
    prefix="/reviews",
    tags=["Reviews"]
)

# ============================================================
# Update Review
# PUT /reviews/{review_id}
# ============================================================
@router.put("/{review_id}")
async def update_review(
    review_id: int,
    rating: float = Form(..., ge=0, le=5),
    comment: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]

    # validate rating
    if int(rating * 10) % 5 != 0:
        raise HTTPException(
            status_code=400,
            detail="Rating must be in 0.5 increments (e.g. 0.5, 1, 1.5, ...)"
        )

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # check review exists
        cursor.execute("""
            SELECT user_id FROM reviews WHERE id = %s
        """, (review_id,))
        review = cursor.fetchone()

        if not review:
            raise HTTPException(status_code=404, detail="Review not found")

        # check ownership
        if review["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not allowed")

        # update
        cursor.execute("""
            UPDATE reviews
            SET rating = %s, comment = %s
            WHERE id = %s
        """, (rating, comment, review_id))

        # get updated review
        cursor.execute("""
            SELECT 
                r.*, 
                COALESCE(u.full_name, u.username) as user_name,
                u.avatar as user_avatar
            FROM reviews r
            LEFT JOIN users u ON r.user_id = u.id
            WHERE r.id = %s
        """, (review_id,))

        updated_review = dict(cursor.fetchone())
        updated_review["can_edit"] = True
        updated_review["can_delete"] = True

        conn.commit()

    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم تعديل التقييم",
        "data": updated_review
    }


# ============================================================
# Delete Review
# DELETE /reviews/{review_id}
# ============================================================
@router.delete("/{review_id}")
async def delete_review(
    review_id: int,
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # check review exists
        cursor.execute("""
            SELECT user_id FROM reviews WHERE id = %s
        """, (review_id,))
        review = cursor.fetchone()

        if not review:
            raise HTTPException(status_code=404, detail="Review not found")

        # check ownership
        if review["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not allowed")

        # delete
        cursor.execute("""
            DELETE FROM reviews WHERE id = %s
        """, (review_id,))

        conn.commit()

    finally:
        conn.close()

    return {
        "status": "success",
        "message": "تم حذف التقييم"
    }