#!/usr/bin/env python3
"""
Fixed patch script for smt_nightly_trade_v3_1.py
Properly handles indentation when patching JSON parsing.
"""
import re
import os

target_file = "smt_nightly_trade_v3_1.py"

if not os.path.exists(target_file):
    print(f"Error: {target_file} not found.")
    exit(1)

with open(target_file, 'r') as f:
    lines = f.readlines()

patched_lines = []
changes_made = 0

for i, line in enumerate(lines):
    # Check for response_mime_type and remove it
    if 'response_mime_type' in line and 'application/json' in line:
        # Skip this line (remove it)
        changes_made += 1
        print(f"Removed response_mime_type at line {i+1}")
        continue
    
    # Check for json.loads(response.text) or json.loads(result.text)
    match = re.match(r'^(\s*)data\s*=\s*json\.loads\((response|result)\.text\)', line)
    if match:
        indent = match.group(1)  # Preserve original indentation
        var_name = match.group(2)  # 'response' or 'result'
        
        # Add the clean_text line with same indentation
        patched_lines.append(f'{indent}clean_text = {var_name}.text.strip().replace("```json", "").replace("```", "").strip()\n')
        patched_lines.append(f'{indent}data = json.loads(clean_text)\n')
        changes_made += 1
        print(f"Patched JSON parsing at line {i+1}")
        continue
    
    patched_lines.append(line)

if changes_made > 0:
    with open(target_file, 'w') as f:
        f.writelines(patched_lines)
    print(f"\nDONE: Made {changes_made} changes to {target_file}")
else:
    print("No changes needed - file may already be patched or has different structure.")
