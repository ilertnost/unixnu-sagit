# Legacy layout (kept for reference)

The format-1 device tree that used to live here (`kernel/sagit/`) was moved
into the format-2 root layout: board.toml, configs/, dts/, soc/, patches/xnu/series.
Everything in old/ is historical and unused by the current build scripts;
it is removed once the first official-path build verifies byte-equal outputs.
