"""
Jalankan script ini di server bot kamu:
  python3 check_pyrogram.py

Output-nya paste ke gw — biar gw tau persis apa yang tersedia
di pyrogram versi yang kamu pakai.
"""

import pyrogram
print(f"Pyrogram version: {pyrogram.__version__}")

from pyrogram.raw import types

# Cek semua InputReportReason yang tersedia
reasons = [
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

print("\nInputReportReason types tersedia:")
for r in reasons:
    exists = hasattr(types, r)
    print(f"  {'✅' if exists else '❌'} {r}")

# Cek signature messages.Report
from pyrogram.raw import functions
import inspect
sig = inspect.signature(functions.messages.Report.__init__)
print(f"\nmessages.Report params: {list(sig.parameters.keys())}")
