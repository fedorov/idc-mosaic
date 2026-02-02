# IDC Mosaic - Development Documentation

This document provides technical details for developers working on the IDC Mosaic project.

## Project Overview

IDC Mosaic is an interactive web application that displays a mosaic of medical images from the NCI Imaging Data Commons (IDC). Each tile is clickable, opening the full DICOM series in the IDC viewer. The site is designed for static hosting on GitHub Pages.

## Repository Structure

```
idc-mosaic/
├── pyproject.toml              # Python package configuration
├── DEVELOPMENT.md              # This file
├── development_process.md      # Conversation transcript from initial development
│
├── src/idc_mosaic/             # Python package source
│   ├── __init__.py             # Package initialization, version
│   ├── sampler.py              # IDC data sampling logic
│   └── generator.py            # Manifest generation CLI
│
├── scripts/
│   └── generate.py             # CLI entry point for development
│
├── docs/                       # GitHub Pages static site
│   ├── index.html              # Main webpage
│   ├── css/
│   │   └── style.css           # Mosaic grid styling
│   ├── js/
│   │   └── mosaic.js           # Tile loading and interaction
│   └── data/
│       └── manifest.json       # Generated tile metadata
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
                                                ▼
                                         ┌──────────────┐
                                         │  DICOMweb    │
                                         │  (images)    │
                                         └──────────────┘
```

## Core Components

### 1. Sampler (`src/idc_mosaic/sampler.py`)

Handles sampling of diverse images from IDC.

**Key Classes:**
- `TileSample` - Dataclass representing a single tile with all metadata
- `IDCSampler` - Main sampling logic

**Sampling Algorithm:**
1. Query all series from IDC index for included modalities
2. Calculate proportional distribution based on series count per modality
3. Sample from each modality according to its proportion
4. For volumetric series (CT/MR), select middle slice
5. Resolve SOPInstanceUID via DICOMweb QIDO-RS query
6. Build rendered frame URL and viewer URL

**Configuration:**
```python
# Included modalities (visual imaging only)
INCLUDED_MODALITIES = ["CT", "MR", "PT", "CR", "DX", "MG", "US", "SM", "XA", "NM"]

# DICOMweb endpoint
DICOMWEB_BASE_URL = "https://proxy.imaging.datacommons.cancer.gov/current/viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb"
```

**Key Methods:**
- `get_available_strata()` - Query IDC metadata
- `sample(n_samples)` - Main sampling entry point
- `_build_tile_sample(row)` - Build TileSample from dataframe row
- `_get_sop_instance_uid(study, series, frame_index)` - DICOMweb query for SOP UID
- `_build_tile_url(...)` - Construct rendered frame URL

### 2. Generator (`src/idc_mosaic/generator.py`)

Orchestrates sampling and generates the manifest.

**Functions:**
- `generate_manifest(num_tiles, output_path, seed)` - Main generation function
- `main()` - CLI entry point

**Manifest Format:**
```json
{
  "generated": "2026-02-02T23:05:19.045323+00:00",
  "idc_version": "v23",
  "total_tiles": 100,
  "tiles": [
    {
      "index": 0,
      "tile_url": "https://proxy.imaging.datacommons.cancer.gov/.../rendered",
      "viewer_url": "https://viewer.imaging.datacommons.cancer.gov/...",
      "modality": "CT",
      "body_part": "CHEST",
      "collection": "nlst",
      "series_uid": "1.3.6.1..."
    }
  ]
}
```

### 3. Frontend (`docs/`)

Static website served by GitHub Pages.

**index.html:**
- Header with title and controls
- Grid container for tiles
- Loading indicator

**style.css:**
- CSS Grid layout with CSS custom properties (`--cols`)
- Dark theme appropriate for medical imaging
- Hover effects with scale transform
- Loading shimmer animation
- Responsive design

**mosaic.js:**
- Reads grid size from URL parameters (`?cols=8&rows=8`)
- Fetches and parses manifest.json
- Creates tile elements with background images
- Handles click events to open IDC viewer
- Error handling for failed tile loads

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
# Default: 100 tiles
python scripts/generate.py

# Custom options
python scripts/generate.py --num-tiles 64 --seed 42 --output docs/data/manifest.json
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
| `cols`    | 8       | Number of grid columns |
| `rows`    | 8       | Number of grid rows |

Example: `http://localhost:8000/?cols=10&rows=10`

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
2. Regenerate manifest: `python scripts/generate.py`
3. Commit and push new `manifest.json`

## Troubleshooting

### DICOMweb Requests Failing

- Check if proxy is accessible: `curl -I https://proxy.imaging.datacommons.cancer.gov/`
- Verify SOPInstanceUID exists in IDC
- Check for rate limiting (429 errors)

### Tiles Not Loading in Browser

- Check browser console for CORS errors (shouldn't occur with IDC proxy)
- Verify manifest.json is valid JSON
- Check tile_url format in manifest

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
# Generate with more tiles and fixed seed for reproducibility
python scripts/generate.py --num-tiles 144 --seed 42

# Commit and push
git add docs/data/manifest.json
git commit -m "Regenerate mosaic with 144 tiles"
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
```

### generator.generate_manifest

```python
def generate_manifest(
    num_tiles: int = 100,           # Number of tiles to sample
    output_path: str = "docs/data/manifest.json",  # Output file
    seed: int | None = None,        # Random seed for reproducibility
) -> dict:                          # Returns manifest dictionary
```

## Resources

- [IDC Portal](https://portal.imaging.datacommons.cancer.gov/)
- [IDC Documentation](https://learn.canceridc.dev/)
- [idc-index GitHub](https://github.com/ImagingDataCommons/idc-index)
- [DICOMweb Standard](https://www.dicomstandard.org/using/dicomweb)
- [OHIF Viewer](https://ohif.org/)
