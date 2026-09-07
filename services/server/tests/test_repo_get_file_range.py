"""Finestra di righe su repo_get_file.

Leggere un file intero per capire un metodo costa decine di migliaia di token:
il grafo dice gia' a che riga guardare, quindi la lettura deve poter essere
mirata. Qui si verifica il ritaglio, non l'accesso al repository.
"""
import pytest

from src.text_window import slice_lines


TEXT = "\n".join(f"riga{i}" for i in range(1, 11))  # 10 righe


def test_no_range_returns_everything():
    out = slice_lines(TEXT)
    assert out["content"] == TEXT
    assert (out["start_line"], out["end_line"], out["total_lines"]) == (1, 10, 10)
    assert out["truncated"] is False


def test_range_returns_only_the_window():
    out = slice_lines(TEXT, 3, 5)
    assert out["content"] == "riga3\nriga4\nriga5"
    assert (out["start_line"], out["end_line"]) == (3, 5)
    assert out["truncated"] is True


def test_start_only_reads_to_the_end():
    out = slice_lines(TEXT, 8, None)
    assert out["content"] == "riga8\nriga9\nriga10"
    assert out["end_line"] == 10


def test_end_only_reads_from_the_start():
    out = slice_lines(TEXT, None, 2)
    assert out["content"] == "riga1\nriga2"
    assert out["start_line"] == 1


def test_end_past_the_file_is_clamped_not_an_error():
    # Chiedere venti righe attorno a una che sta in fondo e' normale.
    out = slice_lines(TEXT, 9, 100)
    assert out["content"] == "riga9\nriga10"
    assert out["end_line"] == 10
    assert out["truncated"] is True


def test_start_past_the_file_returns_nothing_but_says_so():
    out = slice_lines(TEXT, 50, 60)
    assert out["content"] == ""
    assert out["total_lines"] == 10
    assert out["truncated"] is True


def test_whole_file_range_is_not_marked_truncated():
    out = slice_lines(TEXT, 1, 10)
    assert out["truncated"] is False


def test_empty_file():
    out = slice_lines("")
    assert out["content"] == ""
    assert out["total_lines"] == 0


@pytest.mark.parametrize("start,end", [(0, 5), (-1, None), (None, 0), (5, 3)])
def test_incoherent_limits_are_rejected(start, end):
    with pytest.raises(ValueError):
        slice_lines(TEXT, start, end)


def test_booleans_are_not_line_numbers():
    with pytest.raises(ValueError):
        slice_lines(TEXT, True, None)
