#!/usr/bin/env python3
"""
a2l2ecu_v1.py  -  convert an ASAP2 (.a2l) file into an ME7Logger ".ecu" file.

Revision: 1

Usage examples
--------------
  python a2l2ecu_v1.py 06A906032TF_0040.A2L
  python a2l2ecu_v1.py 06A906032TF_0040.A2L -o my.ecu
  python a2l2ecu_v1.py 06A906032TF_0040.A2L --reference old.ecu
  python a2l2ecu_v1.py 06A906032TF_0040.A2L --reference old.ecu --restrict

What it does
------------
Reads every /begin MEASUREMENT block in the A2L and writes one line per
variable in the ME7Logger [Measurements] format:

  Name, {Alias}, Address, Size, Bitmask, {Unit}, S, I, A, B, {Comment}

  * Address  <- ECU_ADDRESS
  * Size     <- UBYTE/SBYTE = 1, UWORD/SWORD = 2  (4-byte types are skipped)
  * Bitmask  <- BIT_MASK (0x0000 if none)
  * S        <- 1 for signed types (SBYTE/SWORD), else 0
  * A, B, I  <- from the COMPU_METHOD, ME7Logger convention: phys = A*raw - B
                (ASAP2 RAT_FUNC gives raw = (b*phys + c)/(e*phys + f),
                 so A = f/b and B = c/b for the linear case). Pure 1/x
                 methods (raw = c/(e*phys)) become I=1: phys = A/(raw - B)
  * Unit     <- COMPU_METHOD unit string (normalised to ME7Logger style unless
                --raw-units: Grad C -> degC, U/min -> rpm, hPa -> mbar,
                kg/h -> g/s with A and B rescaled)
  * Comment  <- long identifier (description)
  * ARRAY_SIZE n entries are expanded to name_0 .. name_(n-1)

The A2L has no aliases, hardware/software numbers or engine ID. Those come
from --reference (an existing .ecu for the same ECU) or from the --hw/--sw/
--part/--swver/--engine options.
"""
import argparse
import os
import re
import sys

__version__ = "1"

TYPES = {
    "UBYTE": (1, 0), "SBYTE": (1, 1),
    "UWORD": (2, 0), "SWORD": (2, 1),
    "ULONG": (4, 0), "SLONG": (4, 1),
    "FLOAT32_IEEE": (4, 1), "FLOAT64_IEEE": (8, 1),
}
DTYPE_RE = "|".join(TYPES)

MEAS_RE = re.compile(r"/begin\s+MEASUREMENT(.*?)/end\s+MEASUREMENT", re.S)
CM_RE = re.compile(r"/begin\s+COMPU_METHOD(.*?)/end\s+COMPU_METHOD", re.S)
# name "description" DATATYPE conversion resolution accuracy lower upper
# (the description is matched up to the quote that is followed by the data
#  type, so stray quotes inside descriptions do not break parsing)
HEAD_RE = re.compile(
    r'\s*(\S+)\s+"(.*?)"\s+(' + DTYPE_RE + r')\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)',
    re.S)


def strip_c_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def tokens(s):
    return re.findall(r'"[^"]*"|\S+', s)


def parse_compu_methods(text):
    """name -> (type, unit, coeffs list)"""
    out = {}
    for body in CM_RE.findall(text):
        m = re.match(r'\s*(\S+)\s+"(.*?)"\s+(\S+)\s+"([^"]*)"\s+"([^"]*)"(.*)', body, re.S)
        if not m:
            continue
        name, _desc, ctype, _fmt, unit, rest = m.groups()
        coeffs = []
        c = re.search(r"COEFFS\s+((?:[-+0-9.eE]+\s+){5}[-+0-9.eE]+)", rest)
        if c:
            coeffs = [float(x) for x in c.group(1).split()]
        out[name] = (ctype, unit, coeffs)
    return out


def conversion(cm):
    """Return (A, B, I) or None if the conversion is not supported.

    I=0: phys = A*raw - B          (linear)
    I=1: phys = A/(raw - B)        (inverse, e.g. raw = 128/phys -> A=128, B=0)
    """
    if cm is None:
        return 1.0, 0.0, 0
    ctype, _unit, co = cm
    if ctype == "RAT_FUNC" and len(co) == 6:
        a, b, c, d, e, f = co
        if a == 0 and d == 0 and e == 0 and b != 0:
            return f / b, c / b, 0
        if a == 0 and d == 0 and b == 0 and f == 0 and e != 0:
            return c / e, 0.0, 1
        return None
    if ctype in ("TAB_VERB", "TAB_INTP", "TAB_NOINTP"):
        return 1.0, 0.0, 0       # value/text tables: log the raw value
    return None


