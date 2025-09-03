import importlib

PKG = (__package__ or "shopee_bridge").split(".")[0]

def _resolve(mod: str) -> str:
    return mod if mod.startswith(f"{PKG}.") else f"{PKG}.{mod}"

def call(path: str = None, *, module: str = None, func: str = None, **kwargs):
    """
    Dynamic call:
      - call(path="utils:get_settings")
      - call(module="utils", func="get_settings", foo=1)
    """
    if path:
        module, func = path.split(":", 1)
    mod = importlib.import_module(_resolve(module))
    fn  = getattr(mod, func)
    return fn(**kwargs)
