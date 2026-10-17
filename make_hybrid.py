#!/usr/bin/env python3
"""
Build an MI250X hybrid image from your HPE dump and D6521000V.075.
Writes nothing unless all checks pass.

  default  copy 0x2e + 0x41 only (bootloader and SPL stay HPE)
  --full   copy 0x01 + 0x03 + 0x2e + 0x41 (bootloader SPL 2 -> 3)

    python3 -I make_hybrid.py --base chip1.bin --compare chip2.bin --donor D6521000V.075 --out hybrid.bin
"""
import argparse
import hashlib
import struct
import sys

FULL_SWAP = (0x01, 0x03, 0x2E, 0x41)
TABLES_SWAP = (0x2E, 0x41)
DONOR_SHA256 = "1e31dc75470d61a615cab054051d88bd9b08799db206bbcc2286e3d5eb46e3f0"  # D6521000V.075
IMAGE_SIZE = 2 * 1024 * 1024
TARGET_TID = 16
LINK_NAMES = {0x81: "WAFL", 0x41: "xGMI", 0x01: "HOST"}


class CheckFailed(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise CheckFailed(msg)
    print("  ok   " + msg)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def fletcher32(b):
    """AMD PSP directory checksum (same algorithm as coreboot amdfwtool)."""
    words = struct.unpack("<%dH" % (len(b) // 2), b)
    c0 = c1 = 0xFFFF
    for i in range(0, len(words), 359):
        for w in words[i:i + 359]:
            c0 += w
            c1 += c0
        c0 = (c0 & 0xFFFF) + (c0 >> 16)
        c1 = (c1 & 0xFFFF) + (c1 >> 16)
    c0 = (c0 & 0xFFFF) + (c0 >> 16)
    c1 = (c1 & 0xFFFF) + (c1 >> 16)
    return (c1 << 16) | c0


def parse_dir(d, name):
    """Return (dir_offset, dir_end, stored_checksum, {type: (loc, size)})."""
    hits = []
    i = d.find(b"$PSP")
    while i >= 0:
        hits.append(i)
        i = d.find(b"$PSP", i + 1)
    check(len(hits) == 1, "%s: exactly one $PSP directory (found %d)" % (name, len(hits)))
    o = hits[0]
    cks, cnt = struct.unpack_from("<II", d, o + 4)
    check(0 < cnt < 128, "%s: plausible entry count (%d)" % (name, cnt))
    end = o + 16 + 16 * cnt
    check(fletcher32(d[o + 8:end]) == cks,
          "%s: directory checksum valid (0x%08x)" % (name, cks))
    entries = {}
    for k in range(cnt):
        t, sz, loc = struct.unpack_from("<IIQ", d, o + 16 + 16 * k)
        entries.setdefault(t, []).append((loc, sz))
    return o, end, cks, entries


def blob_version(blob):
    return blob[0x60:0x64][::-1].hex()


def blob_spl(blob):
    """Security patch level (anti-rollback) at +0x4C of the $PS1 header."""
    return struct.unpack_from("<I", blob, 0x4C)[0]


def spl_table(d, entries):
    """Entry 0x55: {fw type: minimum SPL}, 32-byte records of (spl, type) after the header."""
    loc, sz = entries[0x55][0]
    body = d[loc + 0x100:loc + 0x100 + struct.unpack_from("<I", d, loc + 0x14)[0]]
    recs = {}
    for o in range(0, len(body) - 8, 0x20):
        spl, t = struct.unpack_from("<II", body, o)
        if spl or t:
            recs[t] = spl
    return recs


def find_tid_table(d, lo, hi):
    """Find the TOPOLOGY_ID -> key table: >=18 rows of 4 bytes whose first byte is the row index."""
    hits = [o for o in range(lo, hi - 72)
            if all(d[o + 4 * i] == i for i in range(18))]
    return hits


def decode_41(blob):
    """Return {tid: [4-byte entries]} from a type-0x41 topology blob."""
    bsz = struct.unpack_from("<I", blob, 0x14)[0]
    body = blob[0x100:0x100 + bsz]
    nrec = struct.unpack_from("<I", body, 0)[0]
    p, recs = 0x30, {}
    for _ in range(nrec):
        tid, n = struct.unpack_from("<II", body, p)
        p += 8
        recs[tid] = [body[p + 4 * k:p + 4 * k + 4] for k in range(n)]
        p += 4 * n
    return recs


def topo_summary(d, entries):
    loc, sz = entries[0x41][0]
    blob = d[loc:loc + sz]
    check(blob[0x10:0x14] == b"MI-2", "entry 0x41 has MI-2 magic")
    return decode_41(blob)


def tid_row(d, entries, label):
    loc, sz = entries[0x2E][0]
    hits = find_tid_table(d, loc, loc + sz)
    check(len(hits) == 1, "%s: exactly one TOPOLOGY_ID table inside entry 0x2e (@%s)"
          % (label, ", ".join(hex(h) for h in hits) or "none"))
    t = hits[0]
    return t, d[t + 4 * TARGET_TID:t + 4 * TARGET_TID + 4]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="your own dump of the HPE module (2 MiB)")
    ap.add_argument("--donor", required=True, help="AMD D6521000V.075")
    ap.add_argument("--out", required=True, help="output image")
    ap.add_argument("--compare", help="dump of the module's second SPI chip (optional)")
    ap.add_argument("--full", action="store_true", help="also copy the bootloader (0x01, 0x03); raises SPL 2 -> 3")
    a = ap.parse_args()
    SWAP_TYPES = FULL_SWAP if a.full else TABLES_SWAP

    base = open(a.base, "rb").read()
    donor = open(a.donor, "rb").read()
    try:
        print("Inputs")
        print("  base  %s  sha256 %s" % (a.base, sha256(base)))
        print("  donor %s  sha256 %s" % (a.donor, sha256(donor)))
        check(len(base) == IMAGE_SIZE, "base is exactly 2 MiB")
        check(sha256(donor) == DONOR_SHA256, "donor is AMD D6521000V.075 (sha256 match)")
        check(base.count(b"\xff") < len(base) * 0.9 and base.count(b"\x00") < len(base) * 0.9,
              "base is not blank / all-zero (read actually returned data)")
        if a.compare:
            other = open(a.compare, "rb").read()
            print("  chip2 %s  sha256 %s" % (a.compare, sha256(other)))
            check(len(other) == IMAGE_SIZE, "chip2 dump is exactly 2 MiB")
            if other == base:
                print("  ok   both chips are byte-identical")
            else:
                diff = sum(1 for x, y in zip(base, other) if x != y)
                print("  WARN chips differ in %d bytes - back up both, and build a hybrid from each" % diff)

        print("\nPSP directories")
        bo, bend, bcks, be = parse_dir(base, "base")
        do, dend, dcks, de = parse_dir(donor, "donor")

        print("\nSwap entries")
        for t in SWAP_TYPES:
            check(len(be.get(t, [])) == 1 and len(de.get(t, [])) == 1,
                  "entry 0x%02x present exactly once in both" % t)
            bl, bs = be[t][0]
            dl, ds = de[t][0]
            check((bl, bs) == (dl, ds),
                  "entry 0x%02x at same place and size in both (@0x%06x, 0x%x bytes)" % (t, bl, bs))
            check(bl + bs <= len(base) and dl + ds <= len(donor), "entry 0x%02x within both files" % t)
            check(not (bl < bend and bo < bl + bs), "entry 0x%02x does not overlap the directory" % t)
        ranges = sorted(be[t][0] for t in SWAP_TYPES)
        for (l1, s1), (l2, _) in zip(ranges, ranges[1:]):
            check(l1 + s1 <= l2, "swap ranges do not overlap (0x%06x..0x%06x)" % (l1, l1 + s1))
        for t in (0x01, 0x03, 0x2E):
            loc, sz = be[t][0]
            check(base[loc + 0x10:loc + 0x14] == b"$PS1" and donor[loc + 0x10:loc + 0x14] == b"$PS1",
                  "entry 0x%02x has $PS1 header in both" % t)

        print("\nVersions")
        bv = {t: blob_version(base[be[t][0][0]:]) for t in (0x01, 0x03, 0x2E)}
        dv = {t: blob_version(donor[de[t][0][0]:]) for t in (0x01, 0x03, 0x2E)}
        for t in (0x01, 0x03, 0x2E):
            print("       0x%02x  base %s   donor %s" % (t, bv[t], dv[t]))
        check(dv[0x01] == dv[0x2E] == dv[0x03], "donor 0x01/0x03/0x2e versions are paired")

        print("\nAnti-rollback (SPL)")
        bspl = {t: blob_spl(base[be[t][0][0]:]) for t in (0x01, 0x03, 0x2E)}
        dspl = {t: blob_spl(donor[de[t][0][0]:]) for t in (0x01, 0x03, 0x2E)}
        check(len(be.get(0x55, [])) == 1, "base has one SPL table (entry 0x55)")
        btab = spl_table(base, be)
        print("       base SPL %s   donor SPL %s   base SPL table %s"
              % (bspl[0x01], dspl[0x01], {hex(k): v for k, v in btab.items()}))
        check(len(set(bspl.values())) == 1 and len(set(dspl.values())) == 1,
              "SPL is the same across 0x01/0x03/0x2e in each image")
        if not a.full:
            check(not set(TABLES_SWAP) & set(btab), "copied entries are not covered by the SPL table")
            print("  ok   bootloader and SPL table stay base (SPL %d) - no SPL change expected" % bspl[0x01])
            if bv[0x01] != dv[0x2E]:
                print("  WARN base bootloader %s with donor 0x2e %s - untested pairing, may not boot"
                      % (bv[0x01], dv[0x2E]))
        else:
            for t in (0x01, 0x03):
                check(dspl[t] >= btab.get(t, 0),
                      "donor 0x%02x SPL %d meets the kept SPL table minimum %d" % (t, dspl[t], btab.get(t, 0)))
            check(dspl[0x01] >= bspl[0x01],
                  "donor SPL %d is not lower than base SPL %d (no rollback)" % (dspl[0x01], bspl[0x01]))
            if dspl[0x01] > bspl[0x01]:
                print("  WARN donor SPL %d > base SPL %d. If the PSP fuses SPL %d on boot, firmware with SPL %d "
                      "(your backup) will no longer boot." % (dspl[0x01], bspl[0x01], dspl[0x01], bspl[0x01]))

        print("\nBase: is it really the blocked HPE configuration?")
        bt, brow = tid_row(base, be, "base")
        print("       base TID16 row @0x%x: %s" % (bt + 4 * TARGET_TID, brow.hex(" ")))
        btopo = topo_summary(base, be)
        print("       base 0x41 TIDs: %s" % sorted(btopo))
        if brow[1] != 0 or TARGET_TID in btopo:
            raise CheckFailed("base already maps TID16 to a key or has a TID16 descriptor - "
                              "this is not the blocked HPE image; nothing to fix, stop and inspect")
        print("  ok   base rejects TID16 (key 0, no descriptor) - as expected for HPE firmware")

        print("\nBuild")
        out = bytearray(base)
        for t in SWAP_TYPES:
            loc, sz = be[t][0]
            out[loc:loc + sz] = donor[loc:loc + sz]
            print("       copied entry 0x%02x  0x%06x..0x%06x  sha256 %s"
                  % (t, loc, loc + sz, sha256(donor[loc:loc + sz])[:16]))
        out = bytes(out)

        print("\nVerify output")
        check(len(out) == len(base), "output size unchanged")
        oo, oend, ocks, oe = parse_dir(out, "output")
        check(out[bo:bend] == base[bo:bend], "PSP directory bytes unchanged")
        allowed = [(be[t][0][0], be[t][0][0] + be[t][0][1]) for t in SWAP_TYPES]
        stray = [i for i, (x, y) in enumerate(zip(base, out))
                 if x != y and not any(lo <= i < hi for lo, hi in allowed)]
        check(not stray, "no bytes changed outside the four swapped entries")
        for t in SWAP_TYPES:
            loc, sz = oe[t][0]
            check(out[loc:loc + sz] == donor[loc:loc + sz], "entry 0x%02x identical to donor" % t)
        changed = sum(1 for x, y in zip(base, out) if x != y)
        print("       %d bytes differ from base" % changed)
        ot, orow = tid_row(out, oe, "output")
        print("       output TID16 row @0x%x: %s" % (ot + 4 * TARGET_TID, orow.hex(" ")))
        check(orow[1] != 0, "output maps TID16 to a non-zero key")
        l1, l3 = oe[0x01][0][0], oe[0x03][0][0]
        body3 = out[l3 + 0x100:l3 + 0x100 + struct.unpack_from("<I", out, l3 + 0x14)[0]]
        check(out[l1 + 0xD0:l1 + 0xF0] == hashlib.sha256(body3).digest(),
              "bootloader 0x01 carries the SHA-256 of entry 0x03 it is paired with")
        otopo = topo_summary(out, oe)
        print("       output 0x41 TIDs: %s" % sorted(otopo))
        check(TARGET_TID in otopo, "output has a TID16 topology descriptor (%d entries)"
              % len(otopo.get(TARGET_TID, [])))
        kinds = {}
        for e in otopo[TARGET_TID]:
            if e[1] != 0xFF:
                kinds[LINK_NAMES.get(e[0], hex(e[0]))] = kinds.get(LINK_NAMES.get(e[0], hex(e[0])), 0) + 1
        print("       TID16 links: %s" % kinds)

    except CheckFailed as e:
        print("  FAIL " + str(e))
        print("\nNo output written.")
        return 1

    try:
        with open(a.out, "xb") as f:
            f.write(out)
    except FileExistsError:
        print("\n%s already exists - not overwriting. Choose a new --out path." % a.out)
        return 1
    print("\nWrote %s\n  sha256 %s" % (a.out, sha256(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
