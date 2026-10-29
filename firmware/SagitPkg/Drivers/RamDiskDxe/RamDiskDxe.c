/** @file
  Publish the payload volume that the Android bootloader placed in RAM as a
  read-only block device, so that FatDxe can mount it and UniXNU can read its
  configuration and kernel.

  The boot image this firmware ships in is packed with a ramdisk; ABL copies it
  to the address in the header. Where that address is learned from depends on
  what the bootloader left behind, tried in this order:

  1. `linux,initrd-start`/`-end` in the device tree ABL handed over. edk2-msm8998
     stores the incoming tree pointer in the cell at PcdFdtStoreAddress
     (ArmPlatformPeiBootAction, the "oh my fdt" store), and that tree is the
     only one that describes this boot.
  2. The FDT configuration table, as a last resort. It normally holds the DTB
     DtPlatformDxe copied out of the firmware volume -- a tree that looks real
     but never carries this boot's initrd -- so the stashed pointer wins.
  3. A self-describing probe: read the FAT boot sector at PcdPayloadProbeAddress
     (the ramdisk offset our boot image declares) and take the volume size from
     its own header. This covers an ABL that copies the ramdisk but never
     writes the initrd bounds into the tree.

  Whichever way it resolves, the outcome is recorded in the boot-service
  variable "SagitRamDisk" so a UEFI Shell on the phone can answer what
  happened (dmpstore SagitRamDisk) -- there is no serial console.

  Copyright (c) 2026 ilertnost
  SPDX-License-Identifier: GPL-3.0-or-later
**/

#include <Uefi.h>

#include <Guid/Fdt.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DebugLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/PcdLib.h>
#include <Library/PrintLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiRuntimeServicesTableLib.h>

#include <Protocol/BlockIo.h>
#include <Protocol/DevicePath.h>
#include <Protocol/DiskIo.h>

#define FDT_MAGIC          0xd00dfeedU
#define FDT_BEGIN_NODE     1U
#define FDT_END_NODE       2U
#define FDT_PROP           3U
#define FDT_NOP            4U
#define FDT_END            9U

#define RAMDISK_BLOCK_SIZE 512U

// Boot-service only: it lives in memory for this boot, so the Shell can read
// it after a boot that went wrong, and nothing is ever written to flash.
#define PAYLOAD_STATUS_VARIABLE  (CHAR16 *)L"SagitRamDisk"

STATIC CONST EFI_GUID  mStatusGuid = {
  0xC0A1F5D4, 0x7E3B, 0x4C21,
  { 0x8F, 0x62, 0x1A, 0x55, 0x9D, 0x3E, 0x70, 0x11 }
};

#pragma pack(1)
typedef struct {
  VENDOR_DEVICE_PATH          Vendor;
  EFI_DEVICE_PATH_PROTOCOL    End;
} RAMDISK_DEVICE_PATH;
#pragma pack()

STATIC RAMDISK_DEVICE_PATH  mDevicePath = {
  {
    { HARDWARE_DEVICE_PATH, HW_VENDOR_DP, { (UINT8)sizeof (VENDOR_DEVICE_PATH), (UINT8)(sizeof (VENDOR_DEVICE_PATH) >> 8) } },
    { 0x5E6A1D42, 0x1C3B, 0x4A87, { 0x9F, 0x2E, 0x6B, 0x51, 0xC4, 0x0D, 0x77, 0xA8 } }
  },
  { END_DEVICE_PATH_TYPE, END_ENTIRE_DEVICE_PATH_SUBTYPE, { sizeof (EFI_DEVICE_PATH_PROTOCOL), 0 } }
};

STATIC UINT8                    *mRamBase;
STATIC EFI_BLOCK_IO_MEDIA       mMedia;

STATIC
EFI_STATUS
EFIAPI
RamDiskReset (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN BOOLEAN                ExtendedVerification
  );

