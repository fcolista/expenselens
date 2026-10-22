from slowapi import Limiter
from slowapi.util import get_remote_address

OCR_UPLOADS_PER_HOUR = 10
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
