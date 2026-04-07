from app.core.config import settings


def get_refresh_token_cookie_params(max_age: int = 7 * 24 * 60 * 60) -> dict:
    """
    Returns the parameters for the refresh token cookie based on environment.
    """

    is_production = getattr(settings, "ENV", "development") == "production"

    params = {
        "httponly": True,
        "secure": is_production,               
        "samesite": "None" if is_production else "Lax",
        "path": "/",
    }

    if hasattr(settings, "REFRESH_TOKEN_COOKIE_SETTINGS"):
        custom = settings.REFRESH_TOKEN_COOKIE_SETTINGS.copy()

        if is_production:
            custom.pop("secure", None)
            custom.pop("samesite", None)

        params.update(custom)

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
