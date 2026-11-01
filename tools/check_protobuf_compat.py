"""Check that librespot's generated protobuf messages still work on the installed protobuf version.

librespot 0.0.10 pins protobuf 3.20.x. Before relaxing that pin, run this in a scratch venv that has the
newer protobuf installed (protobuf >= 4 needs the pure-Python implementation):

    set PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
    python tools/check_protobuf_compat.py

It round-trips (serialise -> parse) an empty instance of every message type. A handful of proto2 messages with
required fields cannot be serialised empty on ANY version (33 of 173 today); what matters is that the count of
failures is the same as on the pinned version. Verified identical on protobuf 3.20.1, 5.29.6 and 6.33.6.
A live Spotify login is NOT covered by this check.
"""
import importlib
import pkgutil
import sys

import google.protobuf as pb
import librespot.proto as proto

# The generated modules import each other by bare name.
sys.path.insert(0, proto.__path__[0])


def main() -> int:
    ok = bad = 0
    for module_info in pkgutil.iter_modules(proto.__path__):
        if not module_info.name.endswith("_pb2"):
            continue
        try:
            mod = importlib.import_module(f"librespot.proto.{module_info.name}")
        except Exception as exc:
            bad += 1
            print("IMPORT FAIL", module_info.name, repr(exc)[:70])
            continue
        for name, cls in vars(mod).items():
            descriptor = getattr(cls, "DESCRIPTOR", None)
            if descriptor is None or not hasattr(cls, "SerializeToString") or not hasattr(descriptor, "fields"):
                continue
            try:
                message = cls()
                cls().ParseFromString(message.SerializeToString())
                ok += 1
            except Exception as exc:
                bad += 1
                print("FAIL", module_info.name, name, repr(exc)[:80])
    print(f"protobuf {pb.__version__} | message types round-tripped: {ok} | failed: {bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
