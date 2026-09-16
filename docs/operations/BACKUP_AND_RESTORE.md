# Backup and restore

## Why this matters more than it looks

Sterling records market data that cannot be bought back later. The data vendor
does not sell historical prices for expired option contracts — this was verified
directly, and it is why the historical study had to use estimated prices instead
of real ones for all 746 signals.

Every day Sterling records is permanent evidence. Every day it fails to record
is permanently gone.

## What exists today

The market data lake lives on a USB drive, with two verified copies:

```
working    /run/media/.../SterlingLake
copy 1     /mnt/OS/SterlingLakeBackup          (internal disk)
copy 2     the SD card                          (separate device)
```

Both were verified file-by-file with checksums, not just copied. A successful
copy is not evidence that the files survived intact.

## Making a new backup

```bash
LAKE=$(ls -d /run/media/*/*/SterlingLake)
DEST=/mnt/OS/SterlingLakeBackup

rsync -a --info=stats2 "$LAKE/" "$DEST/SterlingLake/"
```

Then **verify it**, which is the part people skip:

```bash
cd "$DEST/SterlingLake" && sha256sum -c "$DEST/LAKE_MANIFEST.sha256" --quiet && echo VERIFIED
```

If that prints VERIFIED, the copy is real. If it prints anything else, the copy
is not trustworthy and the original is still your only copy.

## Regenerating the manifest after new evidence is recorded

```bash
LAKE=$(ls -d /run/media/*/*/SterlingLake)
cd "$LAKE" && find . -type f -print0 | sort -z | xargs -0 sha256sum > /tmp/LAKE_MANIFEST.sha256
```

Keep the manifest with each copy. Record, each time: the date, the number of
files, and whether verification passed.

## Restoring

Copy back from a verified backup, then verify the restored copy against the same
manifest before using it. Do not skip that check because the copy "looked fine".

## If the drive dies

The two backups are the system. Replace the drive, restore from a verified copy,
verify again, and continue. Nothing is lost provided the backups were verified
when they were made — which is the entire reason for verifying them.
