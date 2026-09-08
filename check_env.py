#!/usr/bin/env python3
"""
Jalankan ini di server bot kamu SEBELUM jalankan bot:
  python3 check_env.py

Paste output ke developer biar bisa fix dengan tepat.
"""
import sys

print(f"Python: {sys.version}")
print()

# 1. Pyrogram version & layer
try:
    import pyrogram
    print(f"✅ pyrogram versi: {pyrogram.__version__}")
except ImportError as e:
    print(f"❌ pyrogram tidak terinstall: {e}")
    sys.exit(1)

# 2. Cek semua InputReportReason yang tersedia
from pyrogram.raw import types, functions
print()
print("=== InputReportReason tersedia ===")
candidates = [
    "InputReportReasonSpam",
    "InputReportReasonViolence",
    "InputReportReasonPornography",
    "InputReportReasonChildAbuse",
    "InputReportReasonCopyright",
    "InputReportReasonGeoIrrelevant",
    "InputReportReasonFake",
    "InputReportReasonIllegalDrugs",
    "InputReportReasonPersonalDetails",
    "InputReportReasonOther",
]
available = []
for name in candidates:
    ok = hasattr(types, name)
    print(f"  {'✅' if ok else '❌'} {name}")
    if ok:
        available.append(name)

# 3. Cek signature messages.Report
import inspect
print()
print("=== messages.Report signature ===")
sig = inspect.signature(functions.messages.Report.__init__)
params = list(sig.parameters.keys())
print(f"  params: {params}")

# 4. Cek motor (MongoDB driver)
print()
try:
    import motor
    print(f"✅ motor versi: {motor.version}")
except ImportError:
    print("❌ motor tidak terinstall — jalankan: pip install motor==3.3.2")

# 5. Cek aiogram
try:
    import aiogram
    print(f"✅ aiogram versi: {aiogram.__version__}")
except ImportError as e:
    print(f"❌ aiogram: {e}")

print()
print("=== DONE — paste output ini ===")
