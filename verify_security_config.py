import os
import sys

# Add the current directory to sys.path to allow importing app
sys.path.append(os.getcwd())

from app.core.config import Settings
from app.core.security_helpers import get_refresh_token_cookie_params, get_cors_settings

def test_dev_settings():
    print("Testing Development Settings (ENV=dev)...")
    os.environ["ENV"] = "dev"
    # Re-instantiate settings to pick up env change
    from app.core import config
    import importlib
    importlib.reload(config)
    from app.core.config import settings
    
    assert settings.IS_PROD == False
    assert "http://localhost:3000" in settings.ALLOWED_ORIGINS
    
    cookie_params = get_refresh_token_cookie_params()
    assert cookie_params["secure"] == False
    assert cookie_params["samesite"] == "lax"
    assert cookie_params["httponly"] == True
    
    cors = get_cors_settings()
    assert "http://localhost:3000" in cors["allow_origins"]
    print("✅ Dev settings verified.\n")

def test_prod_settings():
    print("Testing Production Settings (ENV=prod)...")
    os.environ["ENV"] = "prod"
    os.environ["PROD_DOMAIN"] = "https://myapp.com"
    
    from app.core import config
    import importlib
    importlib.reload(config)
    from app.core.config import settings
    
    assert settings.IS_PROD == True
    assert settings.ALLOWED_ORIGINS == ["https://myapp.com"]
    assert "*" not in settings.ALLOWED_ORIGINS
    
    cookie_params = get_refresh_token_cookie_params()
    assert cookie_params["secure"] == True
    assert cookie_params["samesite"] == "strict"
    assert cookie_params["httponly"] == True
    
    cors = get_cors_settings()
    assert cors["allow_origins"] == ["https://myapp.com"]
    print("✅ Prod settings verified.\n")

if __name__ == "__main__":
    try:
        test_dev_settings()
        test_prod_settings()
        print("🎉 All security configurations verified successfully!")
    except AssertionError as e:
        print(f"❌ Verification failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"💥 An error occurred: {e}")
        sys.exit(1)
