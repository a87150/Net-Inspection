import pathlib, re
settings = pathlib.Path("net/settings.py").read_text(encoding="utf-8")
used = sorted(set(re.findall(r"os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]", settings)))
# 其他模块也可能读 env
for p in pathlib.Path("net").rglob("*.py"):
    if "migrations" in p.parts: continue
    try: t = p.read_text(encoding="utf-8")
    except Exception: continue
    used = sorted(set(used) | set(re.findall(r"os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]", t)))
example = pathlib.Path(".env.example").read_text(encoding="utf-8")
declared = set(re.findall(r"^#?\s*([A-Z0-9_]+)=", example, re.M))
missing = [v for v in used if v not in declared]
print("settings/代码读取", len(used), "| .env.example 声明", len(declared))
print("缺", len(missing))
for m in missing: print("  ", m)