STATIC
EFI_STATUS
EFIAPI
RamDiskReadBlocks (
  IN  EFI_BLOCK_IO_PROTOCOL  *This,
  IN  UINT32                 MediaId,
  IN  EFI_LBA                Lba,
  IN  UINTN                  BufferSize,
  OUT VOID                   *Buffer
  );

STATIC
EFI_STATUS
EFIAPI
RamDiskWriteBlocks (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN UINT32                 MediaId,
  IN UINT64                 Lba,
  IN UINTN                  BufferSize,
  IN VOID                   *Buffer
  );

STATIC
EFI_STATUS
EFIAPI
RamDiskFlushBlocks (
  IN EFI_BLOCK_IO_PROTOCOL  *This
  );

STATIC EFI_BLOCK_IO_PROTOCOL  mBlockIo = {
  EFI_BLOCK_IO_PROTOCOL_REVISION,
  &mMedia,
  RamDiskReset,
  RamDiskReadBlocks,
  RamDiskWriteBlocks,
  RamDiskFlushBlocks
};

/**
  FatDriverBindingSupported() refuses a handle that carries BlockIo but no
  DiskIo, so the block device alone would never mount. The payload is one
  contiguous RAM buffer, so DiskIo is a bounds-checked copy.
**/
STATIC
EFI_STATUS
EFIAPI
RamDiskReadDisk (
  IN  EFI_DISK_IO_PROTOCOL  *This,
  IN  UINT32                MediaId,
  IN  UINT64                Offset,
  IN  UINTN                 BufferSize,
  OUT VOID                  *Buffer
  )
{
  if ((Buffer == NULL) ||
      (Offset + BufferSize > (UINT64)(mMedia.LastBlock + 1) * RAMDISK_BLOCK_SIZE))
  {
    return EFI_INVALID_PARAMETER;
  }

  CopyMem (Buffer, mRamBase + Offset, BufferSize);
  return EFI_SUCCESS;
}

STATIC
EFI_STATUS
EFIAPI
RamDiskWriteDisk (
  IN EFI_DISK_IO_PROTOCOL  *This,
  IN UINT32                MediaId,
  IN UINT64                Offset,
  IN UINTN                 BufferSize,
  IN VOID                  *Buffer
  )
{
  return EFI_WRITE_PROTECTED;
}

STATIC EFI_DISK_IO_PROTOCOL  mDiskIo = {
  EFI_DISK_IO_PROTOCOL_REVISION,
  RamDiskReadDisk,
  RamDiskWriteDisk
};

/**
  Big-endian readers. A device tree is big-endian regardless of the host.
**/
STATIC
UINT32
Fdt32 (
  IN CONST UINT8  *P
  )
{
  return ((UINT32)P[0] << 24) | ((UINT32)P[1] << 16) | ((UINT32)P[2] << 8) | (UINT32)P[3];
}

STATIC
UINT64
Fdt64 (
  IN CONST UINT8  *P
  )
{
  return ((UINT64)Fdt32 (P) << 32) | (UINT64)Fdt32 (P + 4);
}

/**
  Read an address property whose width depends on the tree: two cells or one.
**/
STATIC
UINT64
FdtAddress (
  IN CONST UINT8  *Data,
  IN UINT32       Length
  )
{
  if (Length >= 8) {
    return Fdt64 (Data);
  }

  if (Length >= 4) {
    return Fdt32 (Data);
  }

  return 0;
}

