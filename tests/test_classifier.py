from pathlib import Path

from app.classifier import classify_file, is_series_filename, sanitize_filename, unique_destination


def test_sanitize_filename_removes_linux_and_control_characters() -> None:
    assert sanitize_filename("../bad:/na\\me?.mkv") == "_bad_na_me_.mkv"
    assert sanitize_filename("..") == "telegram-file"


def test_classification_examples() -> None:
    assert classify_file("Movie.Name.2025.1080p.mkv") == "movies"
    assert classify_file("Series.Name.S01E04.mkv") == "tv"
    assert classify_file("track.flac") == "audio"
    assert classify_file("photo.webp") == "images"
    assert classify_file("bundle.7z") == "archives"
    assert classify_file("sheet.xlsx") == "documents"
    assert classify_file("clip.mp4") == "videos"


def test_series_detection_is_case_insensitive() -> None:
    assert is_series_filename("show.s1e4.1080p.mkv")
    assert is_series_filename("Show S12E101.mkv")
    assert not is_series_filename("Movie.2025.mkv")


def test_unique_destination_does_not_overwrite(tmp_path: Path) -> None:
    (tmp_path / "movie.mkv").write_bytes(b"x")
    first_duplicate = unique_destination(tmp_path, "movie.mkv")
    assert first_duplicate.name == "movie (1).mkv"
    first_duplicate.write_bytes(b"y")

    second_duplicate = unique_destination(tmp_path, "movie.mkv")
    assert second_duplicate.name == "movie (2).mkv"
    second_duplicate.write_bytes(b"z")

    third_duplicate = unique_destination(tmp_path, "movie.mkv")
    assert third_duplicate.name == "movie (3).mkv"

