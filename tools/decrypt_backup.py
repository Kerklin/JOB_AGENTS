"""Opens an encrypted backup pack from the outbox folder on YOUR computer.

Usage (Windows PowerShell or any terminal):
    pip install cryptography
    python tools/decrypt_backup.py outbox/<file>.enc

You are asked for BACKUP_KEY (typing is hidden). The CV, cover letter, report and
the e-mail text are written to a new folder called restored/<file>/ .
"""
import base64
import getpass
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agents.common import decrypt  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python tools/decrypt_backup.py outbox/<file>.enc")
    src = pathlib.Path(sys.argv[1])
    key = getpass.getpass("BACKUP_KEY: ")
    try:
        data = json.loads(decrypt(key, src.read_bytes()))
    except Exception:
        sys.exit("Could not open the file: wrong BACKUP_KEY, or the file is damaged.")
    out = pathlib.Path("restored") / src.stem
    out.mkdir(parents=True, exist_ok=True)
    for f in data["files"]:
        (out / pathlib.Path(f["name"]).name).write_bytes(base64.b64decode(f["b64"]))
    (out / "message.txt").write_text(data["subject"] + "\n\n" + data["body"], encoding="utf-8")
    print("Done. Files are in:", out.resolve())


if __name__ == "__main__":
    main()
