import ast
import codecs
import hashlib
import io
from pathlib import Path
import tempfile
from typing import Literal
import unittest
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "app" / "materials.py").read_text(encoding="utf-8"))
FUNCTIONS = {"safe_name", "copy_limited", "validate_content", "ReviewInput"}
CONSTANTS = {"MAX_BYTES", "CHUNK", "TYPES"}
NODES = [node for node in TREE.body if
         (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in FUNCTIONS)
         or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in CONSTANTS for target in node.targets))]
NS = dict(globals())
exec(compile(ast.Module(body=NODES, type_ignores=[]), "<isolated-materials-units>", "exec"), NS)


class MaterialsUnitTests(unittest.TestCase):
    def test_filename_removes_directories(self):
        self.assertEqual(NS["safe_name"]("../../report.txt"), "report.txt")
        self.assertEqual(NS["safe_name"]("C:\\folder\\report.txt"), "report.txt")

    def test_extension_is_case_insensitive(self):
        self.assertEqual(NS["safe_name"]("REPORT.PDF"), "REPORT.PDF")

    def test_rejects_unsupported_extension(self):
        with self.assertRaises(HTTPException) as result:
            NS["safe_name"]("report.exe")
        self.assertEqual(result.exception.status_code, 415)

    def test_rejects_control_characters(self):
        with self.assertRaises(HTTPException):
            NS["safe_name"]("report\n.txt")

    def test_copy_hash_and_size(self):
        with tempfile.TemporaryDirectory() as root:
            data = "Материалы этапа".encode("utf-8")
            path = Path(root) / "file.part"
            size, digest = NS["copy_limited"](io.BytesIO(data), path)
            self.assertEqual(size, len(data))
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertEqual(path.read_bytes(), data)

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(HTTPException) as result:
                NS["copy_limited"](io.BytesIO(b""), Path(root) / "empty")
            self.assertEqual(result.exception.status_code, 422)

    def test_file_limit(self):
        old = NS["MAX_BYTES"]
        try:
            NS["MAX_BYTES"] = 8
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaises(HTTPException) as result:
                    NS["copy_limited"](io.BytesIO(b"123456789"), Path(root) / "big")
                self.assertEqual(result.exception.status_code, 413)
        finally:
            NS["MAX_BYTES"] = old

    def test_valid_utf8(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "text"
            path.write_text("Русский текст", encoding="utf-8")
            NS["validate_content"](path, ".txt")

    def test_invalid_text(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "text"
            for value in (b"\xff\xfe", b"abc\x00def"):
                path.write_bytes(value)
                with self.assertRaises(HTTPException):
                    NS["validate_content"](path, ".txt")

    def test_rejects_fake_pdf(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "pdf"
            path.write_bytes(b"not a PDF")
            with self.assertRaises(HTTPException):
                NS["validate_content"](path, ".pdf")

    def test_docx_structure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "docx"
            with ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", '<Types ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>')
                archive.writestr("word/document.xml", "<document/>")
            NS["validate_content"](path, ".docx")

    def test_rejects_docx_with_vba(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "docx"
            with ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("word/document.xml", "<document/>")
                archive.writestr("word/vbaProject.bin", "macro")
            with self.assertRaises(HTTPException):
                NS["validate_content"](path, ".docx")

    def test_revision_requires_remarks(self):
        with self.assertRaises(ValidationError):
            NS["ReviewInput"](decision="revision", remarks="   ")
        value = NS["ReviewInput"](decision="revision", remarks=" Исправить требования ")
        self.assertEqual(value.remarks, "Исправить требования")

    def test_decision_enum(self):
        with self.assertRaises(ValidationError):
            NS["ReviewInput"](decision="invalid")
        self.assertEqual(NS["ReviewInput"](decision="accepted").remarks, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
