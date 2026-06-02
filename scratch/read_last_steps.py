import json

path = r"C:\Users\oleyk\.gemini\antigravity-ide\brain\ec87cd2a-2f27-4cd4-8e0b-9a0c5ddfdad5\.system_generated\logs\transcript.jsonl"
print(f"Reading late steps from {path}...")

with open(path, 'r', encoding='utf-8') as f:
    for idx, line in enumerate(f):
        if idx >= 80:
            data = json.loads(line)
            step_type = data.get('type')
            source = data.get('source')
            content = data.get('content')
            
            print(f"\n=================================")
            print(f"Step {idx}: source={source}, type={step_type}")
            print(f"=================================")
            if content:
                print(content)
