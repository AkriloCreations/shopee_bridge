# shopee_bridge/api.py
import importlib, re, pkgutil
import frappe  # pyright: ignore[reportMissingImports]

PKG_NAME = (__package__ or "shopee_bridge").split(".")[0]
NAME_PATTERN = re.compile(r"^(?P<mod>[A-Za-z_][A-Za-z0-9_]*)__(?P<fn>[A-Za-z_][A-Za-z0-9_]*)$")

# ⛔ modul yang TIDAK boleh diexpose
EXCLUDE_MODULES = {"webhook", "auth", "api", "__pycache__"}
# ✅ (opsional) kalau mau ketat, batasi modul yang boleh discan:
ALLOWED_MODULES = None  # contoh: {"utils","orders","audit","finance","helpers"}
# ✅ (opsional) hanya expose fungsi yang diawali prefix tertentu
ONLY_PREFIX = None      # contoh: "api_"

# cache untuk nama fungsi tanpa modul -> (module, function)
_BARE_CACHE: dict[str, tuple[str, str]] = {}

def _resolve_module(mod_name: str) -> str:
    return mod_name if mod_name.startswith(f"{PKG_NAME}.") else f"{PKG_NAME}.{mod_name}"

def _is_allowed(mod_name: str, fn_name: str) -> bool:
    if mod_name in EXCLUDE_MODULES:
        return False
    if ALLOWED_MODULES is not None and mod_name not in ALLOWED_MODULES:
        return False
    if fn_name.startswith("_"):
        return False
    if ONLY_PREFIX and not fn_name.startswith(ONLY_PREFIX):
        return False
    return True

def _build_dynamic(export_name: str, mod_name: str, fn_name: str):
    @frappe.whitelist()
    def wrapper(**kwargs):
        mod = importlib.import_module(_resolve_module(mod_name))
        fn = getattr(mod, fn_name)
        return fn(**kwargs)
    wrapper.__name__ = export_name
    globals()[export_name] = wrapper  # cache wrapper agar panggilan berikutnya cepat
    return wrapper

def _candidate_modules() -> list[str]:
    """List modul di dalam package tanpa meng-import semuanya dulu."""
    pkg = importlib.import_module(PKG_NAME)
    names = []
    for it in pkgutil.iter_modules(pkg.__path__):
        name = it.name
        if name in EXCLUDE_MODULES or name.startswith("_"):
            continue
        if ALLOWED_MODULES and name not in ALLOWED_MODULES:
            continue
        names.append(name)
    return names

def _resolve_bare_function(func_name: str) -> tuple[str, str] | None:
    """Cari func di modul kandidat. Ambil pertama yang cocok; jika ganda → error."""
    if func_name in _BARE_CACHE:
        return _BARE_CACHE[func_name]

    found = None
    duplicates = []

    for mod_name in _candidate_modules():
        mod = importlib.import_module(_resolve_module(mod_name))  # lazy: import saat butuh
        obj = getattr(mod, func_name, None)
        if callable(obj) and _is_allowed(mod_name, func_name):
            if found is None:
                found = (mod_name, func_name)
            else:
                duplicates.append(mod_name)

    if duplicates:
        # nama fungsi ditemukan di >1 modul → minta pakai module__function
        raise AttributeError(
            f"Ambiguous function '{func_name}' found in multiple modules: "
            f"{found[0]}, {', '.join(duplicates)}. "
            f"Use '{found[0]}__{func_name}' or '<module>__{func_name}'."
        )

    if found:
        _BARE_CACHE[func_name] = found
    return found

def __getattr__(name: str):
    """Dukungan:
       1) module__function
       2) function (bare): auto-resolve dengan scan modul kandidat + cache
    """
    # 1) Pola module__function
    m = NAME_PATTERN.match(name)
    if m:
        mod_name = m.group("mod")
        fn_name  = m.group("fn")
        if not _is_allowed(mod_name, fn_name):
            raise AttributeError(f"{mod_name}.{fn_name} is not exposed")
        return _build_dynamic(name, mod_name, fn_name)

    # 2) Bare function: otomatis cari modul yang punya fungsi ini
    resolved = _resolve_bare_function(name)
    if resolved:
        mod_name, fn_name = resolved
        return _build_dynamic(name, mod_name, fn_name)

    # Tidak cocok keduanya
    raise AttributeError(name)

@frappe.whitelist()
def list_hint():
    return {
        "pattern": ["shopee_bridge.api.<module>__<function>", "shopee_bridge.api.<function> (auto)"],
        "excluded_modules": sorted(EXCLUDE_MODULES),
        "allowed_modules": sorted(ALLOWED_MODULES) if ALLOWED_MODULES else None,
        "only_prefix": ONLY_PREFIX,
        "note": "private functions (prefix '_') are not exposed; bare name is auto-resolved & cached",
    }
