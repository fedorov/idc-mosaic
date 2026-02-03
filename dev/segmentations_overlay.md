# Segmentation Overlay Implementation

## Conversation Transcript - 2026-02-03

### User Request
> is it possible to modify this code to sample images that have segmentations, and pre-render the segmentations for the specific tiles selected?

---

### Initial Exploration (13:24)

Explored the codebase to understand:
1. Overall structure and purpose of the project
2. How image sampling currently works
3. How tiles are selected and rendered
4. Any existing segmentation-related code
5. The data flow from IDC to rendered tiles

**Key findings:**
- IDC Mosaic is a static website displaying medical images from NCI Imaging Data Commons
- No existing segmentation code
- Uses DICOMweb proxy for runtime image loading
- Sampling via `IDCSampler` class with proportional modality distribution

---

### IDC Segmentation Research (13:26)

Used the `imaging-data-commons` skill to query for segmentations.

**Key discovery:** IDC provides a `seg_index` table with detailed segmentation metadata. The `segmented_SeriesInstanceUID` column links segmentations back to source image series.

```python
client.fetch_index("seg_index")
result = client.sql_query("""
    SELECT
        src.SeriesInstanceUID as image_series,
        s.SeriesInstanceUID as seg_series,
        s.AlgorithmName,
        s.total_segments
    FROM seg_index s
    JOIN index src ON s.segmented_SeriesInstanceUID = src.SeriesInstanceUID
""")
```

**TotalSegmentator availability:** 126,051 segmentations with ~79 anatomical structures each (CT only).

---

### User Preferences (13:27)

Asked user about implementation approach:

1. **Rendering method:** User chose "Overlay at runtime in browser"
2. **Segmentation source:** User chose "TotalSegmentator (Recommended)"

---

### DICOM SEG Structure Analysis (13:28-13:30)

Explored DICOM SEG structure:
- SEG objects are multi-frame (e.g., 3902 frames for 79 segments across 173 slices)
- 1-bit packed pixel data (512x512 = 32KB per frame)
- Per-frame Functional Groups map frames to source images and segment numbers
- Segment colors stored as CIELab values

**DICOMweb retrieval:**
- `/rendered` endpoint returns 406 for SEG (not supported)
- Raw frame retrieval via `/frames/{n}` with `Accept: multipart/related` works
- Returns 1-bit packed binary mask data

---

### Implementation (13:30-13:35)

#### 1. New Data Classes in `sampler.py`

```python
@dataclass
class SegmentInfo:
    number: int
    label: str
    rgb: tuple[int, int, int]

@dataclass
class SegmentationData:
    series_uid: str
    sop_uid: str
    algorithm: str
    frame_map: dict[int, int]  # segment_num -> frame_index
    segments: list[SegmentInfo]
```

#### 2. New `IDCSegmentationSampler` Class

- Queries `seg_index` for CT images with TotalSegmentator segmentations
- Downloads SEG DICOM to parse Per-frame Functional Groups
- Builds frame mapping (segment number → DICOMweb frame index)
- Extracts segment labels and CIELab→RGB colors

#### 3. Updated `generator.py`

- Added `--with-segmentations` CLI flag
- Includes `dicomweb_base_url` and segmentation metadata in manifest

#### 4. Updated `mosaic.js`

- Canvas-based tile rendering for overlay compositing
- Fetches segmentation frames via DICOMweb
- Parses multipart response to extract raw pixel data
- Unpacks 1-bit packed data to byte arrays
- Composites colored segments onto base image at 50% opacity

---

### Testing (13:34-13:35)

Generated test manifest with 4 tiles:
```bash
python scripts/generate.py --num-tiles 4 --with-segmentations --seed 42
```

Output:
```
Tiles with segmentations: 4/4
Segments per tile: min=20, max=27, avg=23.5
```

---

### Bug Fix: Flipped Overlay (13:37)

**User reported:** "the overlay shows up, but it is flipped"

**User hint:** "did you check ImageOrientationPatient between SEG and CT?"

**Investigation revealed:**
- CT: `ImageOrientationPatient = [1, 0, 0, 0, 1, 0]` (standard)
- SEG: `ImageOrientationPatient = [1, 0, 0, 0, -1, 0]` (Y-inverted)

**Fix:** Modified `unpackBitData()` in JavaScript to flip vertically:

```javascript
function unpackBitData(packedData, width, height) {
    const totalPixels = width * height;
    const unpacked = new Uint8Array(totalPixels);

    for (let srcRow = 0; srcRow < height; srcRow++) {
        // Flip vertically: SEG has ImageOrientationPatient [1,0,0,0,-1,0]
        // while CT typically has [1,0,0,0,1,0]
        const dstRow = height - 1 - srcRow;

        for (let col = 0; col < width; col++) {
            const srcIdx = srcRow * width + col;
            const dstIdx = dstRow * width + col;

            const byteIndex = Math.floor(srcIdx / 8);
            const bitIndex = srcIdx % 8;

            if (byteIndex < packedData.length) {
                unpacked[dstIdx] = (packedData[byteIndex] >> bitIndex) & 1;
            }
        }
    }

    return unpacked;
}
```

---

## Files Modified

| File | Changes |
|------|---------|
| `src/idc_mosaic/sampler.py` | Added `SegmentInfo`, `SegmentationData`, `cielab_to_rgb()`, `IDCSegmentationSampler` |
| `src/idc_mosaic/generator.py` | Added `--with-segmentations` flag, segmentation metadata in manifest |
| `docs/js/mosaic.js` | Canvas rendering, DICOMweb frame fetching, bit unpacking, overlay compositing |
| `docs/css/style.css` | Added `.tile-canvas` styling |

---

## Usage

```bash
# Generate manifest with TotalSegmentator segmentations
python scripts/generate.py --num-tiles 100 --with-segmentations --seed 42

# Serve locally
python -m http.server 8000 -d docs
# Open http://localhost:8000
```

---

## Technical Notes

### DICOM SEG Frame Structure
- Each frame is a binary mask for one segment on one slice
- Frames are 1-bit packed (LSB first)
- Per-frame Functional Groups link frames to source SOPInstanceUID and segment number

### DICOMweb Endpoints Used
- `GET /studies/{study}/series/{series}/instances` - List instances (QIDO-RS)
- `GET /studies/{study}/series/{series}/instances/{sop}/frames/{n}` - Retrieve frame (WADO-RS)

### Color Conversion
DICOM CIELab (scaled 0-65535) → RGB via XYZ color space with D65 white point and sRGB gamma correction.
