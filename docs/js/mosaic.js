/**
 * IDC Mosaic - Interactive tile grid with DICOMweb image loading
 * Supports segmentation overlay rendering
 */

(function () {
    'use strict';

    // Configuration
    const MANIFEST_URL = 'data/manifest.json';
    const SEGMENTATION_OPACITY = 0.5;

    // DOM elements
    const mosaicGrid = document.getElementById('mosaic');
    const gridSelect = document.getElementById('grid-select');
    const statsElement = document.getElementById('stats');
    const loadingElement = document.getElementById('loading');
    const errorElement = document.getElementById('error');

    // State
    let manifestData = null;
    let currentGridSize = 8;

    /**
     * Initialize the mosaic
     */
    async function init() {
        // Read grid size from URL params
        const params = new URLSearchParams(window.location.search);
        const colsParam = params.get('cols');
        if (colsParam) {
            const cols = parseInt(colsParam, 10);
            if ([4, 6, 8, 10, 12].includes(cols)) {
                currentGridSize = cols;
                gridSelect.value = cols.toString();
            }
        }

        // Set up grid size change handler
        gridSelect.addEventListener('change', (e) => {
            currentGridSize = parseInt(e.target.value, 10);
            updateURL();
            renderMosaic();
        });

        // Load manifest and render
        try {
            manifestData = await loadManifest();
            hideLoading();
            renderMosaic();
        } catch (error) {
            hideLoading();
            showError(`Failed to load mosaic data: ${error.message}`);
        }
    }

    /**
     * Load the manifest.json file
     */
    async function loadManifest() {
        const response = await fetch(MANIFEST_URL);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
    }

    /**
     * Render the mosaic grid with tiles
     */
    function renderMosaic() {
        if (!manifestData || !manifestData.tiles) {
            return;
        }

        // Clear existing tiles
        mosaicGrid.innerHTML = '';

        // Set grid columns
        document.documentElement.style.setProperty('--cols', currentGridSize);

        // Calculate how many tiles to show
        const numTiles = currentGridSize * currentGridSize;
        const tilesToShow = manifestData.tiles.slice(0, numTiles);

        // Update stats
        const hasSegs = manifestData.has_segmentations;
        updateStats(tilesToShow.length, manifestData.total_tiles, hasSegs);

        // Create tiles
        tilesToShow.forEach((tileData, index) => {
            const tile = createTile(tileData, index);
            mosaicGrid.appendChild(tile);
        });
    }

    /**
     * Create a single tile element
     */
    function createTile(tileData, index) {
        const tile = document.createElement('div');
        tile.className = 'tile loading';
        tile.setAttribute('role', 'button');
        tile.setAttribute('tabindex', '0');
        tile.title = formatTooltip(tileData);

        // Create canvas for rendering (needed for segmentation overlay)
        const canvas = document.createElement('canvas');
        canvas.className = 'tile-canvas';
        tile.appendChild(canvas);

        // Load image and optionally segmentation
        if (tileData.segmentation && manifestData.dicomweb_base_url) {
            loadTileWithSegmentation(tile, canvas, tileData, manifestData.dicomweb_base_url);
        } else {
            loadTileImage(tile, canvas, tileData.tile_url);
        }

        // Click handler - open IDC viewer
        const openViewer = () => {
            window.open(tileData.viewer_url, '_blank', 'noopener');
        };

        tile.addEventListener('click', openViewer);
        tile.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openViewer();
            }
        });

        return tile;
    }

    /**
     * Load tile image without segmentation
     */
    function loadTileImage(tile, canvas, url) {
        const img = new Image();
        img.crossOrigin = 'anonymous';

        img.onload = () => {
            // Set canvas size to match image
            canvas.width = img.width;
            canvas.height = img.height;

            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0);

            tile.classList.remove('loading');
        };

        img.onerror = () => {
            tile.classList.remove('loading');
            tile.classList.add('error');
        };

        img.src = url;
    }

    /**
     * Load tile image with segmentation overlay
     */
    async function loadTileWithSegmentation(tile, canvas, tileData, dicomwebBaseUrl) {
        try {
            // Load base image first
            const baseImage = await loadImageAsync(tileData.tile_url);

            // Set canvas size
            canvas.width = baseImage.width;
            canvas.height = baseImage.height;

            const ctx = canvas.getContext('2d');

            // Draw base image
            ctx.drawImage(baseImage, 0, 0);

            // Load and overlay segmentation frames
            const seg = tileData.segmentation;
            const segFrames = await loadSegmentationFrames(
                dicomwebBaseUrl,
                tileData.study_uid,
                seg.series_uid,
                seg.sop_uid,
                seg.frame_map,
                baseImage.width,
                baseImage.height
            );

            // Create overlay with colored segments
            if (segFrames && Object.keys(segFrames).length > 0) {
                renderSegmentationOverlay(ctx, segFrames, seg.segments, baseImage.width, baseImage.height);
            }

            tile.classList.remove('loading');
        } catch (error) {
            console.error('Error loading tile with segmentation:', error);
            // Fall back to just showing the base image
            loadTileImage(tile, canvas, tileData.tile_url);
        }
    }

    /**
     * Load an image and return a promise
     */
    function loadImageAsync(url) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = url;
        });
    }

    /**
     * Load segmentation frames from DICOMweb
     * Returns map of segment number -> Uint8Array mask
     */
    async function loadSegmentationFrames(baseUrl, studyUid, seriesUid, sopUid, frameMap, width, height) {
        const frames = {};

        // Load frames in parallel with concurrency limit
        const entries = Object.entries(frameMap);
        const batchSize = 5;

        for (let i = 0; i < entries.length; i += batchSize) {
            const batch = entries.slice(i, i + batchSize);
            const promises = batch.map(async ([segNum, frameNum]) => {
                try {
                    const mask = await fetchSegmentationFrame(
                        baseUrl, studyUid, seriesUid, sopUid, frameNum, width, height
                    );
                    if (mask) {
                        frames[parseInt(segNum)] = mask;
                    }
                } catch (e) {
                    console.warn(`Failed to load seg frame ${frameNum}:`, e);
                }
            });
            await Promise.all(promises);
        }

        return frames;
    }

    /**
     * Fetch a single segmentation frame from DICOMweb
     */
    async function fetchSegmentationFrame(baseUrl, studyUid, seriesUid, sopUid, frameNum, width, height) {
        const url = `${baseUrl}/studies/${studyUid}/series/${seriesUid}/instances/${sopUid}/frames/${frameNum}`;

        const response = await fetch(url, {
            headers: {
                'Accept': 'multipart/related; type="application/octet-stream"'
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        // Parse multipart response to get raw frame data
        const buffer = await response.arrayBuffer();
        const frameData = parseMultipartFrame(buffer);

        if (!frameData) {
            return null;
        }

        // Unpack 1-bit data to byte array
        return unpackBitData(frameData, width, height);
    }

    /**
     * Parse multipart response to extract frame data
     */
    function parseMultipartFrame(buffer) {
        const bytes = new Uint8Array(buffer);

        // Find boundary in the response
        // Format: --boundary\r\nContent-Type: ...\r\n\r\n<data>\r\n--boundary--
        // Look for double CRLF that separates headers from data
        let dataStart = -1;
        for (let i = 0; i < bytes.length - 4; i++) {
            if (bytes[i] === 0x0d && bytes[i + 1] === 0x0a &&
                bytes[i + 2] === 0x0d && bytes[i + 3] === 0x0a) {
                dataStart = i + 4;
                break;
            }
        }

        if (dataStart === -1) {
            return null;
        }

        // Find end boundary (starts with \r\n--)
        let dataEnd = bytes.length;
        for (let i = dataStart; i < bytes.length - 4; i++) {
            if (bytes[i] === 0x0d && bytes[i + 1] === 0x0a &&
                bytes[i + 2] === 0x2d && bytes[i + 3] === 0x2d) {
                dataEnd = i;
                break;
            }
        }

        return bytes.slice(dataStart, dataEnd);
    }

    /**
     * Unpack 1-bit packed data to byte array (0 or 1 per pixel)
     * Flips vertically to account for SEG having inverted Y orientation
     */
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
                    // LSB first bit ordering (common for DICOM SEG)
                    unpacked[dstIdx] = (packedData[byteIndex] >> bitIndex) & 1;
                }
            }
        }

        return unpacked;
    }

    /**
     * Render segmentation overlay on canvas
     */
    function renderSegmentationOverlay(ctx, frames, segments, width, height) {
        // Create ImageData for the overlay
        const imageData = ctx.getImageData(0, 0, width, height);
        const data = imageData.data;

        // Build segment number to RGB map
        const colorMap = {};
        for (const seg of segments) {
            colorMap[seg.number] = seg.rgb;
        }

        // Apply each segment's mask with its color
        for (const [segNumStr, mask] of Object.entries(frames)) {
            const segNum = parseInt(segNumStr);
            const rgb = colorMap[segNum];
            if (!rgb) continue;

            const [r, g, b] = rgb;

            for (let i = 0; i < mask.length; i++) {
                if (mask[i] === 1) {
                    const pixelIndex = i * 4;
                    // Blend with original pixel using alpha
                    data[pixelIndex] = Math.round(data[pixelIndex] * (1 - SEGMENTATION_OPACITY) + r * SEGMENTATION_OPACITY);
                    data[pixelIndex + 1] = Math.round(data[pixelIndex + 1] * (1 - SEGMENTATION_OPACITY) + g * SEGMENTATION_OPACITY);
                    data[pixelIndex + 2] = Math.round(data[pixelIndex + 2] * (1 - SEGMENTATION_OPACITY) + b * SEGMENTATION_OPACITY);
                }
            }
        }

        ctx.putImageData(imageData, 0, 0);
    }

    /**
     * Format tooltip text for a tile
     */
    function formatTooltip(tileData) {
        const parts = [
            tileData.modality,
            tileData.body_part !== 'UNKNOWN' ? tileData.body_part : null,
            tileData.collection,
        ].filter(Boolean);

        let tooltip = parts.join(' | ');

        // Add segmentation info if available
        if (tileData.segmentation) {
            const segCount = tileData.segmentation.segments.length;
            tooltip += ` | ${segCount} segments`;
        }

        return tooltip;
    }

    /**
     * Update the stats display
     */
    function updateStats(showing, total, hasSegmentations) {
        if (statsElement) {
            let text = `Showing ${showing} of ${total} tiles`;
            if (hasSegmentations) {
                text += ' (with TotalSegmentator overlays)';
            }
            statsElement.textContent = text;
        }
    }

    /**
     * Update URL with current grid size
     */
    function updateURL() {
        const url = new URL(window.location);
        url.searchParams.set('cols', currentGridSize);
        window.history.replaceState({}, '', url);
    }

    /**
     * Hide loading indicator
     */
    function hideLoading() {
        if (loadingElement) {
            loadingElement.classList.add('hidden');
        }
    }

    /**
     * Show error message
     */
    function showError(message) {
        if (errorElement) {
            errorElement.textContent = message;
            errorElement.style.display = 'block';
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
