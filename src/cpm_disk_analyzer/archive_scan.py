"""Recursive CP/M archive inventory and conservative Z80-code archaeology."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

from .analyzer import analyze_image
from .containers import ImageFormatError, read_image
from .filesystem import extract_logical_file, group_directory_entries
from .layout import to_filesystem_order
from .profiles import get_profile

IMAGE_SUFFIXES = {".img", ".imd", ".dsk"}
SOURCE_SUFFIXES = {".asm", ".mac", ".prn", ".lst", ".inc", ".lib"}
BINARY_SUFFIXES = {".com"}
HEX_SUFFIXES = {".hex"}

_CONFIDENCE_RANK = {"none": 0, "suggestive": 1, "strong": 2, "very strong": 3}

# Used only to locate the opcode field of a source line. The list includes
# ordinary 8080 mnemonics so that a symbol named like a Z80 mnemonic in an
# operand (CALL OUTD, OUT OUTD, etc.) cannot be mistaken for an instruction.
_ASSEMBLY_MNEMONICS = {
    # Intel 8080
    "ACI", "ADC", "ADD", "ADI", "ANA", "ANI", "CALL", "CC", "CM", "CMA",
    "CMC", "CMP", "CNC", "CNZ", "CP", "CPE", "CPI", "CPO", "CZ", "DAA",
    "DAD", "DCR", "DCX", "DI", "EI", "HLT", "IN", "INR", "INX", "JC",
    "JM", "JMP", "JNC", "JNZ", "JP", "JPE", "JPO", "JZ", "LDA", "LDAX",
    "LHLD", "LXI", "MOV", "MVI", "NOP", "ORA", "ORI", "OUT", "PCHL", "POP",
    "PUSH", "RAL", "RAR", "RC", "RET", "RLC", "RM", "RNC", "RNZ", "RP",
    "RPE", "RPO", "RRC", "RST", "RZ", "SBB", "SBI", "SHLD", "SPHL", "STA",
    "STAX", "STC", "SUB", "SUI", "XCHG", "XRA", "XRI", "XTHL",
    # Z80 mnemonics/forms that matter to source parsing
    "BIT", "CPD", "CPDR", "CPIR", "DJNZ", "EX", "EXX", "IM", "IND", "INDR",
    "INI", "INIR", "LD", "LDD", "LDDR", "LDI", "LDIR", "NEG", "OTDR", "OTIR",
    "OUTD", "OUTI", "RES", "RETI", "RETN", "RL", "RLD", "RR", "RRD", "SET",
    "SLA", "SLL", "SRA", "SRL", "JR",
}
_ASSEMBLER_DIRECTIVES = {
    "ASEG", "CSEG", "DSEG", "DB", "DEFB", "DEFL", "DEFM", "DEFS", "DEFW",
    "DS", "DW", "ELSE", "END", "ENDIF", "ENDM", "EQU", "IF", "IFDEF",
    "IFNDEF", "INCLUDE", "MACRO", "ORG", "PUBLIC", "SET", "TITLE",
}
_Z80_BLOCK_MNEMONICS = {
    "LDIR", "LDDR", "LDI", "LDD", "CPIR", "CPDR", "CPD",
    "INIR", "INDR", "INI", "IND", "OTIR", "OTDR", "OUTI", "OUTD",
}
_Z80_SIMPLE_MNEMONICS = {
    "DJNZ", "EXX", "RLD", "RRD", "RETI", "RETN", "NEG",
    "BIT", "RES", "SET", "SLA", "SRA", "SRL", "SLL",
}
_LISTING_PREFIX_TOKEN = re.compile(r"^(?:[0-9]+|[0-9A-F]{2,6})$", re.I)
_REGISTER_IXIY = re.compile(r"(?<![A-Z0-9_])I[XY](?![A-Z0-9_])", re.I)
_REGISTER_AF_ALT = re.compile(r"\bAF\s*'", re.I)


# Flow-control opcodes whose lengths/targets we need to follow from a real
# entry point. Everything else uses the documented 8080 instruction length.
_THREE_BYTE = {
    0x01, 0x11, 0x21, 0x31,  # LXI
    0x22, 0x2A, 0x32, 0x3A,  # direct memory
    0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA,  # Jcc
    0xC3,  # JMP
    0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC,  # Ccc
    0xCD,  # CALL
}
_TWO_BYTE = {
    0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x36, 0x3E,  # MVI
    0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE,  # immediate ALU
    0xD3, 0xDB,  # OUT port, IN port
}
_CONDITIONAL_JUMPS = {0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA}
_CONDITIONAL_CALLS = {0xC4, 0xCC, 0xD4, 0xDC, 0xE4, 0xEC, 0xF4, 0xFC}
_CONDITIONAL_RETURNS = {0xC0, 0xC8, 0xD0, 0xD8, 0xE0, 0xE8, 0xF0, 0xF8}
_RST_OPCODES = {0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF}
_Z80_RELATIVE = {
    0x10: "DJNZ",
    0x18: "JR",
    0x20: "JR NZ",
    0x28: "JR Z",
    0x30: "JR NC",
    0x38: "JR C",
}

# ED-prefix instructions with clear documented Z80 semantics. Undefined ED
# combinations are deliberately excluded.
_KNOWN_ED = {
    0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B,
    0x4D, 0x4F, 0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x62, 0x63, 0x64, 0x65,
    0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F, 0x70, 0x71,
    0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x7B, 0x7C, 0x7D,
    0x7E, 0x7F,
    0xA0, 0xA1, 0xA2, 0xA3, 0xA8, 0xA9, 0xAA, 0xAB,
    0xB0, 0xB1, 0xB2, 0xB3, 0xB8, 0xB9, 0xBA, 0xBB,
}


def infer_density(path: Path, root: Path) -> str:
    """Infer the user's provenance bucket from directory names only."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    lowered = {part.lower().replace("_", "").replace("-", "") for part in parts[:-1]}
    if "singledensity" in lowered:
        return "single"
    if "doubledensity" in lowered:
        return "double"
    return "unknown"


