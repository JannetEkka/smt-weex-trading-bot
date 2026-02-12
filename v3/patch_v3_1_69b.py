#!/usr/bin/env python3
"""
V3.1.69b: Fix remaining bare genai.Client() in daemon portfolio manager.
Run AFTER patch_v3_1_69.py
"""

DAEMON_FILE = "smt_daemon_v3_1.py"

def main():
    with open(DAEMON_FILE, 'r') as f:
        content = f.read()
    
    old = """        from google import genai
        from google.genai.types import GenerateContentConfig
        
        client = genai.Client()
        config = GenerateContentConfig(temperature=0.1)
        
        response = _gemini_with_timeout(client, "gemini-2.5-flash", prompt, config, timeout=120)"""
    
    new = """        from google.genai.types import GenerateContentConfig
        
        config = GenerateContentConfig(temperature=0.1)
        
        response = _gemini_full_call_daemon("gemini-2.5-flash", prompt, config, timeout=90)"""
    
    if old in content:
        content = content.replace(old, new, 1)
        with open(DAEMON_FILE, 'w') as f:
            f.write(content)
        print("[FIX] Daemon portfolio manager now uses _gemini_full_call_daemon")
    else:
        print("[SKIP] Pattern not found (may already be fixed)")
    
    # Verify
    import subprocess, sys
    r = subprocess.run([sys.executable, "-m", "py_compile", DAEMON_FILE], capture_output=True, text=True)
    print(f"[{'PASS' if r.returncode == 0 else 'FAIL'}] Syntax check")
    
    # Confirm no bare genai.Client() outside wrappers
    with open(DAEMON_FILE, 'r') as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if 'genai.Client()' in line and '_do_call' not in lines[max(0,i-5):i+1][0]:
            # Check if this line is inside _gemini_full_call_daemon
            context = ''.join(lines[max(0,i-10):i])
            if '_do_call' in context or '_gemini_full_call' in context:
                continue
            print(f"  WARNING: Bare genai.Client() at line {i+1}")

if __name__ == "__main__":
    main()
