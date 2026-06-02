import json

path = r"C:\Users\oleyk\.gemini\antigravity-ide\brain\f7d24124-f658-4916-81dd-51dc53b8c042\.system_generated\logs\transcript.jsonl"
print(f"Reading f7d24124 transcript from {path}...")

with open(path, 'r', encoding='utf-8') as f:
    for idx, line in enumerate(f):
        data = json.loads(line)
        step_type = data.get('type')
        source = data.get('source')
        content = data.get('content')
        tool_calls = data.get('tool_calls', [])
        
        print(f"Step {idx}: source={source}, type={step_type}")
        if content:
            print(f"  Content: {content[:300]}...")
        if tool_calls:
            print(f"  Tool Calls: {[tc.get('name') for tc in tool_calls]}")
