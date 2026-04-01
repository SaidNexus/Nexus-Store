import logging
from datetime import datetime, timedelta, timezone
from typing import List

logger = logging.getLogger(__name__)

def get_product_status(product: dict) -> List[str]:
    status = []
    if not isinstance(product, dict):
        return status
        
    # 🆕 NEW (Check safely using created_at mainly, fallback updated_at)
    date_value = product.get("created_at") or product.get("updated_at")

    if date_value:
        try:
            if isinstance(date_value, str):
                # Handles strings that might have timezone offsets or just naive
                try:
                    created_at_dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
                except ValueError:
                    # fallback to basic naive parse
                    created_at_dt = datetime.strptime(date_value.split(".")[0], "%Y-%m-%d %H:%M:%S")
            else:
                created_at_dt = date_value

            # Handle aware vs naive
            if created_at_dt.tzinfo is not None:
                now = datetime.now(timezone.utc)
            else:
                now = datetime.utcnow()

            if now - created_at_dt <= timedelta(days=7):
                status.append("new")
        except Exception as e:
            logger.error(f"Error parsing date for product status: {e}")
            pass

    # 💸 BEST DEAL
    try:
        raw_price = product.get("price")
        raw_discount = product.get("discount_price")
        
        if raw_price is not None and raw_discount is not None:
            price = float(raw_price)
            discount_price = float(raw_discount)
            
            if price > 0 and discount_price >= 0:
                discount = ((price - discount_price) / price) * 100
                if discount >= 14:
                    status.append("best_deal")
    except (ValueError, TypeError) as e:
        logger.error(f"Error calculating discount for product status: {e}")
        pass

    return status