def parse_measurements(text, cms, warn):
    text = strip_c_comments(text)
    rows = []
    for body in MEAS_RE.findall(text):
        h = HEAD_RE.match(body)
        if not h:
            warn("could not parse a MEASUREMENT block: %r" % body[:60].strip())
            continue
        name, desc, dtype, conv = h.group(1), h.group(2), h.group(3), h.group(4)
        rest = body[h.end():]
        addr = re.search(r"ECU_ADDRESS\s+(0[xX][0-9A-Fa-f]+|\d+)", rest)
        if not addr:
            continue
        addr = int(addr.group(1), 0)
        mask = re.search(r"BIT_MASK\s+(0[xX][0-9A-Fa-f]+|\d+)", rest)
        mask = int(mask.group(1), 0) if mask else 0
        arr = re.search(r"ARRAY_SIZE\s+(\d+)", rest)
        arr = int(arr.group(1)) if arr else 0
        size, signed = TYPES[dtype]
        cm = cms.get(conv)
        ab = conversion(cm)
        if ab is None:
            warn("%s: unsupported conversion %s, using A=1 B=0" % (name, conv))
            ab = (1.0, 0.0, 0)
        unit = cm[1] if cm else ""
        desc = re.sub(r"[\r\n]+\s*", " ", desc.replace('""', '"')).strip()
        rows.append(dict(name=name, addr=addr, size=size, mask=mask, unit=unit,
                         signed=signed, A=ab[0], B=ab[1], inv=ab[2], comment=desc, arr=arr))
    return rows


def expand_arrays(rows):
    out = []
    for r in rows:
        if r["arr"] and r["arr"] > 1:
            for i in range(r["arr"]):
                x = dict(r)
                x["name"] = "%s_%d" % (r["name"], i)
                x["addr"] = r["addr"] + i * r["size"]
                out.append(x)
        else:
            out.append(r)
    return out


# ME7Logger-style units (what ME7Info writes). kg/h is converted to g/s.
UNIT_MAP = {
    "grad KW": "\xb0KW", "Grad KW": "\xb0KW", "Grad C": "\xb0C",
    "U/min": "rpm", "Upm": "rpm", "U/min/s": "rpm/s", "hPa": "mbar",
}


def normalise_units(rows):
    for r in rows:
        if r["unit"] == "kg/h" and not r["inv"]:
            f = 1000.0 / 3600.0
            r["A"] *= f
            r["B"] *= f
            r["unit"] = "g/s"
        else:
            r["unit"] = UNIT_MAP.get(r["unit"], r["unit"])


def cfmt(x):
    """C-style %g with a 3-digit exponent (e.g. 3.05176e-005), like the originals."""
    s = "%g" % x
    m = re.match(r"^(.*e[+-])(\d+)$", s)
    if m:
        s = m.group(1) + m.group(2).zfill(3)
    if s == "-0":
        s = "0"
    return s


def fmt_line(r, alias=""):
    return "%-16s, %-34s, 0x%06X, %2d, %7s, %-10s, %d, %d,%13s,%7s, {%s}" % (
        r["name"], "{%s}" % alias, r["addr"], r["size"], "0x%04X" % r["mask"],
        "{%s}" % r["unit"], r["signed"], r["inv"], cfmt(r["A"]), cfmt(r["B"]), r["comment"])


def read_reference(path):
    """Return (communication_lines, identification_lines, aliases, raw_lines)."""
    raw = open(path, "rb").read().decode("latin-1").replace("\r\n", "\n").split("\n")
    sections, cur = {}, None
    aliases = {}
    for ln in raw:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s
            sections[cur] = []
            continue
        if cur in ("[Communication]", "[Identification]") and s and not s.startswith(";"):
            sections[cur].append(ln.rstrip())
        elif cur == "[Measurements]" and s and not s.startswith(";"):
            p = [x.strip() for x in ln.split(",", 10)]
            if len(p) >= 4:
                al = re.match(r"^\{(.*)\}$", p[1])
                if al and al.group(1):
                    try:
                        aliases[(p[0], int(p[2], 16))] = al.group(1)
                    except ValueError:
                        pass
    return sections.get("[Communication]"), sections.get("[Identification]"), aliases, raw


def reference_names(raw):
    names = set()
    inm = False
    for ln in raw:
        s = ln.strip()
        if s.startswith("["):
            inm = (s == "[Measurements]")
            continue
        if inm and s and not s.startswith(";"):
            names.add(s.split(",", 1)[0].strip())
    return names


