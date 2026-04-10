import os
from typing import List

# Load .env file automatically (works in both dev and production)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — env vars must be set externally


class Settings:
    def __init__(self):
        # ---------------------------
        # 🌍 Environment Detection
        # ---------------------------
        self.ENV = os.getenv("ENV", "dev").lower()

        # ✅ FIX: robust detection (supports prod / production / live)
        self.IS_PROD = self.ENV in ["prod", "production", "live"]

        # ---------------------------
        # 🌐 CORS Configuration
        # ---------------------------
        prod_origins = os.getenv(
            "PROD_DOMAIN",
            "https://your-production-domain.com"
        ).split(",")

        self.PROD_ALLOWED_ORIGINS: List[str] = [
            o.strip() for o in prod_origins if o.strip()
        ]

        self.DEV_ALLOWED_ORIGINS: List[str] = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]

    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        if self.IS_PROD:
            # Include dev origins for debugging (safe with proper cookie settings)
            prod = [o for o in self.PROD_ALLOWED_ORIGINS if o != "*"]
            return list(dict.fromkeys(prod + self.DEV_ALLOWED_ORIGINS))
        return self.DEV_ALLOWED_ORIGINS

    @property
    def REFRESH_TOKEN_COOKIE_SETTINGS(self) -> dict:
        """
        Cookie settings for consistent auth handling.
        """
        if self.IS_PROD:
            return {
                "httponly": True,
                "secure": True,          # 🔥 required for SameSite=None
                "samesite": "none",      # 🔥 FIX: allow cross-domain cookies
                "path": "/",
                "domain": None           # مهم تخليها None مع Render + Netlify
            }

        # Development
        return {
            "httponly": True,
            "secure": False,
            "samesite": "lax",
            "path": "/"
        }


settings = Settings()
