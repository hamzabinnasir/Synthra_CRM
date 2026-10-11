"""Knowledge-base chunking and hashing: pure functions, no database or embedding model needed."""
from app.kb import service as kb


def test_normalise_collapses_spaces_and_crlf():
    assert kb.normalise("  a   b\r\nc\t\td  ") == "a b\nc d"


def test_sha_is_stable_hex():
    assert kb.sha("hello") == kb.sha("hello") and kb.sha("hello") != kb.sha("hello!")
    assert len(kb.sha("hello")) == 64


def test_empty_and_short_text():
    assert kb.chunk_text("") == [] and kb.chunk_text("   \n  ") == []
    assert kb.chunk_text("short text") == ["short text"]


def test_long_text_is_split_within_the_size_limit_and_fully_covered():
    text = ("word " * 600).strip()
    chunks = kb.chunk_text(text)
    assert len(chunks) >= 3
    assert all(len(chunk) <= kb.CHUNK_SIZE for chunk in chunks)
    assert text.startswith(chunks[0]) and text.endswith(chunks[-1])


def test_chunks_overlap_so_context_is_not_lost_at_the_cut():
    text = ("word " * 600).strip()
    first, second = kb.chunk_text(text)[:2]
    assert first[-20:] in second or second[:20] in first


def test_prefers_to_end_on_a_sentence_boundary():
    text = "A" * 700 + ". " + "B" * 700
    chunks = kb.chunk_text(text)
    assert chunks[0] == "A" * 700 + "."


def test_always_terminates_even_with_overlap_larger_than_size():
    assert kb.chunk_text("x" * 500, size=50, overlap=500)
