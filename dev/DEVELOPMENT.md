# IDC Mosaic - Development Documentation

This document provides technical details for developers working on the IDC Mosaic project.

## Project Overview

IDC Mosaic is an interactive web application that displays a mosaic of medical images from the NCI Imaging Data Commons (IDC). Each tile is clickable, opening the full DICOM series in the IDC viewer. The site is designed for static hosting on GitHub Pages.

## Repository Structure

```
idc-mosaic/
├── pyproject.toml              # Python package configuration
├── dev/
│   ├── DEVELOPMENT.md          # This file
│   └── development_process.md  # Conversation transcript from initial development
│
├── src/idc_mosaic/             # Python package source
│   ├── __init__.py             # Package initialization, version
│   ├── sampler.py              # IDC data sampling logic (diverse + segmentation)
│   └── generator.py            # Manifest generation CLI
│
├── scripts/
│   └── generate.py             # CLI entry point for development
│
├── docs/                       # GitHub Pages static site
│   ├── index.html              # Main webpage with view selector
│   ├── css/
│   │   └── style.css           # Mosaic grid styling, canvas overlay
│   ├── js/
│   │   └── mosaic.js           # Tile loading, segmentation overlay, view modes
│   └── data/
│       ├── manifest.json       # CT tiles with segmentation data
│       └── manifest_diverse.json  # Diverse modality tiles
│
└── .venv/                      # Virtual environment (not committed)
```

## Architecture

### Data Flow

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  idc-index  │────▶│   sampler    │────▶│  generator   │
│  (metadata) │     │  (sampling)  │     │  (manifest)  │
└─────────────┘     └──────────────┘     └──────────────┘
                           │                     │
                           ▼                     ▼
                    ┌──────────────┐     ┌──────────────┐
                    │  DICOMweb    │     │ manifest.json│
                    │  (SOPUIDs)   │     │   (output)   │
                    └──────────────┘     └──────────────┘
```

### Runtime Flow (Browser)

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Browser    │────▶│ manifest.json│────▶│  Render Grid │
│  (load)     │     │   (fetch)    │     │   (tiles)    │
└─────────────┘     └──────────────┘     └──────────────┘
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          ▼                     ▼                     ▼
                   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
                   │  DICOMweb    │      │  DICOMweb    │      │   Canvas     │
                   │  (CT image)  │      │  (SEG frames)│      │  (overlay)   │
                   └──────────────┘      └──────────────┘      └──────────────┘
```

## View Modes

The application supports three view modes, selectable via dropdown:

| Mode | Description | Manifest |
|------|-------------|----------|
| Diverse Modalities | Images across CT, MR, MG, SM, etc. | `manifest_diverse.json` |
| CT Only | CT images without overlays | `manifest.json` |
| CT + Segmentations | CT images with TotalSegmentator overlays | `manifest.json` |

## Core Components

### 1. Sampler (`src/idc_mosaic/sampler.py`)

Handles sampling of diverse images from IDC, with optional segmentation support.

**Key Classes:**
- `TileSample` - Dataclass representing a single tile with all metadata
- `SegmentInfo` - Segment metadata (number, label, RGB color)
- `SegmentationData` - Segmentation series data (frame_map, segments, viewer_url)
- `IDCSampler` - Diverse modality sampling
- `IDCSegmentationSampler` - CT images with TotalSegmentator segmentations

**Diverse Sampling Algorithm (IDCSampler):**
1. Query all series from IDC index for included modalities
2. Calculate proportional distribution based on series count per modality
3. Sample from each modality according to its proportion
4. For volumetric series (CT/MR), select middle slice
5. Resolve SOPInstanceUID via DICOMweb QIDO-RS query
6. Build rendered frame URL and viewer URL

**Segmentation Sampling Algorithm (IDCSegmentationSampler):**
1. Query `seg_index` joined with `index` for TotalSegmentator segmentations
2. Sample randomly from available segmented CT series
3. For each sample:
   - Get middle slice SOPInstanceUID
   - Download SEG DICOM file
   - Parse Per-frame Functional Groups to build frame_map
   - Extract segment labels and CIELab colors from SegmentSequence
   - Convert CIELab to RGB for browser rendering

**Configuration:**
```python
# Included modalities (visual imaging only)
INCLUDED_MODALITIES = ["CT", "MR", "PT", "CR", "DX", "MG", "US", "SM", "XA", "NM"]

# DICOMweb endpoint
DICOMWEB_BASE_URL = "https://proxy.imaging.datacommons.cancer.gov/current/viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb"
```

