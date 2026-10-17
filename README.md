# MI250X hybrid firmware

Makes an HPE/Cray MI250X (113-D65201) accept board ID 16 (Supermicro AOM-MCM-Q).

Personal use only. Don't share the output file.

Two modes. Try them in this order:

| Mode | Changes | Anti-rollback level (SPL) | If it doesn't boot |
|---|---|---|---|
| A (default) | ID tables (0x2e, 0x41) | stays 2 | flash your backup |
| B `--full` | ID tables + bootloader (0x01, 0x03) | 2 → 3 | may be permanent (risks 1–2) |

## Risks

Untested. Nobody has booted either mode.

1. **Anti-rollback fuse (mode B).** If the first boot fuses SPL 3, your original firmware will never boot again. Unknown whether it does.
2. **Permanent brick (mode B).** If risk 1 happens and mode B doesn't boot, only SPL 3 firmware will run. The only SPL 3 images are Supermicro/ZT's, made for MI250, not MI250X. The module may be dead for good.
3. **No boot (both).** The module stalls at ~30 W. Mode A mixes HPE's bootloader with Supermicro's newer tables, which nobody has tried. Flashing your backup fixes this, unless risk 2 happened.
4. **Bad flash.** A failed write or a bad clip contact leaves corrupt chips. Recoverable with the clip and your backups.
5. **Damage.** Clipping a powered module or using anything other than 3.3 V can kill the chip or the board.
6. **HPE systems.** Both modes drop HPE's descriptors for board IDs 3 and 15. The module may stop working in HPE/Cray systems until you flash the backup.

Flashing back your backup after a mode B boot doesn't add risk. It only shows whether risk 1 happened.

## Needs

- Pomona 5250 clip, Pico with `pico-serprog` (or CH347), `flashrom`, Python 3
- `D6521000V.075`, AMD's MI250 firmware with the board ID 16 tables. It's inside AMD's `amdfwflash` package. Download it and extract the file:

  ```
  wget https://repo.radeon.com/fwupdater/amdfwflash/latest/deb/pool/main/a/amdfwflash/amdfwflash_2.0.714.477_amd64.deb
  dpkg-deb -x amdfwflash_2.0.714.477_amd64.deb pkg
  cp pkg/opt/amdfwflash/ifwi/mu5/D6521000V.075 .
  ```

## 1. Wire

The module has two identical flash chips: SST26VF016B, SOIC-8, marked `26F016B`. They sit on the heatsink side next to the GPU frame, so the heatsink has to come off. Probably one per GPU die (unconfirmed), and normally both hold the same data. Back up and flash both. Pick one as chip 1 and the other as chip 2, and mark them so you don't mix them up later.

Module unpowered, out of the baseboard. 3.3 V only. Pin 1 is at the dot. Clip onto one chip at a time.

| Chip pin | Signal | Pico |
|---|---|---|
| 1 | CE# | GP5 |
| 2 | SO | GP4 |
| 3 | WP# | 3V3 |
| 4 | GND | GND |
| 5 | SI | GP3 |
| 6 | SCK | GP2 |
| 7 | HOLD# | 3V3 |
| 8 | VDD | 3V3 |

## 2. Back up both chips

Clip onto chip 1 and read it three times:

```
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r chip1_a.bin
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r chip1_b.bin
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r chip1_c.bin
sha256sum chip1_*.bin
```

All three hashes must match. If not, reseat the clip and read again.

Move the clip to chip 2 and do the same, naming the files `chip2_a.bin`, `chip2_b.bin`, `chip2_c.bin`.

`chip1_a.bin` and `chip2_a.bin` are your backups. Keep copies somewhere other than this computer.

CH347: `-p ch347_spi`.

## 3. Build

```
python3 -I make_hybrid.py --base chip1_a.bin --compare chip2_a.bin --donor D6521000V.075 --out hybrid.bin
```

| Option | What it is |
|---|---|
| `--base` | Your backup of chip 1. The output is this file with the copied parts replaced. |
| `--compare` | Your backup of chip 2. Optional. Only used to warn if the two chips differ. |
| `--donor` | `D6521000V.075` (download it as shown under Needs). The parts are copied from here. Checked by hash. |
| `--out` | The file to write. Must not exist yet. Your backups are never modified. |
| `--full` | Mode B. Leave it off for mode A. |

The script only writes a file. It doesn't flash anything.

It prints each check as `ok`, `WARN` or `FAIL`. Any `FAIL` stops it and nothing is written. Success ends with `Wrote hybrid.bin` and its sha256.

If it warns that the chips differ, build a second image for chip 2:

```
python3 -I make_hybrid.py --base chip2_a.bin --donor D6521000V.075 --out hybrid2.bin
```

Mode B (only if mode A failed): the same command with `--full` added and a new `--out` name, for example `--out hybrid_full.bin`.

## 4. Flash both chips

Clip onto chip 1:

```
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -w hybrid.bin
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r check.bin
sha256sum hybrid.bin check.bin
```

The write must say `VERIFIED` and both hashes must match.

Move the clip to chip 2 and run the same three commands. If you built `hybrid2.bin` in step 3, use it for chip 2 instead of `hybrid.bin`.

## 5. Test

Install, power up, check:

- Power: ~30 W = stuck, ~105 W+ = booting
- `lspci -nn | grep -i 1002`
- `dmesg | grep -i amdgpu`

If mode A doesn't boot: flash the backup (Undo), confirm it boots, then decide on mode B.

## Undo

Flash `chip1_a.bin` to chip 1 and `chip2_a.bin` to chip 2, as in step 4.
