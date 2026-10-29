# The restore ramdisk

The chosen rootfs path is a ramdisk, not APFS-on-disk: XNU boots with a memory
disk as root, and whatever formats real APFS afterwards runs from inside.

So the first question was what Apple actually ships as a ramdisk. In 20H392 the
answer is better than expected.

## It is not a DMG

`RestoreRamDisk` in `BuildManifest.plist` points at `087-86719-047.dmg`,
106,954,779 bytes. It is not a UDIF image: there is no `koly` trailer and the
last 512 bytes are zero. It is an IMG4 container:

```
SEQUENCE
  UTF8  IM4P
  UTF8  rdsk
  UTF8  0
  OCTET 106954752 bytes        <- the payload
```

Header is 27 bytes; the payload is exactly 26,112 × 4096, and it starts with an
APFS container superblock at offset 32:

```
magic        NXSB
block_size   4096
block_count  26112
incompatible 0x2
```

Volume superblock (`APSB`) is at block 364:

```
fs_index  0
role      0
name      SydneyUpdate20H392.arm64CustomerRamDisk
```

So the ramdisk root is **already a raw APFS container**, uncompressed and
unencrypted. No DMG reader, no APFS writer, no LZFSE: strip 27 bytes and hand
the rest to the kernel. That removes the serialization problem entirely.

## What is inside

Mounted read-only with `apfs-fuse`, the volume root holds `root/`:

```
bin  dev  sbin  private  System  usr  var -> private/var
```

and `sbin` carries exactly the tools that make the plan self-hosting:

```
launchd  mount_apfs  newfs_apfs  fsck_apfs  apfs_hfs_convert  mount  reboot  halt
```

`newfs_apfs` is Apple's own formatter, built for this device. Once this image
runs as root, the APFS container on `userdata` no longer has to be produced on
a Linux host — it can be produced on the phone, by Apple's code, which is the
only implementation known to get it right.

## Consequences for the bridge

- The payload to load is the 106,954,752-byte APFS container, not a DMG.
- It needs 4 KiB alignment and must survive in memory untouched.
- `RestoreTrustCache` (5,569 bytes, from `087-86719-047.dmg.trustcache`) is a
  separate component; without it the binaries in the image will not execute
  under AMFI.

## The kernel, for contrast

`kernelcache.release.iphone10`, 18,213,846 bytes, is also IMG4 — `IM4P`,
type `krnl`, description `KernelCacheBuilder_release-2429.140.2` — but its
payload starts `62 76 78 32`, which is LZFSE (`bvx2`). That one does need a
decompressor, on the host or in the loader.