def main():
    ap = argparse.ArgumentParser(description="Convert an A2L to an ME7Logger .ecu file")
    ap.add_argument("a2l")
    ap.add_argument("-o", "--output", help="output .ecu (default: <a2l name>.ecu)")
    ap.add_argument("--reference", help="existing .ecu to copy [Communication], "
                    "[Identification] and aliases from")
    ap.add_argument("--restrict", action="store_true",
                    help="only keep variables whose name is in --reference")
    ap.add_argument("--part", help="PartNumber, e.g. '06A906032TF '")
    ap.add_argument("--swver", help="SWVersion, e.g. 0040")
    ap.add_argument("--hw", help="HWNumber, e.g. 0261208708")
    ap.add_argument("--sw", help="SWNumber, e.g. 1037379543")
    ap.add_argument("--engine", help="EngineId, e.g. 'BOSCH 1.8l5VT  '")
    ap.add_argument("--raw-units", action="store_true",
                    help="keep the A2L unit strings (default: ME7Logger-style units, "
                    "e.g. Grad C -> \xb0C, U/min -> rpm, kg/h -> g/s)")
    ap.add_argument("--version", action="version", version="a2l2ecu_v%s" % __version__)
    a = ap.parse_args()

    warnings = []
    warn = warnings.append

    text = open(a.a2l, "rb").read().decode("latin-1")
    cms = parse_compu_methods(text)
    rows = expand_arrays(parse_measurements(text, cms, warn))
    if not a.raw_units:
        normalise_units(rows)

    comm = ident = None
    aliases = {}
    ref_names = None
    if a.reference:
        comm, ident, aliases, raw = read_reference(a.reference)
        ref_names = reference_names(raw)

    if a.restrict:
        if not ref_names:
            sys.exit("--restrict needs --reference")
        rows = [r for r in rows if r["name"] in ref_names]

    # drop unsupported sizes and exact duplicates
    seen, keep, skipped = set(), [], 0
    for r in rows:
        if r["size"] not in (1, 2):
            skipped += 1
            continue
        k = (r["name"], r["addr"], r["mask"])
        if k in seen:
            continue
        seen.add(k)
        keep.append(r)
    keep.sort(key=lambda r: (r["name"].lower(), r["name"], r["addr"], r["mask"]))

    base = os.path.splitext(os.path.basename(a.a2l))[0]
    m = re.match(r"^([0-9A-Z]{9,12}?)_(\w+)$", base)
    part = a.part if a.part is not None else (m.group(1) if m else base)
    swver = a.swver if a.swver is not None else (m.group(2) if m else "")

    out = []
    out.append(";")
    out.append("; ECU characteristics for logging with ME7Logger")
    out.append(";")
    out.append("; Generated by a2l2ecu_v%s from %s" % (__version__, os.path.basename(a.a2l)))
    out.append(";")
    out.append("; You can hand-edit this file to add new measurement variable definitions")
    out.append("; or to change existing definitions, e.g. to change a conversion formula.")
    out.append(";")
    out.append("")
    out.append("[Version]")
    out.append("Version           = 1.20")
    out.append("")
    out.append("[Communication]")
    out.extend(comm or [
        "Connect      = SLOW-0x01    ; Possible values: SLOW-0x01, FAST-0x10",
        "Communicate  = HM0          ; Possible values: HM0, HM2-0x10",
        "LogSpeed     = 56000        ; Possible values: 10400, 14400, 19200, 38400, 56000, 76800, 125000",
    ])
    out.append("[Identification]")
    if ident:
        idmap = {}
        for ln in ident:
            k, _, v = ln.partition("=")
            idmap[k.strip()] = v.strip()
    else:
        idmap = {}

    def ident_val(key, override, default=""):
        if override is not None:
            return "{%s}" % override
        return idmap.get(key, "{%s}" % default)
    out.append("HWNumber          = " + ident_val("HWNumber", a.hw))
    out.append("SWNumber          = " + ident_val("SWNumber", a.sw))
    out.append("PartNumber        = " + ident_val("PartNumber", a.part, part))
    out.append("SWVersion         = " + ident_val("SWVersion", a.swver, swver))
    out.append("EngineId          = " + ident_val("EngineId", a.engine))
    out.append("")
    out.append("[Measurements]")
    out.append("; Conversion factors:")
    out.append(";   S -> 0 = unsigned, 1 = signed value")
    out.append(";   I -> 0 = normal, 1 = inverse conversion")
    out.append(";   A -> factor")
    out.append(";   B -> offset")
    out.append("; Normal conversion:  phys = A * internal - B")
    out.append("; Inverse conversion: phys = A / (internal - B)")
    out.append("")
    out.append(";Name           , {Alias}                           , Address, Size, Bitmask, "
               "{Unit},    S, I,            A,      B, Comment")
    for r in keep:
        out.append(fmt_line(r, aliases.get((r["name"], r["addr"]), "")))
    out.append("")

    path = a.output or (os.path.splitext(a.a2l)[0] + ".ecu")
    with open(path, "wb") as f:
        f.write("\r\n".join(out).encode("latin-1", errors="replace"))

    print("wrote %s: %d measurements (%d skipped as 4/8-byte, %d compu methods parsed)"
          % (path, len(keep), skipped, len(cms)))
    if warnings:
        print("%d warning(s):" % len(warnings))
        for w in warnings[:20]:
            print("  " + w)
        if len(warnings) > 20:
            print("  ...")


if __name__ == "__main__":
    main()
