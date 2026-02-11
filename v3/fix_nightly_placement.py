import re

with open("smt_nightly_trade_v3_1.py", "r") as f:
    content = f.read()

# Remove the misplaced function from inside the try/except
bad_block = """    print("  [V3.1.21] Hot-reload enabled")
# V3.1.61: Gemini timeout wrapper
def _gemini_with_timeout(client, model, contents, config, timeout=120):
    \"\"\"Call Gemini with a thread-based timeout.\"\"\"
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        return future.result(timeout=timeout)
except ImportError:"""

good_block = """    print("  [V3.1.21] Hot-reload enabled")
except ImportError:"""

if bad_block in content:
    content = content.replace(bad_block, good_block)
    print("FIX 1: Removed misplaced function from try/except")
else:
    print("WARN: Bad block not found")

# Now insert the function AFTER the hot-reload except block
insert_after = """except ImportError:
    HOT_RELOAD_ENABLED = False"""

helper = """except ImportError:
    HOT_RELOAD_ENABLED = False


# V3.1.61: Gemini timeout wrapper
def _gemini_with_timeout(client, model, contents, config, timeout=120):
    \"\"\"Call Gemini with a thread-based timeout.\"\"\"
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.models.generate_content,
            model=model,
            contents=contents,
            config=config
        )
        return future.result(timeout=timeout)
"""

if insert_after in content:
    content = content.replace(insert_after, helper, 1)
    print("FIX 2: Inserted _gemini_with_timeout after hot-reload block")
else:
    print("WARN: Insert point not found")

with open("smt_nightly_trade_v3_1.py", "w") as f:
    f.write(content)

import py_compile
try:
    py_compile.compile("smt_daemon_v3_1.py", doraise=True)
    py_compile.compile("smt_nightly_trade_v3_1.py", doraise=True)
    print("SYNTAX OK - both files clean")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
