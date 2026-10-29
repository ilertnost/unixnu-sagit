# Mi 6 (sagit) input wiring, from the vendor schematic

Source: `Xiaomi Mi 6 schematic.pdf` (41 pages, MSM8998 platform). Only facts
were taken; no vendor code. Values cross-checked against the board DTS in
`ios_sagit/device_trees/xiaomi-sagit.dts`, which agrees on every pin below.

## Fingerprint (the Home sensor)

| net | pin | note |
|---|---|---|
| `FP_LDO_EN` | TLMM `GPIO_35` | sensor rail enable |
| `FP_SPI_RST` | TLMM `GPIO_37` | reset, active low |
| `FP_INT_N` | TLMM `GPIO_121` | sensor notification, active low |
| `BLSP_SPI_MOSI12` | TLMM `GPIO_81` | SPI data out |
| `BLSP_SPI_MISO12` | TLMM `GPIO_82` | SPI data in |
| `BLSP_SPI_CS_N12` | TLMM `GPIO_83` | chip select |
| `BLSP_SPI_CLK12` | TLMM `GPIO_84` | SPI clock |

The SPI controller is **BLSP12**. The FPC firmware opens it as index 11
(zero-based), which is the same controller.

`FP_INT_N` is the sensor's "something happened" line. It is **not** a Home
press by itself: the sensor has to be asked over SPI, through the secure
world, what happened.

## Touchscreen

| net | pin |
|---|---|
| `TS_I2C_SDA` | TLMM `GPIO_87` |
| `TS_I2C_SCL` | TLMM `GPIO_88` |
| `TS_RESET_N` | TLMM `GPIO_89` |
| `TS_INT_N` | TLMM `GPIO_125` |

## Keys

| net | path |
|---|---|
| `KPD_PWR_N` / `PHONE_ON_N` | PM8998 PON block, not a TLMM GPIO |
| `VOL_UP_N` | PM8998 side, read through the PMIC |
| `VOL_DOWN` | PM8998 GPIO, line 6 |

Power and volume do **not** go through the SoC's TLMM block. They arrive over
SPMI from the PMIC, which is why the boot path has to bring up PM8998 before
any key can be read.

## What this means for Home

There is no mechanical Home switch on this board, and no dedicated Home GPIO.
The only physical thing that reports "a finger is on the Home area" is the
fingerprint sensor, and reaching it needs the secure world.

That is not a defect of the port. The iPhone 8 has no mechanical Home button
either: it is a solid-state sensor with a Taptic Engine. The `buttons` node in
the D20AP device tree declares `home-button-type = <1>`, i.e. the non-clicking
kind. So the device identity we target already expects a sensor, not a switch.
