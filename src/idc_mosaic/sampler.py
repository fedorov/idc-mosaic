"""Stratified sampling of diverse images from IDC."""

import random
from dataclasses import dataclass, asdict, field
from typing import Optional

import requests
from idc_index import IDCClient
from tqdm import tqdm


# DICOMweb public proxy endpoint
DICOMWEB_BASE_URL = (
    "https://proxy.imaging.datacommons.cancer.gov/current/"
    "viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb"
)

# Modalities to include (visual imaging only)
# Note: SM (Slide Microscopy) is handled separately due to its pyramid structure
INCLUDED_MODALITIES = ["CT", "MR", "PT", "CR", "DX", "MG", "US", "XA", "NM"]
INCLUDED_MODALITIES_WITH_SM = INCLUDED_MODALITIES + ["SM"]

# SeriesDescription patterns to exclude (scout, localizer, dose reports, etc.)
EXCLUDED_SERIES_PATTERNS = [
    "scout", "localizer", "topogram", "surview",  # Planning/positioning images
    "dose report", "dose info",  # Dose reports (but not "dose" alone - could be valid PET)
    "screenshot", "secondary capture", "presentation",  # Secondary captures
    "reformat",  # Post-processed derived images
]

# Minimum instance counts by modality for volumetric data
# This helps filter out localizers that may not match SeriesDescription patterns
MIN_INSTANCE_COUNTS = {
    "CT": 20,   # Typical CT has 100+ slices, localizers have 1-3
    "MR": 10,   # MR varies more, but localizers typically have few slices
    "PT": 30,   # PET typically has many slices
}

# SM (Slide Microscopy) ImageType values to exclude
# THUMBNAIL: low-res preview, LABEL: slide label image, OVERVIEW: macro view
SM_EXCLUDED_IMAGE_TYPES = ["THUMBNAIL", "LABEL", "OVERVIEW"]

# Pixel spacing range for SM sampling (in mm)
# Excluding the extremes: very highest resolution (too large) and lowest (no detail)
# Range allows sampling across different magnification levels for variety
SM_PIXEL_SPACING_MIN = 0.0002  # mm - exclude highest res layers (huge files)
SM_PIXEL_SPACING_MAX = 0.01   # mm - exclude lowest res layers (no detail)

# Default variance threshold for content filtering (0-1 normalized)
# Images with variance below this are considered "empty" or low-content
DEFAULT_VARIANCE_THRESHOLD = 0.005

# Maximum retries when sampling to find content-rich images
MAX_CONTENT_RETRIES = 3
# SM retries per pyramid layer before moving to next layer
SM_RETRIES_PER_LAYER = 3
# Maximum pyramid layers to try for SM before giving up
SM_MAX_LAYER_ATTEMPTS = 5
# Minimum tissue percentage for SM tiles (0-100)
# Tiles with less tissue content are considered mostly background
SM_MIN_TISSUE_PERCENT = 15
# SM-specific oversample factor (higher than radiology due to more rejections)
SM_OVERSAMPLE_FACTOR = 3.0


@dataclass
class SegmentInfo:
    """Metadata for a single segment."""

    number: int
    label: str
    rgb: tuple[int, int, int]


@dataclass
class SegmentationData:
    """Segmentation data for a tile."""

    series_uid: str
    sop_uid: str
    algorithm: str
    # Map from segment number to list of DICOMweb frame indices (1-based)
    frame_map: dict[int, int]
    segments: list[SegmentInfo]
    viewer_url: str = ""


@dataclass
class TileSample:
    """Represents a sampled image tile for the mosaic."""

    series_uid: str
    study_uid: str
    sop_uid: str
    modality: str
    body_part: str
    collection_id: str
    instance_count: int
    frame_number: int
    tile_url: str
    viewer_url: str
    segmentation: Optional[SegmentationData] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        # Convert segmentation to serializable format
        if self.segmentation:
            d["segmentation"] = {
                "series_uid": self.segmentation.series_uid,
                "sop_uid": self.segmentation.sop_uid,
                "algorithm": self.segmentation.algorithm,
                "frame_map": self.segmentation.frame_map,
                "segments": [
                    {"number": s.number, "label": s.label, "rgb": list(s.rgb)}
                    for s in self.segmentation.segments
                ],
            }
        return d


