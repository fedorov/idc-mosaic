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
INCLUDED_MODALITIES = ["CT", "MR", "PT", "CR", "DX", "MG", "US", "SM", "XA", "NM"]


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


class IDCSampler:
    """Samples diverse images from IDC for mosaic generation."""

    def __init__(self, seed: Optional[int] = None):
        """Initialize sampler with optional random seed for reproducibility."""
        self.client = IDCClient()
        self.seed = seed
        if seed is not None:
            random.seed(seed)

    def get_available_strata(self):
        """Query IDC to understand available data distribution."""
        modality_list = ", ".join(f"'{m}'" for m in INCLUDED_MODALITIES)

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
          AND instanceCount > 0
        """
        return self.client.sql_query(query)

    def sample(self, n_samples: int, progress: bool = True) -> list[TileSample]:
        """
        Sample n diverse images using proportional sampling.

        Strategy:
        1. Get all available series grouped by modality
        2. Calculate proportional samples based on series count per modality
        3. For each selected series, determine frame and resolve SOPInstanceUID
        """
        import pandas as pd

        # Get available data
        df = self.get_available_strata()

        if len(df) == 0:
            raise ValueError("No data found in IDC index")

        # Calculate series count per modality
        modality_counts = df.groupby("Modality").size()
        total_series = modality_counts.sum()

        # Calculate proportional samples per modality
        # Ensure at least 1 sample per modality if possible
        proportions = modality_counts / total_series
        samples_per_modality = (proportions * n_samples).round().astype(int)

        # Ensure at least 1 sample per modality (if we have enough samples)
        if n_samples >= len(modality_counts):
            samples_per_modality = samples_per_modality.clip(lower=1)

        # Adjust to match exactly n_samples
        diff = n_samples - samples_per_modality.sum()
        if diff != 0:
            # Add/remove from largest modalities first
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
        for modality, n_to_sample in samples_per_modality.items():
            if n_to_sample <= 0:
                continue
            modality_df = df[df["Modality"] == modality]
            n_to_sample = min(n_to_sample, len(modality_df))
            sampled = modality_df.sample(n=n_to_sample, random_state=self.seed)
            selected_rows.append(sampled)

        # Combine all selected rows
        selected = pd.concat(selected_rows).head(n_samples)

        # Shuffle to mix modalities
        selected = selected.sample(frac=1, random_state=self.seed)

        # Build TileSample objects
        samples = []
        iterator = tqdm(selected.iterrows(), total=len(selected), desc="Resolving tiles") if progress else selected.iterrows()

        for _, row in iterator:
            sample = self._build_tile_sample(row)
            if sample is not None:
                samples.append(sample)

        return samples

    def _build_tile_sample(self, row) -> Optional[TileSample]:
        """Build a TileSample from a dataframe row."""
        study_uid = row["StudyInstanceUID"]
        series_uid = row["SeriesInstanceUID"]
        instance_count = int(row["instanceCount"])

        # Select frame (middle slice for volumes)
        frame_number = 1
        frame_index = 0
        if instance_count > 1:
            frame_index = instance_count // 2

        # Get SOPInstanceUID via DICOMweb
        sop_uid = self._get_sop_instance_uid(study_uid, series_uid, frame_index)
        if sop_uid is None:
            return None

        # Build URLs
        tile_url = self._build_tile_url(study_uid, series_uid, sop_uid, frame_number)
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