def _source_instruction(code: str) -> str | None:
    """Return only the instruction field from an assembler source/listing line."""
    stripped = code.strip()
    if not stripped:
        return None
    tokens = stripped.replace("\t", " ").split()
    if not tokens:
        return None

    def word(token: str) -> str:
        return token.rstrip(":").upper()

    # A colon always marks the first field as a label.
    if tokens[0].endswith(":") and len(tokens) >= 2:
        return " ".join(tokens[1:])

    # A directive in the second field means the first token is a label even
    # when that label happens to be named like an instruction: OUTD EQU ...
    if len(tokens) >= 2 and word(tokens[1]) in _ASSEMBLER_DIRECTIVES:
        return " ".join(tokens[1:])

    # Plain source: mnemonic/directive first.
    if word(tokens[0]) in _ASSEMBLY_MNEMONICS or word(tokens[0]) in _ASSEMBLER_DIRECTIVES:
        return " ".join(tokens)

    # Plain source with an unadorned label.
    if len(tokens) >= 2:
        second = word(tokens[1])
        if second in _ASSEMBLY_MNEMONICS or second in _ASSEMBLER_DIRECTIVES:
            return " ".join(tokens[1:])

    # Common PRN/LST layout: line/address/object bytes followed by source.
    # Only accept a later mnemonic when every earlier token is numeric/hex-ish;
    # that prevents symbols in operands from being promoted to mnemonics.
    for index in range(1, len(tokens)):
        candidate = word(tokens[index])
        if candidate not in _ASSEMBLY_MNEMONICS and candidate not in _ASSEMBLER_DIRECTIVES:
            continue
        if all(_LISTING_PREFIX_TOKEN.fullmatch(token.rstrip(":")) for token in tokens[:index]):
            return " ".join(tokens[index:])
    return None


