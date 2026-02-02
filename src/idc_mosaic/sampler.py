"""Stratified sampling of diverse images from IDC."""

import random
from dataclasses import dataclass, asdict
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

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


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