/**
  Find linux,initrd-start and linux,initrd-end in /chosen.

  A hand-written walk, because this must work before any library that owns a
  device tree object is initialised, and because it only ever needs one node.

  @param[in]  Fdt    Pointer to a flattened device tree.
  @param[out] Start  First byte of the initrd.
  @param[out] End    One past the last byte of the initrd.

  @retval EFI_SUCCESS            Both properties were found.
  @retval EFI_NOT_FOUND          The tree has no /chosen node with an initrd.
  @retval EFI_INVALID_PARAMETER  The blob is not a device tree.
**/
STATIC
EFI_STATUS
FindInitrd (
  IN  CONST UINT8  *Fdt,
  OUT UINT64       *Start,
  OUT UINT64       *End
  )
{
  UINT32        TotalSize;
  UINT32        StructOffset;
  UINT32        StringsOffset;
  CONST UINT8   *Cursor;
  CONST UINT8   *StructEnd;
  CONST UINT8   *Strings;
  CONST CHAR8   *Name;
  UINT32        Depth;
  BOOLEAN       InChosen;
  UINT64        FoundStart;
  UINT64        FoundEnd;

  if ((Fdt == NULL) || (((UINTN)Fdt & 7) != 0)) {
    return EFI_INVALID_PARAMETER;
  }

  if (Fdt32 (Fdt) != FDT_MAGIC) {
    return EFI_INVALID_PARAMETER;
  }

  TotalSize     = Fdt32 (Fdt + 4);
  StructOffset  = Fdt32 (Fdt + 8);
  StringsOffset = Fdt32 (Fdt + 12);
  if ((TotalSize < 40) || (StructOffset >= TotalSize) || (StringsOffset >= TotalSize)) {
    return EFI_INVALID_PARAMETER;
  }

  Cursor    = Fdt + StructOffset;
  StructEnd = Fdt + TotalSize;
  Strings   = Fdt + StringsOffset;
  Depth     = 0;
  InChosen  = FALSE;
  FoundStart = 0;
  FoundEnd   = 0;

  while (Cursor + 4 <= StructEnd) {
    UINT32  Token = Fdt32 (Cursor);

    Cursor += 4;

    if (Token == FDT_BEGIN_NODE) {
      Name = (CONST CHAR8 *)Cursor;
      // The name is NUL-terminated and padded to four bytes.
      while ((Cursor < StructEnd) && (*Cursor != 0)) {
        Cursor++;
      }

      while ((Cursor < StructEnd) && (((UINTN)Cursor & 3) != 0)) {
        Cursor++;
      }

      Depth++;
      // The root node is itself a BEGIN_NODE, so /chosen (a child of the
      // root) sits at depth 2, not 1.
      InChosen = (BOOLEAN)((Depth == 2) && (AsciiStrCmp (Name, "chosen") == 0));
      continue;
    }

    if (Token == FDT_END_NODE) {
      if (Depth == 0) {
        return EFI_INVALID_PARAMETER;
      }

      if (Depth == 2) {
        InChosen = FALSE;
      }

      Depth--;
      continue;
    }

    if (Token == FDT_PROP) {
      UINT32       Length;
      UINT32       NameOffset;
      CONST UINT8  *Value;
      CONST CHAR8  *PropName;

      if (Cursor + 8 > StructEnd) {
        return EFI_INVALID_PARAMETER;
      }

      Length     = Fdt32 (Cursor);
      NameOffset = Fdt32 (Cursor + 4);
      Cursor    += 8;
      Value      = Cursor;
      if ((NameOffset >= TotalSize - StringsOffset) || (Value + Length > StructEnd)) {
        return EFI_INVALID_PARAMETER;
      }

      PropName = (CONST CHAR8 *)(Strings + NameOffset);
      if (InChosen) {
        if (AsciiStrCmp (PropName, "linux,initrd-start") == 0) {
          FoundStart = FdtAddress (Value, Length);
        } else if (AsciiStrCmp (PropName, "linux,initrd-end") == 0) {
          FoundEnd = FdtAddress (Value, Length);
        }
      }

      Cursor = Value + ALIGN_VALUE (Length, 4);
      continue;
    }

    if (Token == FDT_NOP) {
      continue;
    }

    if (Token == FDT_END) {
      break;
    }

    // An unknown token means the offsets are wrong; stop rather than wander.
    return EFI_INVALID_PARAMETER;
  }

  if ((FoundStart == 0) || (FoundEnd <= FoundStart)) {
    return EFI_NOT_FOUND;
  }

  *Start = FoundStart;
  *End   = FoundEnd;
  return EFI_SUCCESS;
}

