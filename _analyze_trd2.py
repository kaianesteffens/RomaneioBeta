import re

c = open(r'C:\Users\eduardo\AppData\Local\Temp\trd_js.txt', encoding='utf-8', errors='ignore').read()

print("=== APIUrlGlobalService ===")
for m in re.finditer(r'APIUrlGlobalService', c):
    ctx = c[m.start():m.start()+300]
    if 'function' in ctx or 'action' in ctx:
        print(f"\nat {m.start()}: {repr(ctx)}")
        print('---')

print("\n=== action function ===")
for m in re.finditer(r'\.action\s*=\s*function', c):
    print(f"\nat {m.start()}: {repr(c[m.start():m.start()+400])}")
    
print("\n=== anonymous/rest ===")
for m in re.finditer(r'anonymous/rest', c):
    ctx = c[max(0,m.start()-200):m.start()+200]
    print(f"\nat {m.start()}: {repr(ctx)}")

print("\n=== validaCliente ===")
for m in re.finditer(r'validaCliente', c):
    print(f"\nat {m.start()}: {repr(c[max(0,m.start()-100):m.start()+300])}")

print("\n=== externalTenantConsulta ===")
for m in re.finditer(r'externalTenant', c):
    print(f"\nat {m.start()}: {repr(c[max(0,m.start()-50):m.start()+300])}")
