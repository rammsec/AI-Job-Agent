import hashlib
import json
from pathlib import Path

from docx import Document
from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_FOLDER = PROJECT_ROOT / "data" / "input" / "resumes"
TEXT_OUTPUT_FOLDER = PROJECT_ROOT / "data" / "output" / "text"
METADATA_PATH = PROJECT_ROOT / "data" / "output" / "processing_metadata.json"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def file_hash(file_path: Path) -> str:
    """Return a SHA-256 hash without loading an entire resume into memory."""
    hasher = hashlib.sha256()

    with file_path.open("rb") as resume_file:
        for chunk in iter(lambda: resume_file.read(64 * 1024), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def load_metadata() -> dict:
    if not METADATA_PATH.exists():
        return {}

    try:
        with METADATA_PATH.open(encoding="utf-8") as metadata_file:
            return json.load(metadata_file)
    except (json.JSONDecodeError, OSError):
        print("Warning: processing metadata could not be read; processing all resumes.")
        return {}


def save_metadata(metadata: dict) -> None:
    with METADATA_PATH.open("w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=2, sort_keys=True)
        metadata_file.write("\n")


def supported_resume_files(folder: Path) -> list[Path]:
    return sorted(
        file_path for file_path in folder.iterdir() if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def process_resumes():
    INPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    TEXT_OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    resume_files = supported_resume_files(INPUT_FOLDER)

    if not resume_files:
        print(f"No supported resumes found in: {INPUT_FOLDER}")
        return

    metadata = load_metadata()

    for resume_path in resume_files:
        file_name = resume_path.name
        current_hash = file_hash(resume_path)
        previous_hash = metadata.get(file_name, {}).get("sha256")

        print(f"\nFound: {file_name}")

        if previous_hash == current_hash:
            print("Status: UNCHANGED")
            print("Skipping...")
            continue

        status = "NEW" if previous_hash is None else "CHANGED"
        print(f"Status: {status}")
        print("Extracting...")

        try:
            text = extract_text(resume_path)
        except (OSError, ValueError, RuntimeError) as error:
            print(f"Status: FAILED ({error})")
            continue

        txt_path = TEXT_OUTPUT_FOLDER / f"{resume_path.stem}.txt"
        txt_path.write_text(text, encoding="utf-8")

        metadata[file_name] = {"sha256": current_hash}
        save_metadata(metadata)

        action = "Created" if status == "NEW" else "Updated"
        print(f"{action}: {txt_path.name}")
        print(f"Characters: {len(text):,}")
        print("Status: SUCCESS")


def extract_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(str(file_path))
        pages = []

        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)

        return "\n".join(pages)

    if suffix == ".docx":
        document = Document(file_path)
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        return "\n".join(paragraphs)

    if suffix == ".txt":
        return file_path.read_text(encoding="utf-8", errors="ignore")

    raise ValueError(f"Unsupported resume format: {suffix}")