/**
  Return the device tree that describes this boot.

  The cell the platform stashed the incoming pointer in is tried first; see
  the file header for why the FDT configuration table is only a fallback.
**/
STATIC
CONST UINT8 *
PlatformFdt (
  VOID
  )
{
  VOID        *Table;
  UINT64      Store;
  UINT64      Fdt;
  UINTN       Index;

  Store = PcdGet64 (PcdFdtStoreAddress);
  if (Store != 0) {
    CopyMem (&Fdt, (CONST VOID *)(UINTN)Store, sizeof (Fdt));
    if ((Fdt != 0) && ((Fdt & 7) == 0) &&
        (Fdt32 ((CONST UINT8 *)(UINTN)Fdt) == FDT_MAGIC))
    {
      return (CONST UINT8 *)(UINTN)Fdt;
    }

    DEBUG ((DEBUG_WARN, "SAGIT-RAMDISK: no valid tree at 0x%lx\n", Store));
  }

  Table = NULL;
  // There is no lookup helper in this EDK2 generation; walk the table.
  for (Index = 0; Index < gST->NumberOfTableEntries; Index++) {
    if (CompareGuid (&gST->ConfigurationTable[Index].VendorGuid, &gFdtTableGuid)) {
      Table = gST->ConfigurationTable[Index].VendorTable;
      break;
    }
  }

  if ((Table != NULL) && (Fdt32 ((CONST UINT8 *)Table) == FDT_MAGIC)) {
    DEBUG ((DEBUG_WARN, "SAGIT-RAMDISK: using the FDT configuration table\n"));
    return (CONST UINT8 *)Table;
  }

  return NULL;
}

/**
  Recognise the payload volume by its boot sector and return its size.

  The volume is self-describing: FAT records its own extent in the boot
  sector, so the driver never needs to know how big the payload is.
**/
STATIC
BOOLEAN
FatVolumeAt (
  IN  EFI_PHYSICAL_ADDRESS  Address,
  OUT UINT64                *Size
  )
{
  CONST UINT8  *Br = (CONST UINT8 *)(UINTN)Address;
  UINT32       TotalSectors;

  if ((Br[510] != 0x55) || (Br[511] != 0xAA)) {
    return FALSE;
  }

  if ((Br[11] | (Br[12] << 8)) != RAMDISK_BLOCK_SIZE) {
    return FALSE;
  }

  TotalSectors = Br[19] | (Br[20] << 8);
  if (TotalSectors == 0) {
    TotalSectors = Br[32] | (Br[33] << 8) | ((UINT32)Br[34] << 16) | ((UINT32)Br[35] << 24);
  }

  // Anything smaller cannot be a payload volume; garbage would have to pass
  // four independent checks anyway.
  if (TotalSectors < 2048) {
    return FALSE;
  }

  if ((AsciiStrnCmp ((CONST CHAR8 *)&Br[54], "FAT", 3) != 0) &&
      (AsciiStrnCmp ((CONST CHAR8 *)&Br[82], "FAT", 3) != 0))
  {
    return FALSE;
  }

  *Size = (UINT64)TotalSectors * RAMDISK_BLOCK_SIZE;
  return TRUE;
}

STATIC
VOID
ReportStatus (
  IN CONST CHAR8  *Text
  )
{
  gRT->SetVariable (
         PAYLOAD_STATUS_VARIABLE,
         (EFI_GUID *)&mStatusGuid,
         EFI_VARIABLE_BOOTSERVICE_ACCESS,
         AsciiStrSize (Text),
         (VOID *)Text
         );
}

