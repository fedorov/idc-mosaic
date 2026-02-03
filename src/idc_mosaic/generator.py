"""Generate mosaic manifest and orchestrate the sampling process."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from idc_index import IDCClient

from .sampler import IDCSampler, IDCSegmentationSampler, DICOMWEB_BASE_URL


def generate_manifest(
    num_tiles: int = 100,
    output_path: str = "docs/data/manifest.json",
    seed: int | None = None,
    with_segmentations: bool = False,
) -> dict:
    """
    Generate a manifest.json file with tile data for the mosaic.

    Args:
        num_tiles: Number of tiles to sample
        output_path: Path to write manifest.json
        seed: Random seed for reproducibility
        with_segmentations: If True, sample only images with TotalSegmentator segmentations

    Returns:
        The generated manifest dictionary
    """
    # Get IDC version
    client = IDCClient()
    idc_version = client.get_idc_version()

    # Sample tiles
    mode = "with TotalSegmentator segmentations" if with_segmentations else "diverse"
    print(f"Sampling {num_tiles} {mode} images from IDC v{idc_version}...")

    if with_segmentations:
        sampler = IDCSegmentationSampler(seed=seed)
    else:
        sampler = IDCSampler(seed=seed)
    samples = sampler.sample(num_tiles)

    print(f"Successfully resolved {len(samples)} tiles")

    # Build manifest
    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "idc_version": f"v{idc_version}" if not str(idc_version).startswith("v") else str(idc_version),
        "total_tiles": len(samples),
        "dicomweb_base_url": DICOMWEB_BASE_URL,
        "has_segmentations": with_segmentations,
        "tiles": [],
    }

    for idx, sample in enumerate(samples):
        tile_data = {
            "index": idx,
            "tile_url": sample.tile_url,
            "viewer_url": sample.viewer_url,
            "modality": sample.modality,
            "body_part": sample.body_part,
            "collection": sample.collection_id,
            "series_uid": sample.series_uid,
            "study_uid": sample.study_uid,
        }

        # Include segmentation data if available
        if sample.segmentation:
            seg = sample.segmentation
            tile_data["segmentation"] = {
                "series_uid": seg.series_uid,
                "sop_uid": seg.sop_uid,
                "algorithm": seg.algorithm,
                "frame_map": seg.frame_map,
                "segments": [
                    {"number": s.number, "label": s.label, "rgb": s.rgb}
                    for s in seg.segments
                ],
            }

        manifest["tiles"].append(tile_data)

    # Write manifest
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Manifest written to {output_path}")

    # Print summary
    if with_segmentations:
        seg_count = sum(1 for t in manifest["tiles"] if "segmentation" in t)
        print(f"\nTiles with segmentations: {seg_count}/{len(manifest['tiles'])}")
        # Show segment count distribution
        seg_counts = [len(t.get("segmentation", {}).get("segments", [])) for t in manifest["tiles"] if "segmentation" in t]
        if seg_counts:
            print(f"Segments per tile: min={min(seg_counts)}, max={max(seg_counts)}, avg={sum(seg_counts)/len(seg_counts):.1f}")
    else:
        modalities = {}
        for tile in manifest["tiles"]:
            mod = tile["modality"]
            modalities[mod] = modalities.get(mod, 0) + 1

        print("\nModality distribution:")
        for mod, count in sorted(modalities.items(), key=lambda x: -x[1]):
            print(f"  {mod}: {count}")

    return manifest


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate IDC Mosaic manifest with diverse imaging samples"
    )
    parser.add_argument(
        "-n",
        "--num-tiles",
        type=int,
        default=100,
        help="Number of tiles to sample (default: 100)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="docs/data/manifest.json",
        help="Output path for manifest.json (default: docs/data/manifest.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--with-segmentations",
        action="store_true",
        help="Sample only CT images with TotalSegmentator segmentations",
    )

    args = parser.parse_args()

    generate_manifest(
        num_tiles=args.num_tiles,
        output_path=args.output,
        seed=args.seed,
        with_segmentations=args.with_segmentations,
    )


if __name__ == "__main__":
    main()
