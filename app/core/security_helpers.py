from app.core.config import settings


def get_refresh_token_cookie_params(max_age: int = 7 * 24 * 60 * 60) -> dict:
    """
    Returns the parameters for the refresh token cookie based on environment.
    Uses centralized settings.IS_PROD for consistency.
    """
    is_production = settings.IS_PROD  # 🔥 FIXED

    return {
        "key": "refresh_token",
        "httponly": True,
        "secure": is_production,                 # True in production
        "samesite": "none" if is_production else "lax",  # 🔥 critical for cross-domain
        "path": "/",
        "max_age": max_age,
    }


def get_logout_cookie_params() -> dict:
    """
    Returns params to CLEAR the refresh token cookie.
    Uses set_cookie with max_age=0 and an empty value.
    """
    is_production = settings.IS_PROD  # 🔥 FIXED

    return {
        "key": "refresh_token",
        "value": "",
        "httponly": True,
        "secure": is_production,
        "samesite": "none" if is_production else "lax",
        "path": "/",
        "max_age": 0,
    }


def get_cors_settings() -> dict:
    """
    Returns CORS settings for the app based on environment.
    """
    return {
        "allow_origins": settings.ALLOWED_ORIGINS,
        "allow_credentials": True,   # 🔥 important for cookies
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
