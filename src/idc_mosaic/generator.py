"""Generate mosaic manifest and orchestrate the sampling process."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from idc_index import IDCClient

from .sampler import IDCSampler


def generate_manifest(
    num_tiles: int = 100,
    output_path: str = "docs/data/manifest.json",
    seed: int | None = None,
) -> dict:
    """
    Generate a manifest.json file with tile data for the mosaic.

    Args:
        num_tiles: Number of tiles to sample
        output_path: Path to write manifest.json
        seed: Random seed for reproducibility

    Returns:
        The generated manifest dictionary
    """
    # Get IDC version
    client = IDCClient()
    idc_version = client.get_idc_version()

    # Sample tiles
    print(f"Sampling {num_tiles} diverse images from IDC v{idc_version}...")
    sampler = IDCSampler(seed=seed)
    samples = sampler.sample(num_tiles)

    print(f"Successfully resolved {len(samples)} tiles")

    # Build manifest
    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "idc_version": f"v{idc_version}" if not str(idc_version).startswith("v") else str(idc_version),
        "total_tiles": len(samples),
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
        }
        manifest["tiles"].append(tile_data)

    # Write manifest
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Manifest written to {output_path}")

    # Print summary
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

    args = parser.parse_args()

    generate_manifest(
        num_tiles=args.num_tiles,
        output_path=args.output,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
