import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SDK_ROOT = ROOT.parent / "smart-service-sdk"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

if "smart_service_sdk" not in sys.modules:
    package = types.ModuleType("smart_service_sdk")
    package.__path__ = [str(SDK_ROOT / "smart_service_sdk")]
    sys.modules["smart_service_sdk"] = package