/**
  Find the payload volume by scanning conventional memory.

  sagit's ABL copies the ramdisk, but not to the address the boot image header
  names: on the first hardware boot it landed at 0x43400000 while the header
  said 0xE0000000, and it also left a copy higher up. The address changes
  between boots, so the volume is found by what it is -- a FAT boot sector
  whose label is ADRENOLINE -- rather than by where the header asked for it.

  Only EfiConventionalMemory is read, so the walk cannot touch a hole.
**/
STATIC
EFI_STATUS
ScanForPayload (
  OUT EFI_PHYSICAL_ADDRESS  *Found,
  OUT UINT64                *Length
  )
{
  EFI_STATUS             Status;
  UINTN                  Size;
  UINTN                  MapKey;
  UINTN                  DescSize;
  UINT32                 DescVersion;
  EFI_MEMORY_DESCRIPTOR  *Map;
  EFI_MEMORY_DESCRIPTOR  *Desc;
  UINTN                  Count;
  UINTN                  Index;
  UINT64                 Base;
  UINT64                 Region;
  UINT64                 Offset;
  UINT64                 Candidate;

  Size   = 0;
  Status = gBS->GetMemoryMap (&Size, NULL, &MapKey, &DescSize, &DescVersion);
  if (Status != EFI_BUFFER_TOO_SMALL) {
    return Status;
  }

  Size += 8 * DescSize;
  Map   = AllocatePool (Size);
  if (Map == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }

  Status = gBS->GetMemoryMap (&Size, Map, &MapKey, &DescSize, &DescVersion);
  if (EFI_ERROR (Status)) {
    FreePool (Map);
    return Status;
  }

  Count = Size / DescSize;

  for (Index = 0; Index < Count; Index++) {
    Desc = (EFI_MEMORY_DESCRIPTOR *)((UINT8 *)Map + Index * DescSize);
    if (Desc->Type != EfiConventionalMemory) {
      continue;
    }

    Region = (UINT64)Desc->NumberOfPages * EFI_PAGE_SIZE;
    if (Region < (8U << 20)) {
      continue;
    }

    Base = Desc->PhysicalStart;
    for (Offset = 0; Offset + RAMDISK_BLOCK_SIZE <= Region; Offset += RAMDISK_BLOCK_SIZE) {
      CONST UINT8  *Br = (CONST UINT8 *)(UINTN)(Base + Offset);

      if (!FatVolumeAt (Base + Offset, Length)) {
        continue;
      }

      // Prefer our own label; a stray FAT filesystem should not shadow it.
      if (CompareMem (Br + 43, "ADRENOLINE ", 11) != 0) {
        continue;
      }

      *Found = Base + Offset;
      FreePool (Map);
      return EFI_SUCCESS;
    }
  }

  FreePool (Map);

  // No labelled volume; accept the first plausible FAT payload instead.
  Size   = 0;
  Status = gBS->GetMemoryMap (&Size, NULL, &MapKey, &DescSize, &DescVersion);
  if (Status != EFI_BUFFER_TOO_SMALL) {
    return EFI_NOT_FOUND;
  }

  Size += 8 * DescSize;
  Map   = AllocatePool (Size);
  if (Map == NULL) {
    return EFI_NOT_FOUND;
  }

  Status = gBS->GetMemoryMap (&Size, Map, &MapKey, &DescSize, &DescVersion);
  if (EFI_ERROR (Status)) {
    FreePool (Map);
    return EFI_NOT_FOUND;
  }

  Count = Size / DescSize;
  for (Index = 0; Index < Count; Index++) {
    Desc = (EFI_MEMORY_DESCRIPTOR *)((UINT8 *)Map + Index * DescSize);
    if (Desc->Type != EfiConventionalMemory) {
      continue;
    }

    Region = (UINT64)Desc->NumberOfPages * EFI_PAGE_SIZE;
    if (Region < (8U << 20)) {
      continue;
    }

    Base = Desc->PhysicalStart;
    for (Offset = 0; Offset + RAMDISK_BLOCK_SIZE <= Region; Offset += RAMDISK_BLOCK_SIZE) {
      if (!FatVolumeAt (Base + Offset, Length)) {
        continue;
      }

      Candidate = *Length;
      if ((Candidate < (8U << 20)) || (Candidate > (64U << 20))) {
        continue;
      }

      *Found = Base + Offset;
      FreePool (Map);
      return EFI_SUCCESS;
    }
  }

  FreePool (Map);
  return EFI_NOT_FOUND;
}

