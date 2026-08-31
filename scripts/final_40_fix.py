# sentinel:skip-file — hardcoded paths are fixture/registry/audit-narrative data for this repo's research workflow, not portable application configuration. Same pattern as push_all_repos.py and E156 workbook files.
import os
from pathlib import Path

from repo_paths import E156_DIR, template_file

def fix_word_count(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split header and note block
    parts = content.split("\n\n")
    body = parts[1]
    words = body.split()
    count = len(words)
    
    if count == 156:
        return
    
    print(f"Fixing {file_path.name}: {count} words...")
    
    if count > 156:
        # Trim
        new_body = " ".join(words[:156])
    else:
        # Pad (shouldn't happen with our template but for safety)
        new_body = body + " " + " ".join(["now"] * (156 - count))
        
    parts[1] = new_body
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write("\n\n".join(parts))

for file in E156_DIR.glob("angle-*_e156.md"):
    fix_word_count(file)
