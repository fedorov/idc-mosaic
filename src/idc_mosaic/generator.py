"""Generate mosaic manifest and orchestrate the sampling process."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from idc_index import IDCClient
from tqdm import tqdm

from .sampler import IDCSampler, IDCSegmentationSampler, DICOMWEB_BASE_URL


def generate_citations_file(manifest: dict, output_dir: str, client: IDCClient) -> dict:
    """
    Generate a citations.json file with formatted citations for all unique DOIs.

    Args:
        manifest: The manifest dictionary containing tiles with source_doi
        output_dir: Directory to write citations.json
        client: IDCClient instance

    Returns:
        The citations dictionary
    """
    # Collect unique DOIs
    unique_dois = set()
    for tile in manifest["tiles"]:
        doi = tile.get("source_doi")
        if doi:
            unique_dois.add(doi)

    if not unique_dois:
        print("No source DOIs found in manifest")
        return {}

    print(f"Generating citations for {len(unique_dois)} unique DOIs...")

    citations = {}

    for doi in unique_dois:
        try:
            # Get APA citation
            apa_citations = client.citations_from_selection(
                collection_id=None,
                seriesInstanceUID=None,
                # We need to find a series with this DOI to get the citation
            )
        except Exception:
            pass

    # Query series for each DOI and get citations
    for doi in unique_dois:
        try:
            # Find one series with this DOI
            series_query = f"""
                SELECT SeriesInstanceUID
                FROM index
                WHERE source_DOI = '{doi}'
                LIMIT 1
            """
            series_df = client.sql_query(series_query)
            if series_df.empty:
                continue

            series_uid = series_df.iloc[0]['SeriesInstanceUID']

            # Get APA citation
            apa_list = client.citations_from_selection(
                seriesInstanceUID=[series_uid],
                citation_format=IDCClient.CITATION_FORMAT_APA
            )
            apa = apa_list[0] if apa_list else None

            # Get BibTeX citation
            bibtex_list = client.citations_from_selection(
                seriesInstanceUID=[series_uid],
                citation_format=IDCClient.CITATION_FORMAT_BIBTEX
            )
            bibtex = bibtex_list[0] if bibtex_list else None

            citations[doi] = {
                "doi": doi,
                "url": f"https://doi.org/{doi}",
                "apa": apa,
                "bibtex": bibtex,
            }
        except Exception as e:
            print(f"Warning: Failed to get citation for DOI {doi}: {e}")
            # Still include basic DOI info
            citations[doi] = {
                "doi": doi,
                "url": f"https://doi.org/{doi}",
                "apa": None,
                "bibtex": None,
            }

    # Write citations file
    output = Path(output_dir) / "citations.json"
    with open(output, "w") as f:
        json.dump(citations, f, indent=2)

    print(f"Citations written to {output}")

    return citations


def update_viewer_urls(manifest_path: str, output_path: str | None = None) -> dict:
    """
    Update viewer URLs in an existing manifest without regenerating segmentation data.

    This is much faster than regenerating the full manifest since it only needs
    to query idc-index for viewer URLs, not download SEG DICOM files.

    Args:
        manifest_path: Path to existing manifest.json
        output_path: Path to write updated manifest (defaults to manifest_path)

    Returns:
        The updated manifest dictionary
    """
    if output_path is None:
        output_path = manifest_path

    # Load existing manifest
    with open(manifest_path) as f:
        manifest = json.load(f)

    client = IDCClient()

    print(f"Updating viewer URLs for {len(manifest['tiles'])} tiles...")

    for tile in tqdm(manifest["tiles"], desc="Updating URLs"):
        # Update main viewer URL
        tile["viewer_url"] = client.get_viewer_URL(
            seriesInstanceUID=tile["series_uid"]
        )

        # Update segmentation viewer URL if present
        if "segmentation" in tile:
            tile["segmentation"]["viewer_url"] = client.get_viewer_URL(
                seriesInstanceUID=tile["segmentation"]["series_uid"]
            )

    # Update timestamp
    manifest["generated"] = datetime.now(timezone.utc).isoformat()

    # Write updated manifest
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Updated manifest written to {output_path}")

    return manifest


def generate_manifest(
    num_tiles: int = 100,
    output_path: str = "docs/data/manifest.json",
    seed: int | None = None,
    with_segmentations: bool = False,
    content_filter: bool = True,
) -> dict:
    """
    Generate a manifest.json file with tile data for the mosaic.

    Args:
        num_tiles: Number of tiles to sample
        output_path: Path to write manifest.json
        seed: Random seed for reproducibility
        with_segmentations: If True, sample only images with TotalSegmentator segmentations
        content_filter: If True, filter out low-content images using variance check

    Returns:
        The generated manifest dictionary
    """
    # Get IDC version
    client = IDCClient()
    idc_version = client.get_idc_version()

    # Sample tiles
    mode = "with TotalSegmentator segmentations" if with_segmentations else "diverse"
    filter_msg = " with content filtering" if content_filter else ""
    print(f"Sampling {num_tiles} {mode} images from IDC v{idc_version}{filter_msg}...")

    if with_segmentations:
        sampler = IDCSegmentationSampler(seed=seed)
    else:
        sampler = IDCSampler(seed=seed, content_filter=content_filter)
    samples = sampler.sample(num_tiles)

    print(f"Successfully resolved {len(samples)} tiles")

    # Batch query source_DOI for all series
    print("Fetching source DOIs...")
    series_uids = [s.series_uid for s in samples]
    doi_query = f"""
        SELECT SeriesInstanceUID, source_DOI
        FROM index
        WHERE SeriesInstanceUID IN ({','.join(f"'{uid}'" for uid in series_uids)})
    """
    doi_df = client.sql_query(doi_query)
    doi_map = dict(zip(doi_df['SeriesInstanceUID'], doi_df['source_DOI']))

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
            "source_doi": doi_map.get(sample.series_uid),
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
                "viewer_url": seg.viewer_url,
            }

        manifest["tiles"].append(tile_data)

    # Write manifest
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Manifest written to {output_path}")

    # Generate citations file
    generate_citations_file(manifest, str(output.parent), client)

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
    parser.add_argument(
        "--update-urls",
        type=str,
        metavar="MANIFEST",
        help="Update viewer URLs in existing manifest (skip regeneration)",
    )
    parser.add_argument(
        "--no-content-filter",
        action="store_true",
        help="Disable content-based filtering (faster but may include empty tiles)",
    )

    args = parser.parse_args()

    if args.update_urls:
        # Fast path: just update URLs in existing manifest
        update_viewer_urls(
            manifest_path=args.update_urls,
            output_path=args.output if args.output != "docs/data/manifest.json" else None,
        )
    else:
        generate_manifest(
            num_tiles=args.num_tiles,
            output_path=args.output,
            seed=args.seed,
            with_segmentations=args.with_segmentations,
            content_filter=not args.no_content_filter,
        )


if __name__ == "__main__":
    main()
