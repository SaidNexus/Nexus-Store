from app.core.config import settings


def get_refresh_token_cookie_params(max_age: int = 7 * 24 * 60 * 60) -> dict:
    """
    Returns the parameters for the refresh token cookie based on environment.
    """

    params = {
        "httponly": True,
        "secure": True,          
        "samesite": "None", 
        "path": "/",
    }

    # ✅ لو في settings نعمل override
    if hasattr(settings, "REFRESH_TOKEN_COOKIE_SETTINGS"):
        params.update(settings.REFRESH_TOKEN_COOKIE_SETTINGS)

    params["max_age"] = max_age
    params["key"] = "refresh_token"

    return params


def get_cors_settings() -> dict:
    """
    Returns CORS settings for the app based on environment.
    """
    return {
        "allow_origins": settings.ALLOWED_ORIGINS,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