**Key Methods (IDCSampler):**
- `get_available_strata()` - Query IDC metadata
- `sample(n_samples)` - Main sampling entry point
- `_build_tile_sample(row)` - Build TileSample from dataframe row
- `_get_sop_instance_uid(study, series, frame_index)` - DICOMweb query for SOP UID
- `_build_tile_url(...)` - Construct rendered frame URL

**Key Methods (IDCSegmentationSampler):**
- `get_available_segmented_series()` - Query seg_index for TotalSegmentator
- `_build_tile_sample_with_segmentation(row)` - Build TileSample with seg data
- `_get_segmentation_data(...)` - Get SEG SOP UID and download SEG
- `_download_and_parse_seg(...)` - Parse Per-frame Functional Groups, extract frame_map

**Helper Functions:**
- `cielab_to_rgb(L, a, b)` - Convert DICOM CIELab (0-65535 scaled) to RGB

### 2. Generator (`src/idc_mosaic/generator.py`)

Orchestrates sampling and generates the manifest.

**Functions:**
- `generate_manifest(num_tiles, output_path, seed, with_segmentations)` - Main generation
- `update_viewer_urls(manifest_path, output_path)` - Fast URL update without regeneration
- `main()` - CLI entry point

**CLI Options:**
```bash
# Generate diverse modality manifest
python -m idc_mosaic.generator -n 144 -o docs/data/manifest_diverse.json

# Generate CT + segmentation manifest (slow - downloads SEG files)
python -m idc_mosaic.generator -n 144 --with-segmentations --seed 42

# Fast: Update viewer URLs in existing manifest (no SEG download)
python -m idc_mosaic.generator --update-urls docs/data/manifest.json
```

**Manifest Format (with segmentation):**
```json
{
  "generated": "2026-02-05T22:06:43.223863+00:00",
  "idc_version": "v23",
  "total_tiles": 144,
  "dicomweb_base_url": "https://proxy.imaging.datacommons.cancer.gov/.../dicomWeb",
  "has_segmentations": true,
  "tiles": [
    {
      "index": 0,
      "tile_url": "https://proxy.imaging.datacommons.cancer.gov/.../rendered",
      "viewer_url": "https://viewer.imaging.datacommons.cancer.gov/...",
      "modality": "CT",
      "body_part": "CHEST",
      "collection": "nlst",
      "series_uid": "1.3.6.1...",
      "study_uid": "1.2.840...",
      "segmentation": {
        "series_uid": "1.2.276...",
        "sop_uid": "1.2.276...",
        "algorithm": "TotalSegmentator",
        "frame_map": {"6": 247, "12": 468, ...},
        "segments": [
          {"number": 6, "label": "Aorta", "rgb": [225, 96, 74]},
          {"number": 12, "label": "Left Upper lobe of lung", "rgb": [113, 161, 93]}
        ],
        "viewer_url": "https://viewer.imaging.datacommons.cancer.gov/...SEG_SERIES..."
      }
    }
  ]
}
```

### 3. Frontend (`docs/`)

Static website served by GitHub Pages.

**index.html:**
- Header with title and controls
- Grid size dropdown (4×4 to 12×12)
- View mode dropdown (Diverse / CT Only / CT + Segmentations)
- Grid container for tiles
- Loading indicator

**style.css:**
- CSS Grid layout with CSS custom properties (`--cols`)
- Dark theme appropriate for medical imaging
- Hover effects with scale transform
- Loading shimmer animation
- Canvas styling for segmentation overlay (`.tile-canvas`)
- Responsive design

**mosaic.js:**
- Reads grid size and view mode from URL parameters (`?cols=8&view=segmentation`)
- Fetches appropriate manifest based on view mode
- Creates tile elements with canvas for rendering
- **Segmentation overlay rendering:**
  - Fetches raw SEG frames from DICOMweb (`/frames/{n}` with multipart response)
  - Parses multipart response to extract binary frame data
  - Unpacks 1-bit packed data to byte array (LSB first)
  - Flips vertically to correct for ImageOrientationPatient differences
  - Composites colored segments onto base CT image with 50% opacity
- Click handler opens appropriate viewer URL (SEG series when showing segmentations)
- Error handling with fallback to base image

## Development Setup

### Prerequisites
- Python 3.9+
- uv (recommended) or pip

### Environment Setup
```bash
# Clone repository
git clone https://github.com/ImagingDataCommons/idc-mosaic.git
cd idc-mosaic

# Create virtual environment with uv
uv venv .venv
source .venv/bin/activate
uv pip install -e .

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Generate Manifest
```bash
# Default: 100 diverse tiles
python -m idc_mosaic.generator

