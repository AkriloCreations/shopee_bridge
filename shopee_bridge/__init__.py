__version__ = "0.0.1"

import importlib
__all__ = ["api", "utils", "orders", "finance", "audit", "helpers"]

def __getattr__(name: str):
	if name in __all__:
		return importlib.import_module(f"{__name__}.{name}")
	raise AttributeError(name)
