import os

for root, dirs, files in os.walk('.'):
    if '.venv' in root or '.git' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as file:
                    content = file.read()
                    if 'CACHES' in content:
                        print(f"Found 'CACHES' in {path}")
                        # print lines around it
                        lines = content.splitlines()
                        for i, l in enumerate(lines):
                            if 'CACHES' in l:
                                for idx in range(max(0, i-2), min(len(lines), i+8)):
                                    print(f"  {idx+1}: {lines[idx]}")
            except Exception:
                pass
