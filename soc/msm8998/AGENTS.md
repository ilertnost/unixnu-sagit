# MSM8998 SoC support ownership

This directory is a local, unpublished SoC profile under migration, not a
claimed official MSM8998 release. The migration inputs (`soc.toml`,
`configs/msm8998.config`, `patches/xnu/series`) originate from the OnePlus 5
(cheeseburger) format-2 migration by the same author; the device tree include
`dts/msm8998.dtsi` is written for this port and describes the Mi 6 4+4
big.LITTLE arrangement.

Keep board wiring, userland identity and partition policy in the device layer
(board.toml, dts/sagit.dts, package/). Do not borrow another board's
addresses, IDs or storage geometry. When a public UniXNU-msm8998-common
repository exists, these inputs move there and feeds.toml pins its commit.
