import re

FILENAME = 'smt_nightly_trade_v3_1.py'
with open(FILENAME, 'r') as f:
    content = f.read()

# Remove the response_schema and response_mime_type from the generate_content call
# This allows the Google Search tool to work without the 400 error
old_call = r'response = self\.model\.generate_content\(\s*prompt,\s*tools=\[self\.search_tool\],\s*generation_config=\{.*?\}\s*\)'
new_call = 'response = self.model.generate_content(prompt, tools=[self.search_tool])'

content = re.sub(old_call, new_call, content, flags=re.DOTALL)

with open(FILENAME, 'w') as f:
    f.write(content)
print("400 Error Fix Applied: Strict schema removed to enable Search Tool.")
