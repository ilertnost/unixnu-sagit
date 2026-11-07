# Board package: becomes kits/sagit.kit in the assembled tree.
#
# The current bring-up does not load kit drivers yet: the firmware is built
# by tools/build-sagit-edk2.sh (edk2-msm8998 + SagitPkg + DarwinBoot) and the
# payload volume by tools/mksagit-payload.py. When the firmware moves to the
# Mu-Silicium model, the Kit.toml with the driver/overlay/kext lists lives
# here and the official ./unixnu payload and bootimg steps replace the
# sagit-specific packaging scripts.
