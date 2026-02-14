import os, sys

print("CWD:", os.getcwd())
print("sys.path (first 5 entries):")
for p in sys.path[:5]:
    print("  ", p)

print("Has fantasyai directory?", os.path.isdir("fantasyai"))
print("Has fantasyai/__init__.py?", os.path.isfile(os.path.join("fantasyai", "__init__.py")))

try:
    import fantasyai
    print("Imported fantasyai OK:", fantasyai, getattr(fantasyai, "__file__", None))
except Exception as e:
    print("Import fantasyai FAILED:", repr(e))