def check_image_content(
    tile_url: str,
    variance_threshold: float = DEFAULT_VARIANCE_THRESHOLD,
    is_sm: bool = False,
) -> bool:
    """Check if an image has sufficient content based on pixel variance.

    Fetches the rendered image and computes normalized variance.
    Images with low variance (uniform/empty) are rejected.

    For SM (Slide Microscopy) images, also checks tissue percentage
    to ensure tiles aren't mostly white background.

    Args:
        tile_url: URL to the rendered DICOM frame
        variance_threshold: Minimum normalized variance (0-1) to accept
        is_sm: If True, apply additional SM-specific checks (tissue percentage)

    Returns:
        True if image has sufficient content, False otherwise
    """
    try:
        response = requests.get(tile_url, timeout=10)
        if response.status_code != 200:
            return False

        # Parse image and compute variance
        from io import BytesIO
        from PIL import Image
        import numpy as np

        img = Image.open(BytesIO(response.content))

        # For SM, also check tissue percentage (non-white pixels)
        if is_sm and img.mode == "RGB":
            arr = np.array(img, dtype=np.float32) / 255.0
            # Tissue = pixels where at least one channel is below 0.85
            # (white background has all channels near 1.0)
            tissue_mask = np.any(arr < 0.85, axis=2)
            tissue_percent = np.mean(tissue_mask) * 100
            if tissue_percent < SM_MIN_TISSUE_PERCENT:
                return False

        # Convert to grayscale for variance computation
        if img.mode != "L":
            img = img.convert("L")

        arr = np.array(img, dtype=np.float32) / 255.0
        variance = np.var(arr)

        return variance >= variance_threshold

    except Exception:
        # If we can't check, assume it's okay (fail open)
        return True


