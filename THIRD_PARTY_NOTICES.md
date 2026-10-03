# Third-Party Notices

This repository is licensed under the GNU Affero General Public License v3.0 (`AGPL v3.0`) except where otherwise noted.

The following components remain subject to their own upstream licenses and notices:

## 1. Git submodule: `app_common`

- Path: `app_common/`
- Source: `https://github.com/OscarKing888/SuperAppCommonLib.git`
- Status: This is a separate Git repository included as a submodule.
- Notice: The root `LICENSE` file of this repository does not automatically relicense the `app_common/` submodule. Its license and copyright status must be managed in its own upstream repository.

## 2. Bundled ExifTool for Windows

- Path: `app_common/exif_io/exiftools_win/exiftool_files/`
- Included files indicate this package is assembled from upstream ExifTool, Strawberry Perl, and a launcher by Oliver Betz.
- Refer to:
  - `app_common/exif_io/exiftools_win/exiftool_files/readme_windows.txt`
  - `app_common/exif_io/exiftools_win/exiftool_files/LICENSE`
  - `app_common/exif_io/exiftools_win/exiftool_files/Licenses_Strawberry_Perl.zip`
- Notice: These bundled files keep their original upstream licenses and notices.

## 3. Bundled ExifTool for macOS

- Path: `app_common/exif_io/exiftools_mac/exiftool`
- Notice: This executable is a third-party upstream binary/script and should continue to follow its original upstream license terms.

## 4. Python dependencies

- Runtime dependencies are declared in `requirements.txt`.
- Each dependency remains under its own upstream license.
- When distributing binaries or source bundles, review upstream license obligations for all shipped dependencies.

## 5. NAFNet RGB denoising

- The inference architecture in `image_denoise/architecture.py` is adapted from the official [NAFNet](https://github.com/megvii-research/NAFNet) implementation; it retains the upstream MIT notice and the applicable BasicSR Apache 2.0 notice.
- The pinned NAFNet-SIDD width64 weights are downloaded separately and are not committed to Git. Their revision, SHA-256 and source are recorded in `image_denoise/models.py`.
- See [the denoising provenance notice](image_denoise/NOTICE.txt) and [upstream license texts](image_denoise/THIRD_PARTY_LICENSE.txt). Both notices are included with packaged SuperViewer resources.