# Diverse modalities (for "Diverse Modalities" view)
python -m idc_mosaic.generator -n 144 -o docs/data/manifest_diverse.json --seed 42

# CT with segmentations (slow - downloads SEG DICOM files)
python -m idc_mosaic.generator -n 144 --with-segmentations --seed 42

# Fast: Update viewer URLs only (no SEG download)
python -m idc_mosaic.generator --update-urls docs/data/manifest.json
```

### Local Testing
```bash
# Start local server
python -m http.server 8000 -d docs

# Open in browser
open http://localhost:8000
```

### URL Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `cols`    | 8       | Number of grid columns (4, 6, 8, 10, 12) |
| `view`    | segmentation | View mode: `diverse`, `ct-only`, `segmentation` |

Example: `http://localhost:8000/?cols=10&view=diverse`

## External Dependencies

### IDC Services

**idc-index Package:**
- Local DuckDB database with IDC metadata
- No authentication required
- Updated with each IDC release
- [Documentation](https://github.com/ImagingDataCommons/idc-index)

**DICOMweb Public Proxy:**
- URL: `https://proxy.imaging.datacommons.cancer.gov/current/viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb`
- No authentication required
- Daily per-IP quota (sufficient for mosaic generation)
- Supports CORS for browser requests
- WADO-RS `/rendered` endpoint returns JPEG images
- WADO-RS `/frames/{n}` returns raw pixel data (for SEG)

**IDC Viewer:**
- URL pattern: `https://viewer.imaging.datacommons.cancer.gov/v3/viewer/?StudyInstanceUIDs=...&SeriesInstanceUIDs=...`
- OHIF-based DICOM viewer
- Slide microscopy uses SLIM viewer: `https://viewer.imaging.datacommons.cancer.gov/slim/studies/.../series/...`

### Key DICOM Concepts

**Hierarchy:**
- Patient → Study → Series → Instance (SOP)
- SeriesInstanceUID identifies a series (group of related images)
- SOPInstanceUID identifies a single DICOM instance
- For CT/MR, each slice is typically one instance

**Modalities:**
| Code | Meaning |
|------|---------|
| CT   | Computed Tomography |
| MR   | Magnetic Resonance |
| PT   | PET (Positron Emission Tomography) |
| SM   | Slide Microscopy (Pathology) |
| MG   | Mammography |
| CR   | Computed Radiography |
| DX   | Digital Radiography |
| US   | Ultrasound |
| XA   | X-Ray Angiography |
| NM   | Nuclear Medicine |
| SEG  | Segmentation (binary masks) |

### DICOM SEG (Segmentation) Objects

**Structure:**
- Multi-frame DICOM object containing binary segmentation masks
- Each frame is a 1-bit packed binary mask for one segment on one source slice
- `PerFrameFunctionalGroupsSequence` maps frames to source images and segments
- `SegmentSequence` contains metadata (labels, colors) for each segment

**Key Attributes:**
- `SegmentSequence[n].SegmentNumber` - Segment identifier (1-based)
- `SegmentSequence[n].SegmentLabel` - Human-readable label (e.g., "Aorta")
- `SegmentSequence[n].RecommendedDisplayCIELabValue` - Color in CIELab (0-65535 scaled)
- `PerFrameFunctionalGroupsSequence[n].SegmentIdentificationSequence` - Which segment
- `PerFrameFunctionalGroupsSequence[n].DerivationImageSequence.SourceImageSequence` - Source SOP UID

**ImageOrientationPatient Flip:**
- CT typically has `[1,0,0,0,1,0]` (standard orientation)
- SEG from TotalSegmentator has `[1,0,0,0,-1,0]` (Y-inverted)
- Browser rendering must flip vertically to align overlay with CT image

**TotalSegmentator:**
- AI-generated whole-body CT segmentations
- ~79 anatomical structures per scan
- Available for 126,000+ CT scans in IDC via `seg_index`

## Common Development Tasks

### Adding a New Modality

1. Add to `INCLUDED_MODALITIES` in `sampler.py`
2. Verify it exists in IDC:
   ```python
   from idc_index import IDCClient
   client = IDCClient()
   client.sql_query("SELECT DISTINCT Modality FROM index")
   ```

### Changing Sampling Strategy

Edit the `sample()` method in `sampler.py`. Current strategy:
- Proportional to series count per modality
- At least 1 sample per modality (if enough total samples)
- Middle slice for volumetric data

### Modifying the UI

- **Layout**: Edit CSS Grid in `style.css` (`.mosaic-grid`)
- **Colors**: Update CSS custom properties in `:root`
- **Interactions**: Edit `mosaic.js`

### Updating for New IDC Version

1. Update idc-index: `uv pip install --upgrade idc-index`
2. Update viewer URLs (fast): `python -m idc_mosaic.generator --update-urls docs/data/manifest.json`
3. Regenerate diverse manifest: `python -m idc_mosaic.generator -n 144 -o docs/data/manifest_diverse.json`
4. Commit and push updated manifests

## Troubleshooting

### DICOMweb Requests Failing

- Check if proxy is accessible: `curl -I https://proxy.imaging.datacommons.cancer.gov/`
- Verify SOPInstanceUID exists in IDC
- Check for rate limiting (429 errors)

### Tiles Not Loading in Browser

- Check browser console for CORS errors (shouldn't occur with IDC proxy)
- Verify manifest.json is valid JSON
- Check tile_url format in manifest

### Segmentation Overlay Issues

**Overlay appears flipped:**
- Check ImageOrientationPatient between CT and SEG
- The `unpackBitData` function handles vertical flip for TotalSegmentator

**Overlay not appearing:**
- Check browser console for DICOMweb frame fetch errors
- Verify `frame_map` in manifest has correct frame indices (1-based)
- Check that multipart response is being parsed correctly

**Segmentation generation is slow:**
- Normal: Each tile requires downloading 40-200MB SEG DICOM file
- Use `--update-urls` to update viewer URLs without regeneration
- Consider generating fewer tiles or using cached manifests

### Empty or Small Manifest

- Verify idc-index is up to date
- Check that modalities exist in current IDC version
- Review DICOMweb query errors in generation output

## Deployment

### GitHub Pages

1. Push changes to main branch
2. Go to repository Settings → Pages
3. Set source to "Deploy from a branch"
4. Select branch: `main`, folder: `/docs`
5. Site will be available at `https://username.github.io/idc-mosaic/`

### Regenerating for Production

```bash
# Generate diverse modality manifest
python -m idc_mosaic.generator -n 144 -o docs/data/manifest_diverse.json --seed 42

# Generate CT + segmentation manifest (slow)
python -m idc_mosaic.generator -n 144 --with-segmentations --seed 42

# Commit and push
git add docs/data/manifest.json docs/data/manifest_diverse.json
git commit -m "Regenerate mosaic manifests"
git push
```

## API Reference

### sampler.TileSample

```python
@dataclass
class TileSample:
    series_uid: str       # DICOM SeriesInstanceUID
    study_uid: str        # DICOM StudyInstanceUID
    sop_uid: str          # DICOM SOPInstanceUID
    modality: str         # DICOM Modality code
    body_part: str        # Body part examined
    collection_id: str    # IDC collection identifier
    instance_count: int   # Number of instances in series
    frame_number: int     # Frame number for rendered URL
    tile_url: str         # DICOMweb rendered frame URL
    viewer_url: str       # IDC viewer URL
    segmentation: Optional[SegmentationData] = None  # Segmentation data if available
```

### sampler.SegmentationData

```python
@dataclass
class SegmentationData:
    series_uid: str       # SEG SeriesInstanceUID
    sop_uid: str          # SEG SOPInstanceUID
    algorithm: str        # Algorithm name (e.g., "TotalSegmentator")
    frame_map: dict[int, int]  # segment_number -> DICOMweb frame index (1-based)
    segments: list[SegmentInfo]  # Segment metadata
    viewer_url: str = ""  # IDC viewer URL for SEG series
```

### sampler.SegmentInfo

```python
@dataclass
class SegmentInfo:
    number: int           # Segment number (1-based)
    label: str            # Segment label (e.g., "Aorta")
    rgb: tuple[int, int, int]  # RGB color for rendering
```

### generator.generate_manifest

```python
def generate_manifest(
    num_tiles: int = 100,           # Number of tiles to sample
    output_path: str = "docs/data/manifest.json",  # Output file
    seed: int | None = None,        # Random seed for reproducibility
    with_segmentations: bool = False,  # Sample CT with TotalSegmentator SEGs
) -> dict:                          # Returns manifest dictionary
```

### generator.update_viewer_urls

```python
def update_viewer_urls(
    manifest_path: str,             # Path to existing manifest
    output_path: str | None = None, # Output path (defaults to manifest_path)
) -> dict:                          # Returns updated manifest dictionary
```

## Resources

- [IDC Portal](https://portal.imaging.datacommons.cancer.gov/)
- [IDC Documentation](https://learn.canceridc.dev/)
- [idc-index GitHub](https://github.com/ImagingDataCommons/idc-index)
- [DICOMweb Standard](https://www.dicomstandard.org/using/dicomweb)
- [OHIF Viewer](https://ohif.org/)
