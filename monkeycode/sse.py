from dataclasses import dataclass
from collections.abc import Iterable, Iterator

from monkeycode.errors import StreamParseError


@dataclass(frozen=True)
class SSEEvent:
    event: str | None
    data: str


def iter_sse_events(lines: Iterable[str | bytes]) -> Iterator[SSEEvent]:
    event_name: str | None = None
    data_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r\n")

        if line == "":
            if data_lines:
                yield SSEEvent(event=event_name, data="\n".join(data_lines))
            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if ":" not in line:
            raise StreamParseError(f"malformed SSE line: {line!r}")

        field, value = line.split(":", 1)
        if value.startswith(" "):
            value = value[1:]

        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
        elif field in {"id", "retry"}:
            continue
        else:
            continue

    if data_lines:
        yield SSEEvent(event=event_name, data="\n".join(data_lines))
