import re

c = open(r'C:\Users\eduardo\AppData\Local\Temp\trd_js.txt', encoding='utf-8', errors='ignore').read()

# Find function loginServices
m = re.search(r'function loginServices', c)
if m:
    print(f"function loginServices at {m.start()}")
    print(repr(c[m.start():m.start()+2000]))
else:
    print("function loginServices: NOT FOUND")

# Look for tracking-related HTTP calls
print("\n\n=== HTTP calls related to tracking ===")
for m in re.finditer(r'\$http\.(get|post)\s*\([^)]{0,200}track', c, re.IGNORECASE):
    print(f"\nat {m.start()}: {repr(c[max(0,m.start()-50):m.start()+300])}")

print("\n\n=== Any REST endpoint with 'login' path ===")
for m in re.finditer(r'[\'\"](/[a-z/\-]+login[a-z/\-]*)[\'\"]\s*,', c, re.IGNORECASE):
    print(f"at {m.start()}: {repr(c[m.start():m.start()+200])}")

print("\n\n=== codigoTracking in services ===")
for m in re.finditer(r'codigoTracking', c):
    ctx = c[max(0,m.start()-100):m.start()+300]
    if 'http' in ctx or 'rest' in ctx.lower() or 'service' in ctx.lower():
        print(f"\nat {m.start()}: {repr(ctx)}")

print("\n\n=== Any /tracking REST path ===")
for m in re.finditer(r'[\'\"](/[a-z/_\-]*tracking[a-z/_\-]*)[\'\""]', c, re.IGNORECASE):
    print(f"at {m.start()}: {repr(c[m.start():m.start()+200])}")
