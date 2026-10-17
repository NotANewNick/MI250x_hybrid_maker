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
- `D6521000V.075`:

  ```
  wget https://repo.radeon.com/fwupdater/amdfwflash/latest/deb/pool/main/a/amdfwflash/amdfwflash_2.0.714.477_amd64.deb
  dpkg-deb -x amdfwflash_2.0.714.477_amd64.deb pkg
  cp pkg/opt/amdfwflash/ifwi/mu5/D6521000V.075 .
  ```

## 1. Wire

Module unpowered, out of the baseboard. 3.3 V only. Pin 1 is at the dot.

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

```
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r chip1_a.bin
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r chip1_b.bin
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r chip1_c.bin
sha256sum chip1_*.bin
```

Hashes must match. Repeat for chip 2. Keep a copy elsewhere.

CH347: `-p ch347_spi`.

## 3. Build

Mode A:

```
python3 -I make_hybrid.py --base chip1_a.bin --compare chip2_a.bin --donor D6521000V.075 --out hybrid.bin
```

Mode B (only if mode A failed): add `--full` and use a new `--out` name.

Must end with `Wrote ...`. If the chips differ, also build one from `--base chip2_a.bin` for chip 2.

## 4. Flash both chips

```
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -w hybrid.bin
flashrom -p serprog:dev=/dev/ttyACM0:115200,spispeed=12M -c SST26VF016B -r check.bin
sha256sum hybrid.bin check.bin
```

Must say `VERIFIED` and hashes must match.

## 5. Test

Install, power up, check:

- Power: ~30 W = stuck, ~105 W+ = booting
- `lspci -nn | grep -i 1002`
- `dmesg | grep -i amdgpu`

If mode A doesn't boot: flash the backup (Undo), confirm it boots, then decide on mode B.

## Undo

Flash `chip1_a.bin` and `chip2_a.bin` back as in step 4.