/**
  Same, but formatted, so the screen (via SagitReportDxe) says which step
  failed and with what status instead of a generic "no payload".
**/
STATIC
VOID
ReportStatusFmt (
  IN CONST CHAR8  *Format,
  ...
  )
{
  CHAR8    Buffer[192];
  VA_LIST  Marker;

  VA_START (Marker, Format);
  AsciiVSPrint (Buffer, sizeof (Buffer), Format, Marker);
  VA_END (Marker);

  ReportStatus (Buffer);
}

/**
  Publish [Start, Start + Length) as a read-only block device.
**/
STATIC
EFI_STATUS
PublishPayload (
  IN EFI_PHYSICAL_ADDRESS  Start,
  IN UINT64                Length
  )
{
  EFI_STATUS            Status;
  EFI_PHYSICAL_ADDRESS  Address;
  EFI_HANDLE            Handle;
  UINTN                 Pages;

  Pages = EFI_SIZE_TO_PAGES (Length);

  // Claim the bootloader's copy so the allocator can never hand these pages to
  // somebody else while UniXNU and FatDxe are reading them.
  Address = Start;
  Status  = gBS->AllocatePages (AllocateAddress, EfiLoaderData, Pages, &Address);
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_WARN, "SAGIT-RAMDISK: could not claim 0x%lx (%r); publishing anyway\n", Start, Status));
  }

  mRamBase                = (UINT8 *)(UINTN)Start;
  mMedia.MediaId          = 1;
  mMedia.RemovableMedia   = FALSE;
  mMedia.MediaPresent     = TRUE;
  mMedia.LogicalPartition = FALSE;
  mMedia.ReadOnly         = TRUE;
  mMedia.WriteCaching     = FALSE;
  mMedia.BlockSize        = RAMDISK_BLOCK_SIZE;
  mMedia.IoAlign          = 1;
  mMedia.LastBlock        = (EFI_LBA)(Length / RAMDISK_BLOCK_SIZE) - 1;

  Handle = NULL;
  Status = gBS->InstallMultipleProtocolInterfaces (
                  &Handle,
                  &gEfiDevicePathProtocolGuid,
                  &mDevicePath,
                  &gEfiBlockIoProtocolGuid,
                  &mBlockIo,
                  &gEfiDiskIoProtocolGuid,
                  &mDiskIo,
                  NULL
                  );
  DEBUG ((
    DEBUG_ERROR,
    "SAGIT-RAMDISK: payload at 0x%lx, %lu bytes, block device %r\n",
    (UINT64)Start,
    Length,
    Status
    ));
  return Status;
}

STATIC
EFI_STATUS
EFIAPI
RamDiskReset (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN BOOLEAN                ExtendedVerification
  )
{
  return EFI_SUCCESS;
}

STATIC
EFI_STATUS
EFIAPI
RamDiskReadBlocks (
  IN  EFI_BLOCK_IO_PROTOCOL  *This,
  IN  UINT32                 MediaId,
  IN  EFI_LBA                Lba,
  IN  UINTN                  BufferSize,
  OUT VOID                   *Buffer
  )
{
  if (Buffer == NULL) {
    return EFI_INVALID_PARAMETER;
  }

  if (MediaId != mMedia.MediaId) {
    return EFI_MEDIA_CHANGED;
  }

  if ((BufferSize % RAMDISK_BLOCK_SIZE) != 0) {
    return EFI_BAD_BUFFER_SIZE;
  }

  if ((Lba > mMedia.LastBlock) || ((BufferSize / RAMDISK_BLOCK_SIZE) > (mMedia.LastBlock + 1 - Lba))) {
    return EFI_INVALID_PARAMETER;
  }

  CopyMem (Buffer, mRamBase + (UINTN)Lba * RAMDISK_BLOCK_SIZE, BufferSize);
  return EFI_SUCCESS;
}

