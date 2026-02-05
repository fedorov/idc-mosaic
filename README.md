# IDC Mosaic

An interactive web mosaic showcasing the diversity of cancer imaging data from the [NCI Imaging Data Commons](https://portal.imaging.datacommons.cancer.gov/).

Each tile displays a medical image from IDC. Click any tile to open the full DICOM series in the IDC viewer.

## Features

- **Diverse Modalities**: CT, MR, PET, mammography, pathology, and more
- **Segmentation Overlays**: AI-generated anatomical segmentations from TotalSegmentator
- **Interactive Grid**: Adjustable grid sizes (4×4 to 12×12)
- **Direct Viewer Links**: Click any tile to explore the full series

## Quick Start

### View the Live Demo

Visit the [GitHub Pages site](https://imagingdatacommons.github.io/idc-mosaic/) to see the mosaic.

### Run Locally

```bash
# Clone the repository
git clone https://github.com/ImagingDataCommons/idc-mosaic.git
cd idc-mosaic

# Start a local server
python -m http.server 8000 -d docs

# Open http://localhost:8000 in your browser
```

## Regenerating the Mosaic

The mosaic tiles are defined in manifest files. To regenerate:

```bash
# Install dependencies
pip install -e .

# Generate diverse modality manifest
python -m idc_mosaic.generator -n 144 -o docs/data/manifest_diverse.json --seed 42

# Generate CT + segmentation manifest (slower - downloads SEG files)
python -m idc_mosaic.generator -n 144 --with-segmentations --seed 42
```

## Project Structure

```
idc-mosaic/
├── docs/                    # Static website (GitHub Pages)
│   ├── index.html
│   ├── css/style.css
│   ├── js/mosaic.js
│   └── data/               # Generated manifests
├── src/idc_mosaic/         # Python package
│   ├── sampler.py          # IDC data sampling
│   └── generator.py        # Manifest generation CLI
└── dev/                    # Developer documentation
```

## Requirements

- Python 3.9+
- [idc-index](https://github.com/ImagingDataCommons/idc-index) for querying IDC metadata

## License

Apache License 2.0 - see [LICENSE](LICENSE)

## Resources

- [NCI Imaging Data Commons](https://portal.imaging.datacommons.cancer.gov/)
- [IDC Documentation](https://learn.canceridc.dev/)
- [idc-index](https://github.com/ImagingDataCommons/idc-index)
