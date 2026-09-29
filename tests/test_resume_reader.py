from pathlib import Path

from pypdf import PdfWriter

from src.resume_reader import extract_text, process_resumes


def test_extract_text_from_pdf(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as pdf_file:
        writer.write(pdf_file)

    text = extract_text(pdf_path)

    assert isinstance(text, str)


def test_process_resumes_works_for_new_files(tmp_path, monkeypatch):
    input_dir = tmp_path / "input" / "resumes"
    output_dir = tmp_path / "output" / "text"
    metadata_path = tmp_path / "output" / "metadata.json"

    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    resume_path = input_dir / "sample.txt"
    resume_path.write_text("Python developer with SQL experience.", encoding="utf-8")

    monkeypatch.setattr("src.resume_reader.INPUT_FOLDER", input_dir)
    monkeypatch.setattr("src.resume_reader.TEXT_OUTPUT_FOLDER", output_dir)
    monkeypatch.setattr("src.resume_reader.METADATA_PATH", metadata_path)

    process_resumes()

    assert (output_dir / "sample.txt").read_text(encoding="utf-8") == "Python developer with SQL experience."
    assert metadata_path.exists()