STATIC
EFI_STATUS
EFIAPI
RamDiskWriteBlocks (
  IN EFI_BLOCK_IO_PROTOCOL  *This,
  IN UINT32                 MediaId,
  IN UINT64                 Lba,
  IN UINTN                  BufferSize,
  IN VOID                   *Buffer
  )
{
  return EFI_WRITE_PROTECTED;
}

STATIC
EFI_STATUS
EFIAPI
RamDiskFlushBlocks (
  IN EFI_BLOCK_IO_PROTOCOL  *This
  )
{
  return EFI_SUCCESS;
}

EFI_STATUS
EFIAPI
SagitRamDiskDxeEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS            Status;
  CONST UINT8           *Fdt;
  EFI_PHYSICAL_ADDRESS  Probe;
  UINT64                Start;
  UINT64                End;
  UINT64                Length;

  Fdt = PlatformFdt ();
  if (Fdt != NULL) {
    Status = FindInitrd (Fdt, &Start, &End);
    if (!EFI_ERROR (Status)) {
      if (((Start | End) & (RAMDISK_BLOCK_SIZE - 1)) == 0) {
        Length = End - Start;
        Status = PublishPayload (Start, Length);
        if (!EFI_ERROR (Status)) {
          ReportStatusFmt ("ok: initrd 0x%lx+0x%lx from tree", Start, Length);
          return EFI_SUCCESS;
        }

        ReportStatusFmt ("initrd 0x%lx+0x%lx: publish failed %r", Start, Length, Status);
        return EFI_SUCCESS;
      } else {
        ReportStatusFmt ("initrd 0x%lx-0x%lx not 512-aligned", Start, End);
        DEBUG ((DEBUG_ERROR, "SAGIT-RAMDISK: initrd 0x%lx-0x%lx is not sector aligned\n", Start, End));
        return EFI_SUCCESS;
      }
    } else {
      ReportStatusFmt ("tree at 0x%lx: no initrd (%r)", (UINT64)(UINTN)Fdt, Status);
      DEBUG ((DEBUG_WARN, "SAGIT-RAMDISK: no initrd in /chosen (%r)\n", Status));
    }
  } else {
    ReportStatus ("no device tree from the bootloader");
  }

  // Either no tree, or no usable initrd in it. The boot image declares where
  // ABL was told to put the volume; the boot sector says whether it arrived.
  Probe = PcdGet64 (PcdPayloadProbeAddress);
  if (FatVolumeAt (Probe, &Length)) {
    Status = PublishPayload (Probe, Length);
    if (!EFI_ERROR (Status)) {
      ReportStatus ("ok: payload found by probe");
      return EFI_SUCCESS;
    }

    ReportStatusFmt ("probe 0x%lx: publish failed %r", Probe, Status);
    return EFI_SUCCESS;
  }

  ReportStatusFmt ("probe 0x%lx: no FAT boot sector", Probe);

  // ABL copies the ramdisk but places it wherever it likes; find it by shape.
  Status = ScanForPayload (&Start, &Length);
  if (!EFI_ERROR (Status)) {
    ReportStatusFmt ("scan: found payload at 0x%lx", Start);
    if (!EFI_ERROR (PublishPayload (Start, Length))) {
      ReportStatusFmt ("ok: payload at 0x%lx found by scan", Start);
      return EFI_SUCCESS;
    }

    ReportStatusFmt ("scan: payload at 0x%lx, publish failed %r", Start, Status);
    return EFI_SUCCESS;
  }

  ReportStatus ("scan: no FAT payload volume in memory");
  DEBUG ((DEBUG_ERROR, "SAGIT-RAMDISK: no payload source found\n"));
  return EFI_SUCCESS;
}
