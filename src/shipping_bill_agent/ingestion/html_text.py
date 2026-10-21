from __future__ import annotations

from html.parser import HTMLParser


class HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {"br", "div", "p", "tr", "li", "h1", "h2", "h3", "h4", "table"}
    CELL_TAGS = {"td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.CELL_TAGS:
            self.parts.append(" | ")
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        lines = []
        for line in "".join(self.parts).splitlines():
            cleaned = " ".join(line.replace("|  |", "|").split())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)
