from app.chunker import chunk_text


def test_short_text_is_one_chunk():
    assert chunk_text("abc", chunk_size=10, overlap=2) == [(0, "abc")]


def test_long_text_covers_everything_and_respects_overlap():
    text = "a" * 25
    chunks = chunk_text(text, chunk_size=10, overlap=3)
    assert chunks[0][0] == 0
    assert chunks[-1][0] + len(chunks[-1][1]) == len(text)
    for (s1, c1), (s2, c2) in zip(chunks, chunks[1:]):
        assert s2 <= s1 + len(c1)  # overlap
        assert s2 + len(c2) > s1 + len(c1)  # no gap
        assert len(c2) <= 10


def test_prefers_sentence_boundary_near_end():
    text = "mot hai ba bon. " * 3  # 48 chars
    chunks = chunk_text(text, chunk_size=20, overlap=4)
    assert chunks[0][1].endswith(".")
