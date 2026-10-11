"""Export PII-Tracer to ONNX (fp32 + int8) under the Cloakroom home. See cloakroom/onnx_export.py.

    python scripts/export_onnx.py [--out DIR]
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from cloakroom.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["export-onnx", *sys.argv[1:]]))
