import pytest

from supportlens.ml.cli import main, prepare
from supportlens.ml.io import IntegrityError, save_once


def test_wrong_gold_hash_stops_before_creating_derived_data(tmp_path):
    source = tmp_path / "data/processed/gold_annotations.csv"
    source.parent.mkdir(parents=True)
    source.write_text("thread_id,intent\n1,ACCOUNT_ACCESS\n")
    with pytest.raises(IntegrityError, match="SHA-256"):
        prepare(tmp_path)
    assert not (tmp_path / "data/derived").exists()


def test_missing_cli_input_has_sanitized_failure(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "prepare"]) == 2
    output = capsys.readouterr().out
    assert "Traceback" not in output
    assert "No customer text is logged" in output


def test_write_once_does_not_overwrite_established_artifact(tmp_path):
    output = tmp_path / "artifact.txt"
    save_once(output, "first")
    save_once(output, "first")
    with pytest.raises(IntegrityError):
        save_once(output, "second")
    assert output.read_text() == "first"
