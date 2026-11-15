"""Requirement extraction, ported from the Pilot 1 engine (app/main.py) with its logic unchanged:
extract text from the BRD, send the unchanged system prompt + the text, parse and validate the JSON reply.
"""
import hashlib
import io
import json
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader
from pypdf.errors import PdfReadError

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".tsv"}
# Added in Pilot 2 (not in Pilot 1): these are plain text, read like .txt. The AI sees the file as it is (a table stays
# comma or tab separated). utf-8-sig drops the byte order mark that Excel puts at the start of a CSV.
PLAIN_TEXT_EXTENSIONS = {".md", ".csv", ".tsv"}
SUPPORTED_LABEL = "PDF, DOCX, TXT, MD, CSV or TSV"
REQUIRED_REQUIREMENT_FIELDS = ("summary", "description", "acceptanceCriteria")

PROMPT_FILENAME = "TestMax_Requirements_Extraction_System_Prompt_v1.2.txt"
PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "system-prompts" / PROMPT_FILENAME


class DocumentError(Exception):
    """The uploaded file cannot be used. The message is shown to the user."""


def load_system_prompt() -> tuple[str, str]:
    """(prompt text, sha256). The file is read as-is; it must never be edited without approval."""
    raw = PROMPT_PATH.read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def extract_docx_text(content: bytes) -> str:
    document = DocxDocument(io.BytesIO(content))
    parts = [p.text for p in document.paragraphs if p.text]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts).strip()


def extract_text(filename: str, content: bytes) -> str:
    extension = Path(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentError(f"Unsupported file type '{extension or 'unknown'}'. Use {SUPPORTED_LABEL}.")
    if not content:
        raise DocumentError("The uploaded file is empty.")
    try:
        if extension == ".pdf":
            text = extract_pdf_text(content)
        elif extension == ".docx":
            text = extract_docx_text(content)
        elif extension in PLAIN_TEXT_EXTENSIONS:
            text = content.decode("utf-8-sig").strip()
        else:
            text = content.decode("utf-8").strip()
    except (PdfReadError, UnicodeDecodeError):
        raise DocumentError("Could not read this file. Check that it is a valid PDF, DOCX or UTF-8 text file (TXT, MD, CSV, TSV).") from None
    except Exception:  # noqa: BLE001
        raise DocumentError("Could not extract text from this file.") from None
    if not text:
        raise DocumentError("No text could be extracted (the document may be empty or image-based).")
    return text


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def validate_requirements_payload(parsed) -> list[dict]:
    if not isinstance(parsed, dict) or "requirements" not in parsed:
        raise ValueError("Response JSON is missing the top-level 'requirements' array.")
    requirements = parsed["requirements"]
    if not isinstance(requirements, list):
        raise ValueError("'requirements' must be a JSON array.")
    for index, item in enumerate(requirements, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Requirement #{index} is not a JSON object.")
        for field in REQUIRED_REQUIREMENT_FIELDS:
            if field not in item or not isinstance(item[field], str):
                raise ValueError(f"Requirement #{index} is missing a valid '{field}' field.")
    return requirements


def parse_reply(raw: str) -> list[dict]:
    """Raises ValueError with a readable message when the AI reply is not the expected JSON."""
    try:
        parsed = json.loads(strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"The AI response was not valid JSON ({exc.msg}).") from None
    return validate_requirements_payload(parsed)