def _source_hit(instruction: str) -> tuple[str, str] | None:
    parts = instruction.strip().split(None, 1)
    if not parts:
        return None
    mnemonic = parts[0].upper()
    operands = parts[1] if len(parts) > 1 else ""
    upper_operands = operands.upper()

    if mnemonic == "CPI":
        # Intel 8080 CPI immediate is ordinary 8080 code. Z80 CPI is the
        # operand-less block compare/increment instruction.
        if not operands.strip():
            return "CPI", "Z80 operand-less block compare/increment"
        return None
    if mnemonic == "JR":
        return instruction.strip(), "Z80 relative jump"
    if mnemonic in _Z80_BLOCK_MNEMONICS:
        return mnemonic, "Z80 block/extended instruction"
    if mnemonic in _Z80_SIMPLE_MNEMONICS:
        return mnemonic, "Z80-only instruction"
    if mnemonic == "IM" and re.match(r"^\s*[012](?:\s|$)", operands):
        return instruction.strip(), "Z80 interrupt mode"
    if _REGISTER_IXIY.search(operands):
        return instruction.strip(), "IX/IY register"
    if _REGISTER_AF_ALT.search(operands):
        return instruction.strip(), "alternate AF register set"
    if mnemonic == "LD" and re.match(r"^\s*(?:A\s*,\s*[IR]|[IR]\s*,\s*A)(?:\s|$)", upper_operands):
        return instruction.strip(), "I/R register transfer"
    if mnemonic in {"ADC", "SBC"} and re.match(r"^\s*HL\s*,", upper_operands):
        return instruction.strip(), "16-bit ADC/SBC HL"
    if mnemonic == "IN" and re.match(r"^\s*[A-Z]\s*,\s*\(\s*C\s*\)", upper_operands):
        return instruction.strip(), "Z80 IN r,(C)"
    if mnemonic == "OUT" and re.match(r"^\s*\(\s*C\s*\)\s*,", upper_operands):
        return instruction.strip(), "Z80 OUT (C),r"
    if mnemonic in {"RL", "RR", "RLC", "RRC"} and operands.strip():
        # 8080 RLC/RRC are operand-less accumulator instructions; register or
        # memory operands imply the Z80 CB-prefix form.
        return instruction.strip(), "Z80 register rotate"
    return None


def source_z80_evidence(data: bytes, *, limit: int = 20) -> list[dict[str, Any]]:
    """Find explicit Z80-only constructs in assembly/listing source text."""
    text = data.rstrip(b"\x1a\x00").decode("latin-1", errors="replace")
    hits: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        code = raw_line.split(";", 1)[0]
        instruction = _source_instruction(code)
        if instruction is None:
            continue
        found = _source_hit(instruction)
        if found is None:
            continue
        display, description = found
        hits.append(
            {
                "kind": "source",
                "confidence": "very strong",
                "location": f"line {line_number}",
                "instruction": display,
                "detail": description,
                "source_line": raw_line.rstrip(),
            }
        )
        if len(hits) >= limit:
            break
    return hits


def _u16(data: bytes, index: int) -> int | None:
    if index + 2 >= len(data):
        return None
    return data[index + 1] | (data[index + 2] << 8)


def _relative_target(address: int, displacement: int) -> int:
    signed = displacement - 256 if displacement & 0x80 else displacement
    return (address + 2 + signed) & 0xFFFF


def _byte_text(data: bytes, index: int, length: int) -> str:
    return " ".join(f"{value:02X}" for value in data[index : index + length])