class IDCSampler:
    """Samples diverse images from IDC for mosaic generation."""

    def __init__(self, seed: Optional[int] = None, content_filter: bool = True):
        """Initialize sampler with optional random seed for reproducibility.

        Args:
            seed: Random seed for reproducibility
            content_filter: If True, filter out low-content images using variance check
        """
        self.client = IDCClient()
        self.seed = seed
        self.content_filter = content_filter
        if seed is not None:
            random.seed(seed)

    def get_available_strata(self):
        """Query IDC to understand available data distribution.

        Applies filters to exclude low-content images:
        - Scout/localizer images based on SeriesDescription patterns
        - Low instance count volumes (likely localizers for CT/MR/PT)
        - Dose reports, secondary captures, and derived images

        Note: SM (Slide Microscopy) is handled separately in get_available_sm_strata().
        """
        modality_list = ", ".join(f"'{m}'" for m in INCLUDED_MODALITIES)

        # Build exclusion clauses for SeriesDescription patterns
        exclusion_clauses = " AND ".join(
            f"LOWER(COALESCE(SeriesDescription, '')) NOT LIKE '%{pattern}%'"
            for pattern in EXCLUDED_SERIES_PATTERNS
        )

        # Build minimum instance count clauses per modality
        instance_clauses = []
        for mod, min_count in MIN_INSTANCE_COUNTS.items():
            instance_clauses.append(f"(Modality = '{mod}' AND instanceCount >= {min_count})")
        # For modalities not in MIN_INSTANCE_COUNTS, allow any instance count > 0
        other_modalities = [m for m in INCLUDED_MODALITIES if m not in MIN_INSTANCE_COUNTS]
        if other_modalities:
            other_list = ", ".join(f"'{m}'" for m in other_modalities)
            instance_clauses.append(f"(Modality IN ({other_list}) AND instanceCount > 0)")
        instance_clause = " OR ".join(instance_clauses)

        query = f"""
        SELECT
            Modality,
            BodyPartExamined,
            collection_id,
            StudyInstanceUID,
            SeriesInstanceUID,
            instanceCount
        FROM index
        WHERE Modality IN ({modality_list})
          AND Modality IS NOT NULL
          AND ({instance_clause})
          AND {exclusion_clauses}
        """
        return self.client.sql_query(query)

    def get_available_sm_strata(self):
        """Query IDC for SM (Slide Microscopy) data with proper pyramid layer filtering.

        SM images have multiple pyramid layers per series. We filter to:
        - Exclude THUMBNAIL, LABEL, and OVERVIEW image types
        - Select layers within a reasonable pixel spacing range for variety
        - Return instance-level data (SOPInstanceUID) for frame-based sampling
        """
        self.client.fetch_index("sm_instance_index")

        query = f"""
        SELECT
            i.collection_id,
            i.StudyInstanceUID,
            i.SeriesInstanceUID,
            i.BodyPartExamined,
            sm.SOPInstanceUID,
            sm.PixelSpacing_0,
            sm.TotalPixelMatrixColumns,
            sm.TotalPixelMatrixRows,
            sm.ImageType
        FROM index i
        JOIN sm_instance_index sm ON i.SeriesInstanceUID = sm.SeriesInstanceUID
        WHERE i.Modality = 'SM'
          AND sm.PixelSpacing_0 >= {SM_PIXEL_SPACING_MIN}
          AND sm.PixelSpacing_0 <= {SM_PIXEL_SPACING_MAX}
          AND array_to_string(sm.ImageType, ',') LIKE '%VOLUME%'
          AND array_to_string(sm.ImageType, ',') NOT LIKE '%THUMBNAIL%'
          AND array_to_string(sm.ImageType, ',') NOT LIKE '%LABEL%'
          AND array_to_string(sm.ImageType, ',') NOT LIKE '%OVERVIEW%'
        """
        return self.client.sql_query(query)

    def sample(self, n_samples: int, progress: bool = True) -> list[TileSample]:
        """
        Sample n diverse images using proportional sampling.

        Strategy:
        1. Get all available series grouped by modality (radiology + SM separately)
        2. Calculate proportional samples based on series count per modality
        3. For radiology: select frame from middle 60% of volume
        4. For SM: sample pyramid layer and frame with content filtering
        5. Resolve SOPInstanceUID and apply content-based filtering
        6. Oversample by 30% to account for content filtering rejections
        """
        import pandas as pd

        # Oversample to account for content filtering rejections
        oversample_factor = 1.3 if self.content_filter else 1.0
        target_samples = int(n_samples * oversample_factor)

        # Get available data for radiology modalities
        df_radiology = self.get_available_strata()

        # Get available data for SM (slide microscopy)
        try:
            df_sm = self.get_available_sm_strata()
            # For SM, group by series (each series may have multiple pyramid layers)
            df_sm_series = df_sm.drop_duplicates(subset=["SeriesInstanceUID"])
            has_sm = len(df_sm_series) > 0
        except Exception:
            df_sm = pd.DataFrame()
            df_sm_series = pd.DataFrame()
            has_sm = False

        if len(df_radiology) == 0 and not has_sm:
            raise ValueError("No data found in IDC index")

        # Calculate series count per modality
        modality_counts = df_radiology.groupby("Modality").size()
        if has_sm:
            modality_counts["SM"] = len(df_sm_series)
        total_series = modality_counts.sum()

        # Calculate proportional samples per modality (using oversampled target)
        proportions = modality_counts / total_series
        samples_per_modality = (proportions * target_samples).round().astype(int)

        # Ensure at least 1 sample per modality (if we have enough samples)
        if target_samples >= len(modality_counts):
            samples_per_modality = samples_per_modality.clip(lower=1)

        # Adjust to match exactly target_samples
        diff = target_samples - samples_per_modality.sum()
        if diff != 0:
            sorted_modalities = modality_counts.sort_values(ascending=False).index
            for mod in sorted_modalities:
                if diff == 0:
                    break
                if diff > 0:
                    samples_per_modality[mod] += 1
                    diff -= 1
                elif diff < 0 and samples_per_modality[mod] > 1:
                    samples_per_modality[mod] -= 1
                    diff += 1

        # Sample from each modality proportionally
        selected_rows = []
        sm_selected = None

        for modality, n_to_sample in samples_per_modality.items():
            if n_to_sample <= 0:
                continue

            if modality == "SM" and has_sm:
                # For SM, oversample more aggressively due to higher rejection rate
                sm_oversample = int(n_to_sample * SM_OVERSAMPLE_FACTOR) if self.content_filter else n_to_sample
                sm_oversample = min(sm_oversample, len(df_sm_series))
                sm_selected = df_sm_series.sample(n=sm_oversample, random_state=self.seed)
            else:
                modality_df = df_radiology[df_radiology["Modality"] == modality]
                n_to_sample = min(n_to_sample, len(modality_df))
                sampled = modality_df.sample(n=n_to_sample, random_state=self.seed)
                selected_rows.append(sampled)

        # Combine radiology rows
        if selected_rows:
            selected_radiology = pd.concat(selected_rows)
        else:
            selected_radiology = pd.DataFrame()

        # Build TileSample objects
        samples = []
        total_to_process = len(selected_radiology) + (len(sm_selected) if sm_selected is not None else 0)

        if progress:
            pbar = tqdm(total=total_to_process, desc="Resolving tiles")

        # Process radiology samples
        for _, row in selected_radiology.iterrows():
            sample = self._build_tile_sample(row)
            if sample is not None:
                samples.append(sample)
            if progress:
                pbar.update(1)

        # Process SM samples
        if sm_selected is not None and len(sm_selected) > 0:
            for _, row in sm_selected.iterrows():
                sample = self._build_sm_tile_sample(row, df_sm)
                if sample is not None:
                    samples.append(sample)
                if progress:
                    pbar.update(1)

        if progress:
            pbar.close()

        # Shuffle to mix modalities
        random.shuffle(samples)

        return samples[:n_samples]

    def _build_tile_sample(self, row) -> Optional[TileSample]:
        """Build a TileSample from a dataframe row.

        For volumetric data (CT, MR, PT), samples from the middle 60% of the volume
        to avoid edge slices that often have less content. Applies content filtering
        with retries to find frames with sufficient tissue/content.
        """
        study_uid = row["StudyInstanceUID"]
        series_uid = row["SeriesInstanceUID"]
        instance_count = int(row["instanceCount"])

        # For volumes, we may retry with different slices if content filtering is enabled
        max_retries = MAX_CONTENT_RETRIES if self.content_filter and instance_count > 1 else 1

        for attempt in range(max_retries):
            # Select frame - for volumes, sample from middle 60% to avoid edge slices
            frame_number = 1
            frame_index = 0
            if instance_count > 1:
                # Middle 60%: from 20% to 80% of the volume
                start_idx = int(instance_count * 0.2)
                end_idx = int(instance_count * 0.8)
                if end_idx <= start_idx:
                    end_idx = start_idx + 1
                frame_index = random.randint(start_idx, end_idx - 1)

            # Get SOPInstanceUID via DICOMweb
            sop_uid = self._get_sop_instance_uid(study_uid, series_uid, frame_index)
            if sop_uid is None:
                continue

            # Build URLs
            tile_url = self._build_tile_url(study_uid, series_uid, sop_uid, frame_number)

            # Content filtering: check if image has sufficient content
            if self.content_filter:
                if check_image_content(tile_url):
                    break  # Found good content
                # Try again with a different slice
            else:
                break

        else:
            # All retries exhausted, use last attempt anyway
            if sop_uid is None:
                return None

        viewer_url = self.client.get_viewer_URL(seriesInstanceUID=series_uid)

        return TileSample(
            series_uid=series_uid,
            study_uid=study_uid,
            sop_uid=sop_uid,
            modality=row["Modality"],
            body_part=row["BodyPartExamined"] or "UNKNOWN",
            collection_id=row["collection_id"],
            instance_count=instance_count,
            frame_number=frame_number,
            tile_url=tile_url,
            viewer_url=viewer_url,
        )

    def _build_sm_tile_sample(self, series_row, df_sm) -> Optional[TileSample]:
        """Build a TileSample for SM (Slide Microscopy) data.

        SM images have pyramid layers with multiple frames (tiles) per layer.
        Strategy:
        1. Start with a random pyramid layer
        2. Try SM_RETRIES_PER_LAYER frames from the central region
        3. If no content found, move to the next lower resolution layer (higher pixel spacing)
        4. Repeat up to SM_MAX_LAYER_ATTEMPTS times
        """
        series_uid = series_row["SeriesInstanceUID"]
        study_uid = series_row["StudyInstanceUID"]

        # Get all pyramid layers for this series, sorted by pixel spacing (low to high res)
        series_layers = df_sm[df_sm["SeriesInstanceUID"] == series_uid].copy()
        if len(series_layers) == 0:
            return None

        # Sort by PixelSpacing_0 ascending (smallest = highest resolution first)
        series_layers = series_layers.sort_values("PixelSpacing_0", ascending=True)

        # Start with a random layer
        start_idx = random.randint(0, len(series_layers) - 1)

        tile_url = None
        frame_number = 1
        found_content = False
        final_sop_uid = None
        final_total_frames = 1

        # Try multiple pyramid layers if content filtering is enabled
        max_layer_attempts = SM_MAX_LAYER_ATTEMPTS if self.content_filter else 1

        for layer_attempt in range(max_layer_attempts):
            # Select layer: start at random, then move to lower resolution layers
            layer_idx = min(start_idx + layer_attempt, len(series_layers) - 1)
            layer = series_layers.iloc[layer_idx]

            sop_uid = layer["SOPInstanceUID"]
            total_cols = int(layer["TotalPixelMatrixColumns"])
            total_rows = int(layer["TotalPixelMatrixRows"])

            # SM images are organized as frames (tiles), typically 256x256 or 512x512 pixels
            tile_size = 512
            n_tiles_x = max(1, total_cols // tile_size)
            n_tiles_y = max(1, total_rows // tile_size)
            total_frames = n_tiles_x * n_tiles_y

            # Sample frame from central 60% to avoid edges (often background)
            start_x = int(n_tiles_x * 0.2)
            end_x = int(n_tiles_x * 0.8)
            start_y = int(n_tiles_y * 0.2)
            end_y = int(n_tiles_y * 0.8)

            if end_x <= start_x:
                end_x = start_x + 1
            if end_y <= start_y:
                end_y = start_y + 1

            # Try multiple frames at this layer
            retries_per_layer = SM_RETRIES_PER_LAYER if self.content_filter else 1

            for attempt in range(retries_per_layer):
                tile_x = random.randint(start_x, end_x - 1)
                tile_y = random.randint(start_y, end_y - 1)

                # Frame number (1-indexed, row-major order)
                frame_number = tile_y * n_tiles_x + tile_x + 1

                # Build tile URL
                tile_url = self._build_tile_url(study_uid, series_uid, sop_uid, frame_number)

                # Content filtering: check if frame has tissue (not background)
                # For SM, also check tissue percentage in addition to variance
                if self.content_filter:
                    if check_image_content(tile_url, is_sm=True):
                        found_content = True
                        final_sop_uid = sop_uid
                        final_total_frames = total_frames
                        break  # Found good content
                else:
                    found_content = True
                    final_sop_uid = sop_uid
                    final_total_frames = total_frames
                    break

            if found_content:
                break  # Found content, stop trying layers

        # If content filtering is enabled and we couldn't find good content, skip this tile
        if self.content_filter and not found_content:
            return None

        viewer_url = self.client.get_viewer_URL(seriesInstanceUID=series_uid)

        return TileSample(
            series_uid=series_uid,
            study_uid=study_uid,
            sop_uid=final_sop_uid,
            modality="SM",
            body_part=series_row.get("BodyPartExamined") or "UNKNOWN",
            collection_id=series_row["collection_id"],
            instance_count=final_total_frames,
            frame_number=frame_number,
            tile_url=tile_url,
            viewer_url=viewer_url,
        )

    def _get_sop_instance_uid(
        self, study_uid: str, series_uid: str, frame_index: int
    ) -> Optional[str]:
        """Query DICOMweb to get SOPInstanceUID for a specific instance."""
        url = f"{DICOMWEB_BASE_URL}/studies/{study_uid}/series/{series_uid}/instances"
        params = {"limit": frame_index + 1}

        try:
            response = requests.get(
                url,
                params=params,
                headers={"Accept": "application/dicom+json"},
                timeout=30,
            )
            if response.status_code == 200:
                instances = response.json()
                if instances and len(instances) > frame_index:
                    # SOPInstanceUID tag is 00080018
                    return instances[frame_index].get("00080018", {}).get("Value", [None])[0]
        except Exception:
            pass

        return None

    def _build_tile_url(
        self, study_uid: str, series_uid: str, sop_uid: str, frame_number: int
    ) -> str:
        """Build the DICOMweb rendered frame URL."""
        return (
            f"{DICOMWEB_BASE_URL}/studies/{study_uid}/series/{series_uid}/"
            f"instances/{sop_uid}/frames/{frame_number}/rendered"
        )


def cielab_to_rgb(L: int, a: int, b: int) -> tuple[int, int, int]:
    """Convert DICOM CIELab (scaled 0-65535) to RGB."""
    # DICOM CIELab uses scaled values (0-65535 for L, a, b shifted by 32768)
    L_norm = L / 65535.0 * 100.0
    a_norm = (a - 32768) / 256.0
    b_norm = (b - 32768) / 256.0

    # CIELab to XYZ
    y = (L_norm + 16) / 116
    x = a_norm / 500 + y
    z = y - b_norm / 200

    def f_inv(t):
        delta = 6 / 29
        if t > delta:
            return t**3
        return 3 * delta**2 * (t - 4 / 29)

    # D65 white point
    X = 95.047 * f_inv(x)
    Y = 100.000 * f_inv(y)
    Z = 108.883 * f_inv(z)

    # XYZ to sRGB
    r = 3.2406 * X / 100 - 1.5372 * Y / 100 - 0.4986 * Z / 100
    g = -0.9689 * X / 100 + 1.8758 * Y / 100 + 0.0415 * Z / 100
    bl = 0.0557 * X / 100 - 0.2040 * Y / 100 + 1.0570 * Z / 100

    # Gamma correction
    def gamma(u):
        if u > 0.0031308:
            return 1.055 * (u ** (1 / 2.4)) - 0.055
        return 12.92 * u

    return (
        int(max(0, min(255, gamma(r) * 255))),
        int(max(0, min(255, gamma(g) * 255))),
        int(max(0, min(255, gamma(bl) * 255))),
    )


class IDCSegmentationSampler:
    """Samples CT images with TotalSegmentator segmentations from IDC."""

    def __init__(self, seed: Optional[int] = None):
        """Initialize sampler with optional random seed for reproducibility."""
        self.client = IDCClient()
        self.seed = seed
        if seed is not None:
            random.seed(seed)
        # Fetch seg_index for segmentation metadata
        self.client.fetch_index("seg_index")

    def get_available_segmented_series(self):
        """Query IDC for CT series that have TotalSegmentator segmentations."""
        query = """
        SELECT
            src.collection_id,
            src.StudyInstanceUID,
            src.SeriesInstanceUID as source_series,
            src.instanceCount as source_instances,
            src.BodyPartExamined,
            seg.SeriesInstanceUID as seg_series,
            seg.AlgorithmName,
            seg.total_segments
        FROM seg_index seg
        JOIN index src ON seg.segmented_SeriesInstanceUID = src.SeriesInstanceUID
        WHERE seg.AlgorithmName LIKE '%TotalSegmentator%'
          AND src.instanceCount > 10
        """
        return self.client.sql_query(query)

    def sample(self, n_samples: int, progress: bool = True) -> list[TileSample]:
        """
        Sample n CT images with TotalSegmentator segmentations.

        For each sampled image, includes segmentation metadata needed
        for runtime overlay rendering.
        """
        import pandas as pd

        # Get available segmented series
        df = self.get_available_segmented_series()

        if len(df) == 0:
            raise ValueError("No TotalSegmentator segmentations found in IDC")

        # Sample randomly
        n_to_sample = min(n_samples, len(df))
        selected = df.sample(n=n_to_sample, random_state=self.seed)

        # Build TileSample objects with segmentation data
        samples = []
        iterator = (
            tqdm(selected.iterrows(), total=len(selected), desc="Resolving tiles")
            if progress
            else selected.iterrows()
        )

        for _, row in iterator:
            sample = self._build_tile_sample_with_segmentation(row)
            if sample is not None:
                samples.append(sample)

        return samples

    def _build_tile_sample_with_segmentation(self, row) -> Optional[TileSample]:
        """Build a TileSample with segmentation data from a dataframe row."""
        study_uid = row["StudyInstanceUID"]
        source_series_uid = row["source_series"]
        seg_series_uid = row["seg_series"]
        source_instances = int(row["source_instances"])

        # Select middle slice for the source image
        frame_number = 1
        frame_index = source_instances // 2

        # Get SOPInstanceUID for the source image
        source_sop_uid = self._get_sop_instance_uid(
            study_uid, source_series_uid, frame_index
        )
        if source_sop_uid is None:
            return None

        # Get segmentation metadata (SOP UID and frame mapping)
        seg_data = self._get_segmentation_data(
            study_uid, seg_series_uid, source_sop_uid
        )

        # Add viewer URL to segmentation data if available
        if seg_data:
            seg_data.viewer_url = self.client.get_viewer_URL(
                seriesInstanceUID=seg_series_uid
            )

        # Build URLs
        tile_url = self._build_tile_url(
            study_uid, source_series_uid, source_sop_uid, frame_number
        )
        viewer_url = self.client.get_viewer_URL(seriesInstanceUID=source_series_uid)

        return TileSample(
            series_uid=source_series_uid,
            study_uid=study_uid,
            sop_uid=source_sop_uid,
            modality="CT",
            body_part=row["BodyPartExamined"] or "UNKNOWN",
            collection_id=row["collection_id"],
            instance_count=source_instances,
            frame_number=frame_number,
            tile_url=tile_url,
            viewer_url=viewer_url,
            segmentation=seg_data,
        )

    def _get_sop_instance_uid(
        self, study_uid: str, series_uid: str, frame_index: int
    ) -> Optional[str]:
        """Query DICOMweb to get SOPInstanceUID for a specific instance."""
        url = f"{DICOMWEB_BASE_URL}/studies/{study_uid}/series/{series_uid}/instances"
        params = {"limit": frame_index + 1}

        try:
            response = requests.get(
                url,
                params=params,
                headers={"Accept": "application/dicom+json"},
                timeout=30,
            )
            if response.status_code == 200:
                instances = response.json()
                if instances and len(instances) > frame_index:
                    return (
                        instances[frame_index].get("00080018", {}).get("Value", [None])[0]
                    )
        except Exception:
            pass

        return None

    def _get_segmentation_data(
        self, study_uid: str, seg_series_uid: str, source_sop_uid: str
    ) -> Optional[SegmentationData]:
        """
        Get segmentation metadata for a specific source image.

        Downloads the SEG DICOM to parse Per-frame Functional Groups
        and build the frame mapping.
        """
        # Get SEG instance SOP UID
        url = f"{DICOMWEB_BASE_URL}/studies/{study_uid}/series/{seg_series_uid}/instances"
        try:
            response = requests.get(
                url,
                headers={"Accept": "application/dicom+json"},
                timeout=30,
            )
            if response.status_code != 200:
                return None

            instances = response.json()
            if not instances:
                return None

            seg_sop_uid = instances[0].get("00080018", {}).get("Value", [None])[0]
            if not seg_sop_uid:
                return None

            # Download and parse the SEG DICOM to get frame mapping
            # We need to download because Per-frame FG isn't in metadata API
            seg_data = self._download_and_parse_seg(
                study_uid, seg_series_uid, seg_sop_uid, source_sop_uid
            )
            return seg_data

        except Exception:
            return None

    def _download_and_parse_seg(
        self,
        study_uid: str,
        seg_series_uid: str,
        seg_sop_uid: str,
        source_sop_uid: str,
    ) -> Optional[SegmentationData]:
        """Download SEG DICOM and extract frame mapping for target source image."""
        import tempfile
        import os

        try:
            # Download the SEG series
            with tempfile.TemporaryDirectory() as tmpdir:
                self.client.download_from_selection(
                    seriesInstanceUID=[seg_series_uid],
                    downloadDir=tmpdir,
                    dirTemplate="",
                    show_progress_bar=False,
                )

                # Find the downloaded file
                files = [f for f in os.listdir(tmpdir) if f.endswith(".dcm")]
                if not files:
                    return None

                import pydicom

                ds = pydicom.dcmread(os.path.join(tmpdir, files[0]))

                # Build mapping: source SOP UID -> list of (frame_idx, segment_num)
                frame_map = {}  # segment_num -> frame_index (1-based for DICOMweb)
                pffgs = ds.PerFrameFunctionalGroupsSequence

                for frame_idx, fg in enumerate(pffgs):
                    seg_num = fg.SegmentIdentificationSequence[0].ReferencedSegmentNumber
                    deriv = fg.DerivationImageSequence[0]
                    src = deriv.SourceImageSequence[0]
                    ref_sop = src.ReferencedSOPInstanceUID

                    if ref_sop == source_sop_uid:
                        # DICOMweb frames are 1-indexed
                        frame_map[seg_num] = frame_idx + 1

                if not frame_map:
                    return None

                # Extract segment metadata (labels and colors)
                segments = []
                for seg in ds.SegmentSequence:
                    seg_num = seg.SegmentNumber
                    if seg_num not in frame_map:
                        continue

                    label = seg.SegmentLabel
                    if hasattr(seg, "RecommendedDisplayCIELabValue"):
                        cielab = seg.RecommendedDisplayCIELabValue
                        rgb = cielab_to_rgb(*cielab)
                    else:
                        # Default color if none specified
                        rgb = (255, 0, 0)

                    segments.append(SegmentInfo(number=seg_num, label=label, rgb=rgb))

                return SegmentationData(
                    series_uid=seg_series_uid,
                    sop_uid=seg_sop_uid,
                    algorithm="TotalSegmentator",
                    frame_map=frame_map,
                    segments=segments,
                )

        except Exception:
            return None

    def _build_tile_url(
        self, study_uid: str, series_uid: str, sop_uid: str, frame_number: int
    ) -> str:
        """Build the DICOMweb rendered frame URL."""
        return (
            f"{DICOMWEB_BASE_URL}/studies/{study_uid}/series/{series_uid}/"
            f"instances/{sop_uid}/frames/{frame_number}/rendered"
        )
