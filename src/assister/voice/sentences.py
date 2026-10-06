"""AI가 글자를 조금씩 내보내는 동안, 다 된 문장부터 목소리로 읽을 수 있게 잘라 준다."""

from __future__ import annotations

import re

MIN_SENTENCE_CHARS = 5
# 문장 끝 기호 뒤에 빈칸이 와야 끝으로 본다. '3.5'의 점 같은 것을 자르지 않기 위해서다.
SENTENCE_END = re.compile(r"[.!?…。]+(?=\s)|\n+")

MARKUP = re.compile(r"[*_`#>]+|^\s*[-•]\s+", re.MULTILINE)
# 그림 문자와 꾸밈 기호. 목소리로 읽을 수 없다.
SYMBOLS = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff‍️←-⇿⬀-⯿]"
)
SPACES = re.compile(r"\s+")


def speakable(text: str) -> str:
    """목소리로 읽을 글만 남긴다. 읽을 것이 없으면 빈 글."""
    cleaned = SYMBOLS.sub("", MARKUP.sub("", text))
    return SPACES.sub(" ", cleaned).strip()


class SentenceSplitter:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, piece: str) -> list[str]:
        """새로 온 글을 받아, 끝난 문장들을 돌려준다. 너무 짧은 문장은 다음 문장에 붙인다."""
        self._buffer += piece
        sentences: list[str] = []
        start = 0
        for match in SENTENCE_END.finditer(self._buffer):
            candidate = self._buffer[start : match.end()].strip()
            if len(candidate) < MIN_SENTENCE_CHARS and not match.group().startswith("\n"):
                continue
            if candidate:
                sentences.append(candidate)
            start = match.end()
        self._buffer = self._buffer[start:]
        return sentences

    def flush(self) -> list[str]:
        """글이 다 왔을 때 남은 부분을 내놓는다."""
        rest, self._buffer = self._buffer.strip(), ""
        return [rest] if rest else []
