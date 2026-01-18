import argparse
import os
import re
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class IdxInfo:
    idx_path: str
    sub_path: str
    palette: List[int]  # 16 entries, 0xRRGGBB
    first_timestamp: str
    first_filepos: int


@dataclass
class ParsedSubtitle:
    start_ms: int
    end_ms: int
    image: "Image.Image"


_TIMESTAMP_RE = re.compile(
    r"timestamp:\s*(?P<ts>\d\d:\d\d:\d\d:\d\d\d)\s*,\s*filepos:\s*(?P<pos>[0-9A-Fa-f]+)"
)


def _timestamp_to_ms(ts: str) -> int:
    # IDX timestamp is HH:MM:SS:mmm
    h, m, s, ms = (int(x) for x in ts.split(":"))
    return ((h * 3600 + m * 60 + s) * 1000) + ms


def parse_idx_entries(idx_path: str) -> Tuple[List[int], List[Tuple[int, int]]]:
    # Returns (palette16, entries) where entries are (start_ms, filepos)
    palette: Optional[List[int]] = None
    entries: List[Tuple[int, int]] = []

    with open(idx_path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.lower().startswith("palette:"):
                _, rest = line.split(":", 1)
                parts = [x.strip() for x in rest.split(",") if x.strip()]
                if len(parts) >= 16:
                    parts = parts[:16]
                    palette = [int(x, 16) for x in parts]
                continue

            m = _TIMESTAMP_RE.search(line)
            if m:
                ts = m.group("ts")
                pos = int(m.group("pos"), 16)
                entries.append((_timestamp_to_ms(ts), pos))

    if palette is None:
        raise ValueError("IDX did not contain a 'palette:' line")
    if not entries:
        raise ValueError("IDX did not contain any 'timestamp: ..., filepos: ...' entries")

    return palette, entries


def parse_idx(idx_path: str) -> IdxInfo:
    palette: Optional[List[int]] = None
    first_ts: Optional[str] = None
    first_pos: Optional[int] = None

    with open(idx_path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.lower().startswith("palette:"):
                _, rest = line.split(":", 1)
                entries = [x.strip() for x in rest.split(",") if x.strip()]
                if len(entries) >= 16:
                    entries = entries[:16]
                    palette = [int(x, 16) for x in entries]

            m = _TIMESTAMP_RE.search(line)
            if m and first_ts is None:
                first_ts = m.group("ts")
                first_pos = int(m.group("pos"), 16)

            if palette is not None and first_ts is not None:
                break

    if palette is None:
        raise ValueError("IDX did not contain a 'palette:' line")
    if first_ts is None or first_pos is None:
        raise ValueError("IDX did not contain any 'timestamp: ..., filepos: ...' entries")

    base, _ = os.path.splitext(idx_path)
    sub_path = base + ".sub"
    if not os.path.exists(sub_path):
        raise FileNotFoundError(f"Expected SUB next to IDX: {sub_path}")

    return IdxInfo(
        idx_path=idx_path,
        sub_path=sub_path,
        palette=palette,
        first_timestamp=first_ts,
        first_filepos=first_pos,
    )


def _find_next_start_code(data: bytes, start_code: bytes, start: int = 0) -> int:
    return data.find(start_code, start)


def _read_be16(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def _parse_pes_payload_for_private_stream_1(pes: bytes) -> bytes:
    # PES header parsing (MPEG2 style), then return payload.
    if len(pes) < 3:
        return b""
    b0 = pes[0]

    # MPEG-2 PES header starts with '10' in the first two bits
    if (b0 & 0xC0) == 0x80 and len(pes) >= 3:
        header_data_length = pes[2]
        start = 3 + header_data_length
        if start > len(pes):
            return b""
        return pes[start:]

    # Fallback: treat whole thing as payload
    return pes


def extract_first_spu_from_sub(sub_path: str, filepos: int) -> bytes:
    return extract_spu_from_sub(sub_path, filepos)


def extract_spu_from_sub(sub_path: str, filepos: int, scan_bytes: int = 1024 * 1024) -> bytes:
    # VobSub filepos usually points at a pack boundary inside the SUB.
    # We scan forward from there to find the first private_stream_1 PES (0x000001BD)
    # and then assemble the complete SPU (may span multiple PES packets).
    START_CODE_PES = b"\x00\x00\x01\xbd"

    with open(sub_path, "rb") as f:
        f.seek(filepos)
        window = f.read(scan_bytes)

    pos = _find_next_start_code(window, START_CODE_PES, 0)
    if pos < 0:
        raise ValueError("Could not find PES start code 0x000001BD after filepos")

    def read_one_pes(at: int) -> Tuple[bytes, int]:
        # Returns (payload, end_offset_in_window)
        if at + 6 > len(window):
            raise ValueError("Truncated PES header")
        # window[at:at+4] == start code, window[at+4] == stream id (0xBD)
        pes_len = _read_be16(window, at + 4)
        pes_start = at + 6
        pes_end = pes_start + pes_len
        if pes_end > len(window):
            raise ValueError("PES packet extends beyond scan window")
        pes = window[pes_start:pes_end]
        payload = _parse_pes_payload_for_private_stream_1(pes)
        return payload, pes_end

    payload, pes_end = read_one_pes(pos)
    if not payload:
        raise ValueError("Empty PES payload")

    substream_id = payload[0]
    if not (0x20 <= substream_id <= 0x3F):
        raise ValueError(f"Unexpected substream id 0x{substream_id:02x} (expected 0x20..0x3f)")

    spu_bytes = bytearray(payload[1:])
    if len(spu_bytes) < 2:
        raise ValueError("Not enough data for SPU size")

    total_size = _read_be16(spu_bytes, 0)
    if total_size < 6 or total_size > 0xFFFF:
        raise ValueError(f"Implausible SPU size: {total_size}")

    scan_pos = pes_end
    while len(spu_bytes) < total_size:
        next_pes = _find_next_start_code(window, START_CODE_PES, scan_pos)
        if next_pes < 0:
            raise ValueError("SPU appears to span beyond scan window; increase scan size")
        payload2, pes_end2 = read_one_pes(next_pes)
        scan_pos = pes_end2
        if not payload2:
            continue
        if payload2[0] != substream_id:
            continue
        spu_bytes.extend(payload2[1:])

    return bytes(spu_bytes[:total_size])


def _parse_spu_display_duration_ms(spu: bytes) -> Optional[int]:
    # Control sequence dates are in 1/100th second relative to subtitle start.
    # We try to find a sequence that includes STP_DSP (0x02) and use its date.
    if len(spu) < 6:
        return None

    s0 = _read_be16(spu, 2)
    if s0 <= 4 or s0 >= len(spu):
        return None

    ctrl_pos = s0
    if ctrl_pos + 4 > len(spu):
        return None

    visited: set[int] = set()
    cmd_seq_pos = ctrl_pos
    stop_date_cs: Optional[int] = None

    while True:
        if cmd_seq_pos in visited:
            break
        visited.add(cmd_seq_pos)
        if cmd_seq_pos + 4 > len(spu):
            break

        date_cs = _read_be16(spu, cmd_seq_pos)
        next_cmd_pos = _read_be16(spu, cmd_seq_pos + 2)
        cmd_pos = cmd_seq_pos + 4
        saw_stop = False

        while cmd_pos < len(spu):
            cmd = spu[cmd_pos]
            cmd_pos += 1
            if cmd == 0xFF:
                break
            if cmd == 0x02:
                saw_stop = True
                continue
            if cmd in (0x00, 0x01):
                continue
            if cmd in (0x03, 0x04):
                cmd_pos += 2
                continue
            if cmd == 0x05:
                cmd_pos += 6
                continue
            if cmd == 0x06:
                cmd_pos += 4
                continue
            if cmd == 0x07:
                if cmd_pos + 2 > len(spu):
                    break
                sz = _read_be16(spu, cmd_pos)
                cmd_pos += 2
                if sz < 2:
                    break
                cmd_pos = min(len(spu), cmd_pos + (sz - 2))
                continue
            break

        if saw_stop:
            stop_date_cs = date_cs
            break

        if next_cmd_pos == cmd_seq_pos:
            break
        if next_cmd_pos < ctrl_pos or next_cmd_pos >= len(spu):
            break
        cmd_seq_pos = next_cmd_pos

    if stop_date_cs is None:
        return None
    return stop_date_cs * 10


def parseSub(sub_path: str, idx_path: Optional[str] = None) -> List[ParsedSubtitle]:
    # Returns PIL images and start/end times in milliseconds.
    try:
        from PIL import Image  # type: ignore
    except Exception as e:
        raise ImportError("parseSub requires Pillow (pip install Pillow)") from e

    sub_path = os.path.abspath(sub_path)
    if idx_path is None:
        base, ext = os.path.splitext(sub_path)
        if ext.lower() == ".idx":
            idx_path = sub_path
            sub_path = base + ".sub"
        else:
            idx_path = base + ".idx"

    idx_path = os.path.abspath(idx_path)
    if not os.path.exists(idx_path):
        raise FileNotFoundError(f"IDX not found: {idx_path}")
    if not os.path.exists(sub_path):
        raise FileNotFoundError(f"SUB not found: {sub_path}")

    palette16, entries = parse_idx_entries(idx_path)
    subtitles: List[ParsedSubtitle] = []

    for i, (start_ms, filepos) in enumerate(entries):
        spu = extract_spu_from_sub(sub_path, filepos)
        rgba, _ = decode_spu_to_rgba_image(spu, palette16)

        h = len(rgba)
        w = len(rgba[0]) if h else 0
        raw = bytearray()
        for y in range(h):
            for x in range(w):
                r, g, b, a = rgba[y][x]
                raw.extend([r, g, b, a])
        img = Image.frombytes("RGBA", (w, h), bytes(raw))

        dur_ms = _parse_spu_display_duration_ms(spu)
        if dur_ms is not None and dur_ms > 0:
            end_ms = start_ms + dur_ms
        elif i < len(entries) - 1:
            end_ms = entries[i + 1][0]
        else:
            end_ms = start_ms + 3000

        subtitles.append(ParsedSubtitle(start_ms=start_ms, end_ms=end_ms, image=img))

    return subtitles


def _decode_run_2bit_nibbles(get_nibble, w_remaining: int) -> Tuple[int, int]:
    # Implements FFmpeg's decode_run_2bit logic using nibble reads.
    v = 0
    t = 1
    while v < t and t <= 0x40:
        v = (v << 4) | get_nibble()
        t <<= 2
    color = v & 0x03
    if v < 4:
        return w_remaining, color  # fill rest of line
    return (v >> 2), color


def _decode_field_rle_2bit(spu: bytes, start: int, w: int, h: int) -> List[List[int]]:
    # Returns h lines of w pixels each, with values 0..3.
    if start >= len(spu):
        raise ValueError("RLE start offset beyond SPU")

    bitpos = start * 8

    def get_nibble() -> int:
        nonlocal bitpos
        byte_index = bitpos >> 3
        if byte_index >= len(spu):
            raise ValueError("RLE decode ran past end of SPU")
        b = spu[byte_index]
        if (bitpos & 7) == 0:
            nib = (b >> 4) & 0x0F
        else:
            nib = b & 0x0F
        bitpos += 4
        return nib

    def align_to_byte() -> None:
        nonlocal bitpos
        mod = bitpos & 7
        if mod != 0:
            bitpos += (8 - mod)

    out: List[List[int]] = []
    for _ in range(h):
        line = [0] * w
        x = 0
        while x < w:
            run_len, color = _decode_run_2bit_nibbles(get_nibble, w - x)
            run_len = min(run_len, w - x)
            if run_len < 0:
                raise ValueError("Negative run length")
            if run_len:
                line[x : x + run_len] = [color] * run_len
                x += run_len
        align_to_byte()
        out.append(line)

    return out


def decode_spu_to_rgba_image(
    spu: bytes,
    palette16: List[int],
) -> Tuple[List[List[Tuple[int, int, int, int]]], Tuple[int, int, int, int]]:
    # Parse SPU layout according to:
    # - first 2 bytes: total size
    # - next 2 bytes: S0 (data packet size)
    if len(spu) < 6:
        raise ValueError("SPU too short")

    s0 = _read_be16(spu, 2)
    if s0 <= 4 or s0 >= len(spu):
        raise ValueError("Invalid data packet size (S0)")

    ctrl_pos = s0

    # Defaults if commands are missing
    colormap = [0, 1, 2, 3]  # map pixel values 0..3 to palette indices 0..15
    alpha_nibble = [0, 0xF, 0xF, 0xF]  # 0=transparent, F=opaque
    x1 = y1 = 0
    x2 = y2 = 0
    offset_top = -1
    offset_bottom = -1

    # Parse control sequences. Some discs put SET_DSPXA in the "start display" sequence;
    # the first sequence may contain only a few no-arg commands.
    if ctrl_pos + 4 > len(spu):
        raise ValueError("Control packet truncated")

    cmd_seq_pos = ctrl_pos
    visited: set[int] = set()
    while True:
        if cmd_seq_pos in visited:
            break
        visited.add(cmd_seq_pos)
        if cmd_seq_pos + 4 > len(spu):
            break

        _date = _read_be16(spu, cmd_seq_pos)
        next_cmd_pos = _read_be16(spu, cmd_seq_pos + 2)
        cmd_pos = cmd_seq_pos + 4

        while cmd_pos < len(spu):
            cmd = spu[cmd_pos]
            cmd_pos += 1
            if cmd == 0xFF:
                break

            if cmd in (0x00, 0x01, 0x02):
                # FSTA_DSP / STA_DSP / STP_DSP : no args
                continue

            if cmd == 0x03:  # SET_COLOR
                if cmd_pos + 2 > len(spu):
                    break
                b1 = spu[cmd_pos]
                b2 = spu[cmd_pos + 1]
                cmd_pos += 2
                # Command order is e2 e1 p b (one nibble each), but pixel values are:
                # 0=background, 1=pattern, 2=emphasis1, 3=emphasis2
                e2 = (b1 >> 4) & 0xF
                e1 = b1 & 0xF
                p = (b2 >> 4) & 0xF
                bg = b2 & 0xF
                colormap = [bg, p, e1, e2]
                continue

            if cmd == 0x04:  # SET_CONTR
                if cmd_pos + 2 > len(spu):
                    break
                b1 = spu[cmd_pos]
                b2 = spu[cmd_pos + 1]
                cmd_pos += 2
                # Command order is e2 e1 p b; reorder to match pixel values 0..3.
                e2 = (b1 >> 4) & 0xF
                e1 = b1 & 0xF
                p = (b2 >> 4) & 0xF
                bg = b2 & 0xF
                alpha_nibble = [bg, p, e1, e2]
                continue

            if cmd == 0x05:  # SET_DAREA
                if cmd_pos + 6 > len(spu):
                    break
                b = spu[cmd_pos : cmd_pos + 6]
                cmd_pos += 6
                x1 = (b[0] << 4) | (b[1] >> 4)
                x2 = ((b[1] & 0x0F) << 8) | b[2]
                y1 = (b[3] << 4) | (b[4] >> 4)
                y2 = ((b[4] & 0x0F) << 8) | b[5]
                continue

            if cmd == 0x06:  # SET_DSPXA
                if cmd_pos + 4 > len(spu):
                    break
                offset_top = _read_be16(spu, cmd_pos)
                offset_bottom = _read_be16(spu, cmd_pos + 2)
                cmd_pos += 4
                continue

            if cmd == 0x07:  # CHG_COLCON
                # [2 bytes size including the size word] then variable params
                if cmd_pos + 2 > len(spu):
                    break
                sz = _read_be16(spu, cmd_pos)
                cmd_pos += 2
                # sz includes its own 2 bytes
                if sz < 2:
                    break
                skip = sz - 2
                cmd_pos = min(len(spu), cmd_pos + skip)
                continue

            # Unknown command: cannot safely skip without spec.
            break

        if next_cmd_pos == cmd_seq_pos:
            break
        if next_cmd_pos < ctrl_pos or next_cmd_pos >= len(spu):
            break
        cmd_seq_pos = next_cmd_pos

    if x2 < x1 or y2 < y1:
        raise ValueError("Invalid display area")
    w = x2 - x1 + 1
    h = y2 - y1 + 1

    if offset_top < 0 or offset_bottom < 0:
        raise ValueError("Missing pixel data offsets (SET_DSPXA command 0x06)")

    top_h = (h + 1) // 2
    bottom_h = h // 2

    top_lines = _decode_field_rle_2bit(spu, offset_top, w, top_h)
    bottom_lines = _decode_field_rle_2bit(spu, offset_bottom, w, bottom_h)

    # Re-interlace into full image lines
    pixels_index: List[List[int]] = [[0] * w for _ in range(h)]
    for i, line in enumerate(top_lines):
        pixels_index[2 * i] = line
    for i, line in enumerate(bottom_lines):
        pixels_index[2 * i + 1] = line

    # Convert to RGBA
    rgba: List[List[Tuple[int, int, int, int]]] = []
    for y in range(h):
        row: List[Tuple[int, int, int, int]] = []
        for x in range(w):
            pv = pixels_index[y][x] & 3
            pal_idx = colormap[pv] & 0xF
            rgb = palette16[pal_idx]
            r = (rgb >> 16) & 0xFF
            g = (rgb >> 8) & 0xFF
            b = rgb & 0xFF
            a = (alpha_nibble[pv] & 0xF) * 17
            row.append((r, g, b, a))
        rgba.append(row)

    return rgba, (x1, y1, x2, y2)


def write_image(path: str, rgba: List[List[Tuple[int, int, int, int]]]) -> None:
    h = len(rgba)
    w = len(rgba[0]) if h else 0

    # Prefer PNG via Pillow if available, otherwise write a simple PPM (RGB only)
    try:
        from PIL import Image  # type: ignore

        raw = bytearray()
        for y in range(h):
            for x in range(w):
                r, g, b, a = rgba[y][x]
                raw.extend([r, g, b, a])
        img = Image.frombytes("RGBA", (w, h), bytes(raw))
        img.save(path)
    except Exception:
        # PPM (P6) without alpha: premultiply over black
        ppm_path = os.path.splitext(path)[0] + ".ppm"
        with open(ppm_path, "wb") as f:
            f.write(f"P6\n{w} {h}\n255\n".encode("ascii"))
            for y in range(h):
                for x in range(w):
                    r, g, b, a = rgba[y][x]
                    r = (r * a) // 255
                    g = (g * a) // 255
                    b = (b * a) // 255
                    f.write(bytes([r, g, b]))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Minimal VobSub (IDX+SUB) parser: extracts and renders the first subtitle to an image."
    )
    ap.add_argument("idx", help="Path to .idx file")
    ap.add_argument(
        "--out",
        default=None,
        help="Output image path (default: next to idx, first_subtitle.png)",
    )
    args = ap.parse_args()

    idx_path = os.path.abspath(args.idx)
    info = parse_idx(idx_path)

    out_path = args.out
    if out_path is None:
        out_path = os.path.join(os.path.dirname(idx_path), "first_subtitle.png")

    spu = extract_first_spu_from_sub(info.sub_path, info.first_filepos)
    rgba, (x1, y1, x2, y2) = decode_spu_to_rgba_image(spu, info.palette)
    write_image(out_path, rgba)

    print("IDX:", info.idx_path)
    print("SUB:", info.sub_path)
    print("First timestamp:", info.first_timestamp)
    print(f"Display area: x={x1}..{x2}, y={y1}..{y2}")
    print("Wrote:", out_path)
    print("Note: if Pillow isn't installed, a .ppm may have been written instead.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
