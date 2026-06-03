# Image Format Support

The shared source of truth for processable image extensions is
`app_common/image_formats.py`.

Supported extension groups:

- Standard Pillow/Qt image files: `.jpg`, `.jpeg`, `.png`, `.webp`, `.tif`, `.tiff`
- HEIF files: `.heic`, `.heif`, `.hif`
- Camera RAW files: `.cr2`, `.cr3`, `.crw`, `.nef`, `.nrw`, `.arw`, `.srf`, `.sr2`, `.rw2`, `.raw`, `.orf`, `.ori`, `.raf`, `.dng`, `.pef`, `.ptx`, `.x3f`, `.rwl`, `.3fr`, `.dcr`, `.kdc`, `.mef`, `.mrw`, `.rwz`
- Photoshop files: `.psd`

PSD support in this repo uses Pillow's PSD reader for the flattened image
preview/import path. Pillow is already listed in both app requirement files, so
no additional library is required for basic `.psd` preview and rendering.

Download/install references:

- Pillow: https://pypi.org/project/Pillow/
- Pillow PSD format documentation: https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#psd

If future work needs layer enumeration or layer compositing beyond the flattened
image path, use `psd-tools` as an optional dependency instead of adding it to the
default runtime:

- psd-tools documentation: https://psd-tools.readthedocs.io/en/latest/
- psd-tools PyPI download: https://pypi.org/project/psd-tools/
