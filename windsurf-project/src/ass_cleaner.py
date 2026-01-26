import re
from pathlib import Path


def clean_ass_line(text: str) -> str:
    text = re.sub(r'\{[^}]*\\p[1-9][^}]*\}.*', '', text)
    text = re.sub(r'\{[^}]+\}', '', text)
    return text.strip()


def _parse_format_fields(format_line: str):
    prefix = 'format:'
    if not format_line.lower().startswith(prefix):
        return None
    fields = [f.strip() for f in format_line[len(prefix):].strip().split(',')]
    return fields


def _has_default_style(lines) -> bool:
    in_styles = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == '[v4+ styles]':
            in_styles = True
            continue

        if in_styles and stripped.startswith('[') and stripped.lower() != '[v4+ styles]':
            break

        if in_styles and stripped.lower().startswith('style:'):
            rest = stripped[len('style:'):].strip()
            name = rest.split(',', 1)[0].strip()
            if name == 'Default':
                return True

    return False


def clean_ass_file(input_path: Path, output_path: Path) -> None:
    raw = input_path.read_bytes()
    try:
        content = raw.decode('utf-8-sig')
    except Exception:
        content = raw.decode('utf-8', errors='replace')

    lines = content.splitlines(keepends=True)

    if not _has_default_style(lines):
        raise ValueError('Missing Default style in [V4+ Styles]')

    in_events = False
    format_fields = None
    text_idx = None
    style_idx = None

    out_lines = []
    for line in lines:
        line_ending = ''
        core = line
        if line.endswith('\r\n'):
            core = line[:-2]
            line_ending = '\r\n'
        elif line.endswith('\n'):
            core = line[:-1]
            line_ending = '\n'
        elif line.endswith('\r'):
            core = line[:-1]
            line_ending = '\r'

        stripped = core.strip()

        if stripped.lower() == '[events]':
            in_events = True
            out_lines.append(line)
            continue

        if in_events and stripped.startswith('[') and stripped.lower() != '[events]':
            in_events = False

        if in_events and stripped.lower().startswith('format:'):
            format_fields = _parse_format_fields(stripped)
            if format_fields:
                try:
                    text_idx = [f.lower() for f in format_fields].index('text')
                except ValueError:
                    text_idx = None
                try:
                    style_idx = [f.lower() for f in format_fields].index('style')
                except ValueError:
                    style_idx = None
            out_lines.append(line)
            continue

        if in_events and text_idx is not None and (
            stripped.lower().startswith('dialogue:') or stripped.lower().startswith('comment:')
        ):
            prefix_end = core.lower().find(':')
            prefix = core[: prefix_end + 1]
            payload = core[prefix_end + 1 :]
            leading_ws = payload[: len(payload) - len(payload.lstrip())]
            payload_stripped = payload.lstrip()

            parts = payload_stripped.split(',', len(format_fields) - 1)
            if len(parts) >= len(format_fields):
                if style_idx is not None:
                    style_value = parts[style_idx].strip()
                    if style_value != 'Default':
                        continue

                original_text = parts[text_idx]
                cleaned_text = clean_ass_line(original_text)
                if not cleaned_text:
                    continue
                parts[text_idx] = cleaned_text

                rebuilt_payload = ','.join(parts)

                out_lines.append(prefix + leading_ws + rebuilt_payload + line_ending)
                continue

        out_lines.append(line)

    output_path.write_text(''.join(out_lines), encoding='utf-8')
