"""
test_save_pdf_file.py

Phase 7 tests for file_manager.save_pdf_file()'s cancel handling and
defensive .pdf extension enforcement. The underlying native dialog
function (tkinter.filedialog.asksaveasfilename) is mocked directly, so
these tests never instantiate a real Tk() window and don't need a
display.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import file_manager


def test_returns_none_on_cancel():
    with patch("file_manager.filedialog.asksaveasfilename", return_value=""):
        result = file_manager.save_pdf_file()
    assert result is None


def test_returns_none_on_whitespace_only_result():
    # Defensive: treat an empty/whitespace-only string the same as a
    # cancel, rather than trying to save to a nonsense path.
    with patch("file_manager.filedialog.asksaveasfilename", return_value="   "):
        result = file_manager.save_pdf_file()
    assert result is None


def test_returns_chosen_path_with_pdf_extension(tmp_path):
    chosen = str(tmp_path / "my_output.pdf")
    with patch("file_manager.filedialog.asksaveasfilename", return_value=chosen):
        result = file_manager.save_pdf_file()
    assert result == Path(chosen)


def test_adds_pdf_extension_if_dialog_returns_none(tmp_path):
    # Simulates the native dialog returning a filename with no extension
    # at all (defaultextension normally handles this, but we don't trust
    # that alone).
    chosen = str(tmp_path / "my_output")
    with patch("file_manager.filedialog.asksaveasfilename", return_value=chosen):
        result = file_manager.save_pdf_file()
    assert result == tmp_path / "my_output.pdf"


def test_corrects_wrong_extension_from_dialog(tmp_path):
    # Simulates a user having typed a non-.pdf extension explicitly,
    # which Windows' native dialog does NOT override on its own.
    chosen = str(tmp_path / "my_output.txt")
    with patch("file_manager.filedialog.asksaveasfilename", return_value=chosen):
        result = file_manager.save_pdf_file()
    assert result == tmp_path / "my_output.txt.pdf"
    assert result.suffix.lower() == ".pdf"


def test_preserves_valid_pdf_extension_case(tmp_path):
    chosen = str(tmp_path / "My_Output.PDF")
    with patch("file_manager.filedialog.asksaveasfilename", return_value=chosen):
        result = file_manager.save_pdf_file()
    assert result == Path(chosen)  # untouched -- already a valid .pdf name


def test_handles_filename_with_spaces(tmp_path):
    chosen = str(tmp_path / "my merged file.pdf")
    with patch("file_manager.filedialog.asksaveasfilename", return_value=chosen):
        result = file_manager.save_pdf_file()
    assert result == Path(chosen)


def test_handles_unicode_filename(tmp_path):
    chosen = str(tmp_path / "résumé final.pdf")
    with patch("file_manager.filedialog.asksaveasfilename", return_value=chosen):
        result = file_manager.save_pdf_file()
    assert result == Path(chosen)


def test_default_filename_is_merged_pdf():
    """Confirms save_pdf_file's default `default_name` parameter matches
    the spec's required default filename, and that it's actually passed
    through to the dialog as `initialfile`.
    """
    with patch("file_manager.filedialog.asksaveasfilename", return_value="") as mock_dialog:
        file_manager.save_pdf_file()
    _, kwargs = mock_dialog.call_args
    assert kwargs["initialfile"] == "merged.pdf"


def test_custom_default_filename_is_used():
    with patch("file_manager.filedialog.asksaveasfilename", return_value="") as mock_dialog:
        file_manager.save_pdf_file(default_name="report_compressed.pdf")
    _, kwargs = mock_dialog.call_args
    assert kwargs["initialfile"] == "report_compressed.pdf"


def test_only_pdf_filetype_is_offered():
    """No 'All files' fallback -- consistent with the same requirement
    already enforced for the Open dialog in Phase 4.
    """
    with patch("file_manager.filedialog.asksaveasfilename", return_value="") as mock_dialog:
        file_manager.save_pdf_file()
    _, kwargs = mock_dialog.call_args
    assert kwargs["filetypes"] == [("PDF files", "*.pdf")]


def test_select_output_folder_returns_path_on_selection(tmp_path):
    with patch("file_manager.filedialog.askdirectory", return_value=str(tmp_path)):
        result = file_manager.select_output_folder()
    assert result == tmp_path


def test_select_output_folder_returns_none_on_cancel():
    with patch("file_manager.filedialog.askdirectory", return_value=""):
        result = file_manager.select_output_folder()
    assert result is None
