"""
chunker.py — Chunks text to fit within token limits (~1500 tokens / chunk).
Ensures no chunk exceeds max_tokens and no text is lost.
"""
import re

APPROX_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count for text (rough heuristic: ~4 chars per token)."""
    return len(text) // APPROX_CHARS_PER_TOKEN + (1 if len(text) % APPROX_CHARS_PER_TOKEN else 0)


def chunk_text(text: str, max_tokens: int = 1500) -> list[str]:
    """
    Split text into chunks where each chunk is <= max_tokens.
    Preserves all text without dropping characters/words.
    """
    if not text or not text.strip():
        return []
    
    max_chars = max_tokens * APPROX_CHARS_PER_TOKEN
    
    if len(text) <= max_chars:
        return [text]

    # Split hierarchy: paragraphs -> lines -> sentences -> words
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_chunk = ""

    for paragraph in paragraphs:
        # Re-add paragraph separator if not first paragraph in current_chunk
        candidate = (current_chunk + "\n\n" + paragraph) if current_chunk else paragraph
        
        if len(candidate) <= max_chars:
            current_chunk = candidate
        else:
            # Paragraph itself might be bigger than max_chars, split paragraph by lines/sentences
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            
            sub_chunks = _split_large_text(paragraph, max_chars)
            for sc in sub_chunks:
                if current_chunk:
                    if len(current_chunk) + 1 + len(sc) <= max_chars:
                        current_chunk += " " + sc
                    else:
                        chunks.append(current_chunk)
                        current_chunk = sc
                else:
                    current_chunk = sc

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _split_large_text(text: str, max_chars: int) -> list[str]:
    """Helper to split large paragraph by lines or sentences or words."""
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    if len(lines) > 1:
        res = []
        curr = ""
        for line in lines:
            cand = (curr + "\n" + line) if curr else line
            if len(cand) <= max_chars:
                curr = cand
            else:
                if curr:
                    res.append(curr)
                sub = _split_large_text(line, max_chars)
                res.extend(sub[:-1])
                curr = sub[-1] if sub else ""
        if curr:
            res.append(curr)
        return res

    # Split by sentence enders (. ! ?)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) > 1:
        res = []
        curr = ""
        for sentence in sentences:
            cand = (curr + " " + sentence) if curr else sentence
            if len(cand) <= max_chars:
                curr = cand
            else:
                if curr:
                    res.append(curr)
                sub = _split_large_text(sentence, max_chars)
                res.extend(sub[:-1])
                curr = sub[-1] if sub else ""
        if curr:
            res.append(curr)
        return res

    # Hard split by words
    words = text.split(" ")
    res = []
    curr = ""
    for w in words:
        cand = (curr + " " + w) if curr else w
        if len(cand) <= max_chars:
            curr = cand
        else:
            if curr:
                res.append(curr)
            curr = w
    if curr:
        res.append(curr)
    return res
