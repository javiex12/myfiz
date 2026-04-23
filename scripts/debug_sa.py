import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
import json

v = settings.GOOGLE_SERVICE_ACCOUNT_JSON
print("len:", len(v))
d = json.loads(v)
print("parsed ok, type:", d.get("type"))
