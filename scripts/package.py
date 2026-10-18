#!/usr/bin/env python3
"""Create a shareable binary/doc/skill archive, never including local state."""
from pathlib import Path
import platform
import zipfile

root = Path(__file__).resolve().parents[1]
binary = root / 'dist/ai-bridge'
if not binary.is_file():
    raise SystemExit('Run make build first')
archive = root / 'dist' / f'ai-bridge-{platform.system().lower()}-{platform.machine()}.zip'
files = [binary, *(root / name for name in ('README.md', 'API.md', 'SKILL.md', 'MACOS_RELEASE.md'))]
files.extend(path for path in (root / 'skills').rglob('*') if path.is_file())
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    for path in files:
        relative = path.name if path == binary else path.relative_to(root)
        bundle.write(path, str(Path('ai-bridge') / relative))
print(archive)