def binary_z80_evidence(
    data: bytes,
    *,
    origin: int = 0x0100,
    entry_points: Iterable[int] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Follow reachable code and report Z80-only opcode evidence.

    The walker uses documented Intel 8080 instruction lengths. Relative-branch
    opcodes are only suggestive because those byte values are undocumented
    one-byte 8080 opcodes; documented Z80 prefixes are stronger evidence.
    """
    if not data:
        return []
    entries = list(entry_points) if entry_points is not None else [origin]
    queue: deque[int] = deque(entries)
    visited: set[int] = set()
    hits: list[dict[str, Any]] = []
    max_steps = min(200_000, max(4096, len(data) * 8))
    steps = 0

    def enqueue(address: int) -> None:
        if origin <= address < origin + len(data) and address not in visited:
            queue.append(address)

    while queue and steps < max_steps and len(hits) < limit:
        address = queue.popleft()
        while origin <= address < origin + len(data) and address not in visited:
            index = address - origin
            opcode = data[index]
            visited.add(address)
            steps += 1
            if steps >= max_steps:
                break

            if opcode in _Z80_RELATIVE:
                if index + 1 >= len(data):
                    break
                mnemonic = _Z80_RELATIVE[opcode]
                target = _relative_target(address, data[index + 1])
                hits.append(
                    {
                        "kind": "binary",
                        "confidence": "suggestive",
                        "location": f"{address:04X}h",
                        "instruction": mnemonic,
                        "detail": f"reachable Z80 relative-branch opcode {opcode:02X}h",
                        "bytes": _byte_text(data, index, 2),
                    }
                )
                enqueue(target)
                if opcode == 0x18:
                    break
                address = (address + 2) & 0xFFFF
                continue

            if opcode in (0xDD, 0xFD):
                prefix_name = "IX (DDh)" if opcode == 0xDD else "IY (FDh)"
                next_byte = data[index + 1] if index + 1 < len(data) else None
                hits.append(
                    {
                        "kind": "binary",
                        "confidence": "strong",
                        "location": f"{address:04X}h",
                        "instruction": prefix_name,
                        "detail": (
                            "reachable Z80 index-register prefix"
                            + (f" before opcode {next_byte:02X}h" if next_byte is not None else "")
                        ),
                        "bytes": _byte_text(data, index, min(2, len(data) - index)),
                    }
                )
                break

            if opcode == 0xED:
                if index + 1 >= len(data):
                    break
                second = data[index + 1]
                if second in _KNOWN_ED:
                    hits.append(
                        {
                            "kind": "binary",
                            "confidence": "strong",
                            "location": f"{address:04X}h",
                            "instruction": f"ED {second:02X}",
                            "detail": "reachable documented Z80 ED-prefix instruction",
                            "bytes": _byte_text(data, index, 2),
                        }
                    )
                break

            if opcode == 0xCB:
                if index + 1 >= len(data):
                    break
                second = data[index + 1]
                hits.append(
                    {
                        "kind": "binary",
                        "confidence": "strong",
                        "location": f"{address:04X}h",
                        "instruction": f"CB {second:02X}",
                        "detail": "reachable Z80 rotate/shift/bit prefix",
                        "bytes": _byte_text(data, index, 2),
                    }
                )
                break

            length = 3 if opcode in _THREE_BYTE else 2 if opcode in _TWO_BYTE else 1
            if index + length > len(data):
                break

            if opcode == 0xC3:
                target = _u16(data, index)
                if target is not None:
                    enqueue(target)
                break
            if opcode in _CONDITIONAL_JUMPS:
                target = _u16(data, index)
                if target is not None:
                    enqueue(target)
                address += 3
                continue
            if opcode == 0xCD or opcode in _CONDITIONAL_CALLS:
                target = _u16(data, index)
                if target is not None:
                    enqueue(target)
                address += 3
                continue
            if opcode in _RST_OPCODES:
                enqueue(opcode & 0x38)
                address += 1
                continue
            if opcode in (0xC9, 0xE9, 0x76):
                break
            if opcode in _CONDITIONAL_RETURNS:
                address += 1
                continue

            address += length

    return hits


def intel_hex_image(data: bytes) -> tuple[bytes, int, int] | None:
    """Parse a small Intel HEX image and return (bytes, origin, entry)."""
    text = data.rstrip(b"\x1a\x00\r\n").decode("ascii", errors="ignore")
    memory: dict[int, int] = {}
    base = 0
    start: int | None = None
    saw_record = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            return None
        try:
            record = bytes.fromhex(line[1:])
        except ValueError:
            return None
        if len(record) < 5 or record[0] + 5 != len(record):
            return None
        if sum(record) & 0xFF:
            return None
        saw_record = True
        count = record[0]
        address = (record[1] << 8) | record[2]
        record_type = record[3]
        payload = record[4 : 4 + count]
        if record_type == 0x00:
            for offset, value in enumerate(payload):
                absolute = base + address + offset
                if 0 <= absolute <= 0xFFFF:
                    memory[absolute] = value
        elif record_type == 0x01:
            break
        elif record_type == 0x02 and len(payload) == 2:
            base = int.from_bytes(payload, "big") << 4
        elif record_type == 0x04 and len(payload) == 2:
            base = int.from_bytes(payload, "big") << 16
        elif record_type in (0x03, 0x05) and len(payload) == 4:
            start = int.from_bytes(payload, "big") & 0xFFFF

    if not saw_record or not memory:
        return None
    low, high = min(memory), max(memory)
    if high - low >= 0x10000:
        return None
    image = bytearray(high - low + 1)
    for address, value in memory.items():
        if low <= address <= high:
            image[address - low] = value
    return bytes(image), low, start if start is not None else low


def file_z80_evidence(name: str, data: bytes) -> list[dict[str, Any]]:
    suffix = Path(name).suffix.lower()
    if suffix in SOURCE_SUFFIXES:
        return source_z80_evidence(data)
    if suffix in BINARY_SUFFIXES:
        return binary_z80_evidence(data, origin=0x0100, entry_points=[0x0100])
    if suffix in HEX_SUFFIXES:
        parsed = intel_hex_image(data)
        if parsed is None:
            return []
        image, origin, entry = parsed
        return binary_z80_evidence(image, origin=origin, entry_points=[entry])
    return []


def _best_confidence(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "none"
    return max((hit["confidence"] for hit in hits), key=lambda value: _CONFIDENCE_RANK[value])


def _image_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def scan_archive(root: str | Path) -> dict[str, Any]:
    """Recursively inventory recognized CP/M images and inspect code for Z80 use."""
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(root_path)

    images: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    image_paths = _image_paths(root_path)

    for image_path in image_paths:
        relative = str(image_path.relative_to(root_path))
        density = infer_density(image_path, root_path)
        image_record: dict[str, Any] = {
            "path": relative,
            "density_bucket": density,
            "status": "unrecognized",
            "error": None,
            "sha256": None,
            "profile_id": None,
            "profile_name": None,
            "confidence": None,
            "score": None,
            "file_count": 0,
            "z80_file_count": 0,
            "z80_strong_file_count": 0,
            "system_z80_evidence": [],
        }
        try:
            result = analyze_image(image_path)
            image_record["sha256"] = result.sha256
            candidate = result.best_candidate
            if candidate is None:
                images.append(image_record)
                continue
            image_record.update(
                {
                    "status": "recognized",
                    "profile_id": candidate.profile_id,
                    "profile_name": candidate.profile_name,
                    "confidence": candidate.confidence,
                    "score": candidate.score,
                }
            )
            container = read_image(image_path)
            profile = get_profile(candidate.profile_id)
            logical_files = group_directory_entries(candidate.files)
            z80_count = 0
            strong_count = 0
            for logical_file in logical_files:
                try:
                    payload = extract_logical_file(
                        container.logical_data, profile, logical_file
                    )
                except (ValueError, OSError) as exc:
                    payload = b""
                    extraction_error = str(exc)
                else:
                    extraction_error = None
                hits = file_z80_evidence(logical_file.name, payload) if payload else []
                confidence = _best_confidence(hits)
                if hits:
                    z80_count += 1
                if _CONFIDENCE_RANK[confidence] >= _CONFIDENCE_RANK["strong"]:
                    strong_count += 1
                files.append(
                    {
                        "image_path": relative,
                        "density_bucket": density,
                        "profile_id": candidate.profile_id,
                        "profile_name": candidate.profile_name,
                        "profile_confidence": candidate.confidence,
                        "user": logical_file.user,
                        "name": logical_file.name,
                        "size": logical_file.estimated_size,
                        "attributes": logical_file.attribute_text,
                        "sha256": hashlib.sha256(payload).hexdigest() if payload else None,
                        "extraction_error": extraction_error,
                        "z80_confidence": confidence,
                        "z80_evidence": hits,
                    }
                )
            image_record["file_count"] = len(logical_files)
            image_record["z80_file_count"] = z80_count
            image_record["z80_strong_file_count"] = strong_count

            logical = to_filesystem_order(container.logical_data, profile)
            system_area = logical[: profile.directory_offset]
            image_record["system_z80_evidence"] = binary_z80_evidence(
                system_area, origin=0x0000, entry_points=[0x0000], limit=10
            )
        except (OSError, ImageFormatError, KeyError, ValueError) as exc:
            image_record["error"] = str(exc)
        images.append(image_record)

    name_counts = Counter(record["name"] for record in files)
    hash_counts = Counter(record["sha256"] for record in files if record["sha256"])
    for record in files:
        record["name_frequency"] = name_counts[record["name"]]
        digest = record["sha256"]
        record["hash_frequency"] = hash_counts[digest] if digest else 0

    recognized = [record for record in images if record["status"] == "recognized"]
    z80_images = [
        record
        for record in recognized
        if record["z80_file_count"] or record["system_z80_evidence"]
    ]
    strong_z80_images = [
        record
        for record in recognized
        if record["z80_strong_file_count"]
        or any(
            _CONFIDENCE_RANK[hit["confidence"]] >= _CONFIDENCE_RANK["strong"]
            for hit in record["system_z80_evidence"]
        )
    ]
    density_counts = Counter(record["density_bucket"] for record in images)
    summary = {
        "root": str(root_path),
        "images_seen": len(images),
        "images_recognized": len(recognized),
        "images_unrecognized": len(images) - len(recognized),
        "logical_files": len(files),
        "unique_file_names": len(name_counts),
        "unique_file_hashes": len(hash_counts),
        "z80_files": sum(1 for record in files if record["z80_evidence"]),
        "z80_strong_files": sum(
            1
            for record in files
            if _CONFIDENCE_RANK[record["z80_confidence"]] >= _CONFIDENCE_RANK["strong"]
        ),
        "z80_images": len(z80_images),
        "z80_strong_images": len(strong_z80_images),
        "density_buckets": dict(sorted(density_counts.items())),
    }
    rare_files = [
        {
            "name": name,
            "occurrences": count,
            "images": sorted(
                {record["image_path"] for record in files if record["name"] == name}
            ),
        }
        for name, count in sorted(name_counts.items())
        if count <= 2
    ]
    return {
        "summary": summary,
        "images": images,
        "files": files,
        "rare_files": rare_files,
    }


def write_scan_json(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _hit_text(hit: dict[str, Any]) -> str:
    extras = []
    if hit.get("bytes"):
        extras.append(f"bytes={hit['bytes']}")
    if hit.get("source_line"):
        extras.append(f"source={hit['source_line'].strip()}")
    extra = f"; {'; '.join(extras)}" if extras else ""
    return (
        f"{hit['location']} {hit['instruction']} "
        f"[{hit['confidence']}] ({hit['detail']}){extra}"
    )


def write_scan_csv(report: dict[str, Any], path: str | Path) -> None:
    columns = [
        "image_path",
        "density_bucket",
        "profile_id",
        "profile_name",
        "profile_confidence",
        "user",
        "name",
        "size",
        "attributes",
        "sha256",
        "name_frequency",
        "hash_frequency",
        "z80_confidence",
        "z80_hits",
        "extraction_error",
    ]
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for record in report["files"]:
            writer.writerow(
                {
                    **{
                        column: record.get(column)
                        for column in columns
                        if column != "z80_hits"
                    },
                    "z80_hits": "; ".join(
                        _hit_text(hit) for hit in record["z80_evidence"]
                    ),
                }
            )


def scan_summary_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"Archive: {summary['root']}",
        (
            f"Images: {summary['images_seen']} seen, "
            f"{summary['images_recognized']} recognized, "
            f"{summary['images_unrecognized']} unrecognized"
        ),
        (
            f"Files: {summary['logical_files']} logical files, "
            f"{summary['unique_file_names']} unique names, "
            f"{summary['unique_file_hashes']} unique contents"
        ),
        (
            f"Z80 evidence: {summary['z80_strong_files']} strong file(s), "
            f"{summary['z80_files'] - summary['z80_strong_files']} suggestive-only file(s); "
            f"{summary['z80_strong_images']} image(s) contain strong evidence"
        ),
    ]
    if summary["density_buckets"]:
        buckets = ", ".join(
            f"{name}={count}" for name, count in summary["density_buckets"].items()
        )
        lines.append(f"Density buckets: {buckets}")

    z80_files = [record for record in report["files"] if record["z80_evidence"]]
    if z80_files:
        lines.append("\nZ80 evidence:")
        for record in z80_files:
            first = max(
                record["z80_evidence"],
                key=lambda hit: _CONFIDENCE_RANK[hit["confidence"]],
            )
            lines.append(
                f"  {record['image_path']}: U{record['user']} {record['name']} - "
                f"{first['location']} {first['instruction']} [{first['confidence']}]"
            )

    system_images = [
        record for record in report["images"] if record["system_z80_evidence"]
    ]
    if system_images:
        lines.append("\nBoot/system-area Z80 evidence:")
        for record in system_images:
            first = max(
                record["system_z80_evidence"],
                key=lambda hit: _CONFIDENCE_RANK[hit["confidence"]],
            )
            lines.append(
                f"  {record['path']}: {first['location']} {first['instruction']} "
                f"[{first['confidence']}]"
            )
    return "\n".join(lines)
