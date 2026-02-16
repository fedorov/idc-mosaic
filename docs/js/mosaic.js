/**
 * IDC Mosaic - Interactive tile grid with DICOMweb image loading
 * Supports segmentation overlay rendering and treemap-style layouts
 */

(function () {
    'use strict';

    // Configuration
    const MANIFEST_TYPE_MAP = {
        'diverse': 'diverse',
        'ct-only': 'ct',
        'segmentation': 'ct'
    };
    const FALLBACK_MANIFEST_URLS = {
        'diverse': 'data/manifest_diverse.json',
        'ct-only': 'data/manifest.json',
        'segmentation': 'data/manifest.json'
    };
    const MANIFEST_INDEX_URL = 'data/manifests/index.json';
    const SEGMENTATION_OPACITY = 0.5;

    // DOM elements
    const mosaicGrid = document.getElementById('mosaic');
    const tileCountInput = document.getElementById('tile-count');
    const viewSelect = document.getElementById('view-select');
    const statsElement = document.getElementById('stats');
    const loadingElement = document.getElementById('loading');
    const errorElement = document.getElementById('error');

    // State
    let manifestData = null;
    let citationsData = null;
    let manifestIndex = null;
    let currentTileCount = 64;
    let currentView = 'diverse'; // 'diverse', 'ct-only', or 'segmentation'

    /**
     * Initialize the mosaic
     */
    async function init() {
        const params = new URLSearchParams(window.location.search);

        // Read tile count from URL params
        const tilesParam = params.get('tiles');
        if (tilesParam) {
            const tiles = parseInt(tilesParam, 10);
            if (tiles >= 1) {
                currentTileCount = tiles;
                tileCountInput.value = tiles.toString();
            }
        }

        // Set up tile count change handler
        tileCountInput.addEventListener('change', (e) => {
            const value = parseInt(e.target.value, 10);
            if (value >= 1 && manifestData) {
                currentTileCount = Math.min(value, manifestData.total_tiles);
                tileCountInput.value = currentTileCount;
                updateURL();
                renderMosaic();
            }
        });

        // Set up view toggle handler
        viewSelect.addEventListener('change', async (e) => {
            currentView = e.target.value;
            updateURL();
            await loadAndRender();
        });

        // Read view mode from URL params
        const viewParam = params.get('view');
        if (viewParam && ['diverse', 'ct-only', 'segmentation'].includes(viewParam)) {
            currentView = viewParam;
            viewSelect.value = viewParam;
        }

        // Load manifest and render
        await loadAndRender();
    }

    /**
     * Load manifest and render mosaic
     */
    async function loadAndRender() {
        showLoading();
        try {
            // Load manifest and citations in parallel
            const [manifest, citations] = await Promise.all([
                loadManifest(),
                loadCitations()
            ]);
            manifestData = manifest;
            citationsData = citations;
            // Update input max value based on available tiles
            tileCountInput.max = manifestData.total_tiles;
            // Clamp current count to available tiles
            if (currentTileCount > manifestData.total_tiles) {
                currentTileCount = manifestData.total_tiles;
                tileCountInput.value = currentTileCount;
            }
            hideLoading();
            renderMosaic();
        } catch (error) {
            hideLoading();
            showError(`Failed to load mosaic data: ${error.message}`);
        }
    }

    /**
     * Load the manifest index (cached after first fetch)
     */
    async function loadManifestIndex() {
        if (manifestIndex !== null) {
            return manifestIndex;
        }
        try {
            const response = await fetch(MANIFEST_INDEX_URL);
            if (!response.ok) {
                console.warn('Manifest index not found, using fallback URLs');
                return null;
            }
            manifestIndex = await response.json();
            return manifestIndex;
        } catch (e) {
            console.warn('Failed to load manifest index:', e);
            return null;
        }
    }

    /**
     * Load a manifest file, randomly selecting from available dated manifests
     */
    async function loadManifest() {
        const index = await loadManifestIndex();
        let url;

        if (index && index.manifests) {
            const manifestType = MANIFEST_TYPE_MAP[currentView] || 'ct';
            const available = index.manifests[manifestType];

            if (available && available.length > 0) {
                const randomIndex = Math.floor(Math.random() * available.length);
                url = 'data/' + available[randomIndex];
            }
        }

        // Fallback to fixed URLs if index unavailable
        if (!url) {
            url = FALLBACK_MANIFEST_URLS[currentView] || FALLBACK_MANIFEST_URLS['segmentation'];
        }

        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
    }

    /**
     * Load the citations.json file
     */
    async function loadCitations() {
        try {
            const response = await fetch('data/citations.json');
            if (!response.ok) {
                console.warn('Citations file not found');
                return {};
            }
            return response.json();
        } catch (e) {
            console.warn('Failed to load citations:', e);
            return {};
        }
    }

    /**
     * Shuffle array using Fisher-Yates algorithm
     */
    function shuffleArray(array) {
        const shuffled = [...array];
        for (let i = shuffled.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
        }
        return shuffled;
    }

    /**
     * Binary Space Partitioning for treemap layout
     * Recursively subdivides a rectangle to place tiles with varying sizes
     */
    function generateTreemapLayout(numTiles, containerWidth, containerHeight) {
        const layouts = [];

        // Assign random weights to tiles (influences relative size)
        const weights = [];
        for (let i = 0; i < numTiles; i++) {
            // Random weight between 1 and 3 for size variation
            weights.push(1 + Math.random() * 2);
        }

        // Recursive subdivision function
        function subdivide(rect, tileIndices) {
            if (tileIndices.length === 0) return;

            if (tileIndices.length === 1) {
                // Base case: single tile fills the rectangle
                layouts.push({
                    index: tileIndices[0],
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height
                });
                return;
            }

            // Calculate total weight for this subset
            const subsetWeight = tileIndices.reduce((sum, idx) => sum + weights[idx], 0);

            // Decide split direction based on aspect ratio
            const isHorizontal = rect.width >= rect.height;

            // Find split point - aim for roughly half the weight
            let splitWeight = 0;
            let splitIndex = 0;
            const targetWeight = subsetWeight / 2;

            // Add randomness to split point (between 30% and 70%)
            const randomOffset = (Math.random() - 0.5) * 0.4 * subsetWeight;
            const adjustedTarget = Math.max(subsetWeight * 0.3, Math.min(subsetWeight * 0.7, targetWeight + randomOffset));

            for (let i = 0; i < tileIndices.length; i++) {
                splitWeight += weights[tileIndices[i]];
                if (splitWeight >= adjustedTarget) {
                    splitIndex = i + 1;
                    break;
                }
            }

            // Ensure we split at least one tile to each side
            splitIndex = Math.max(1, Math.min(tileIndices.length - 1, splitIndex));

            const firstHalf = tileIndices.slice(0, splitIndex);
            const secondHalf = tileIndices.slice(splitIndex);

            const firstWeight = firstHalf.reduce((sum, idx) => sum + weights[idx], 0);
            const ratio = firstWeight / subsetWeight;

            if (isHorizontal) {
                const splitX = rect.x + rect.width * ratio;
                subdivide({ x: rect.x, y: rect.y, width: rect.width * ratio, height: rect.height }, firstHalf);
                subdivide({ x: splitX, y: rect.y, width: rect.width * (1 - ratio), height: rect.height }, secondHalf);
            } else {
                const splitY = rect.y + rect.height * ratio;
                subdivide({ x: rect.x, y: rect.y, width: rect.width, height: rect.height * ratio }, firstHalf);
                subdivide({ x: rect.x, y: splitY, width: rect.width, height: rect.height * (1 - ratio) }, secondHalf);
            }
        }

        // Start with full container and all tile indices (shuffled for randomness)
        const indices = Array.from({ length: numTiles }, (_, i) => i);
        const shuffledIndices = shuffleArray(indices);

        subdivide(
            { x: 0, y: 0, width: containerWidth, height: containerHeight },
            shuffledIndices
        );

        return layouts;
    }

    /**
     * Render the mosaic with treemap layout
     */
    function renderMosaic() {
        if (!manifestData || !manifestData.tiles) {
            return;
        }

        // Clear existing tiles
        mosaicGrid.innerHTML = '';

        // Get container dimensions
        const containerWidth = mosaicGrid.clientWidth || 1400;
        // Calculate height based on a pleasing aspect ratio (roughly 4:3 or 16:9)
        const containerHeight = Math.min(containerWidth * 0.75, window.innerHeight * 0.8);

        // Set container height
        mosaicGrid.style.height = containerHeight + 'px';

        // Randomly select tiles from the manifest
        const shuffledTiles = shuffleArray(manifestData.tiles);
        const tilesToShow = shuffledTiles.slice(0, Math.min(currentTileCount, manifestData.total_tiles));

        // Generate treemap layout
        const layouts = generateTreemapLayout(tilesToShow.length, containerWidth, containerHeight);

        // Update stats
        const showSegs = currentView === 'segmentation' && manifestData.has_segmentations;
        updateStats(tilesToShow.length, manifestData.total_tiles, showSegs, currentView);

        // Create tiles with absolute positioning
        layouts.forEach((layout) => {
            const tileData = tilesToShow[layout.index];
            const tile = createTile(tileData, layout);
            mosaicGrid.appendChild(tile);
        });
    }

    /**
     * Create a single tile element with treemap positioning
     */
    function createTile(tileData, layout) {
        const tile = document.createElement('div');
        tile.className = 'tile loading';
        tile.setAttribute('role', 'button');
        tile.setAttribute('tabindex', '0');
        tile.title = formatTooltip(tileData);

        // Apply treemap positioning
        tile.style.position = 'absolute';
        tile.style.left = layout.x + 'px';
        tile.style.top = layout.y + 'px';
        tile.style.width = layout.width + 'px';
        tile.style.height = layout.height + 'px';

        // Create canvas for rendering (needed for segmentation overlay)
        const canvas = document.createElement('canvas');
        canvas.className = 'tile-canvas';
        tile.appendChild(canvas);

        // Add info icon for citation (if tile or its segmentation has a DOI)
        const hasSourceDoi = !!tileData.source_doi;
        const hasSegDoi = !!(tileData.segmentation && tileData.segmentation.source_doi);
        if (hasSourceDoi || hasSegDoi) {
            const infoIcon = document.createElement('button');
            infoIcon.className = 'info-icon';
            infoIcon.innerHTML = 'i';
            infoIcon.title = 'View citation';
            infoIcon.setAttribute('aria-label', 'View citation for this image');
            infoIcon.addEventListener('click', (e) => {
                e.stopPropagation();
                showCitationPopup(tileData);
            });
            tile.appendChild(infoIcon);
        }

        // Load image and optionally segmentation
        const showSegs = currentView === 'segmentation';
        if (showSegs && tileData.segmentation && manifestData.dicomweb_base_url) {
            loadTileWithSegmentation(tile, canvas, tileData, manifestData.dicomweb_base_url);
        } else {
            loadTileImage(tile, canvas, tileData.tile_url);
        }

        // Click handler - open IDC viewer
        // Use segmentation viewer URL when showing segmentations
        const openViewer = () => {
            let viewerUrl = tileData.viewer_url;
            if (showSegs && tileData.segmentation && tileData.segmentation.viewer_url) {
                viewerUrl = tileData.segmentation.viewer_url;
            }
            window.open(viewerUrl, '_blank', 'noopener');
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
     * Render a single citation block (DOI link, APA, BibTeX) into a container
     */
    function renderCitationBlock(container, doi, label) {
        const citation = citationsData[doi] || {
            doi: doi,
            url: `https://doi.org/${doi}`,
            apa: null,
            bibtex: null
        };

        if (label) {
            const heading = document.createElement('div');
            heading.className = 'citation-label';
            heading.style.fontWeight = 'bold';
            heading.style.marginTop = '0.8em';
            heading.textContent = label;
            container.appendChild(heading);
        }

        // DOI link
        const doiLink = document.createElement('p');
        doiLink.className = 'citation-doi';
        doiLink.innerHTML = `DOI: <a href="${citation.url}" target="_blank" rel="noopener">${doi}</a>`;
        container.appendChild(doiLink);

        // APA citation
        if (citation.apa) {
            const apaSection = document.createElement('div');
            apaSection.className = 'citation-section';
            apaSection.innerHTML = `
                <div class="citation-label">APA Citation:</div>
                <div class="citation-text">${citation.apa}</div>
                <button class="copy-btn" data-text="${escapeHtml(citation.apa)}">Copy</button>
            `;
            container.appendChild(apaSection);
        }

        // BibTeX citation
        if (citation.bibtex) {
            const bibtexSection = document.createElement('div');
            bibtexSection.className = 'citation-section';
            bibtexSection.innerHTML = `
                <div class="citation-label">BibTeX:</div>
                <pre class="citation-bibtex">${escapeHtml(citation.bibtex)}</pre>
                <button class="copy-btn" data-text="${escapeHtml(citation.bibtex)}">Copy</button>
            `;
            container.appendChild(bibtexSection);
        }

        // If no formatted citation available, show DOI only message
        if (!citation.apa && !citation.bibtex) {
            const note = document.createElement('p');
            note.className = 'citation-note';
            note.textContent = 'Click the DOI link above to view the full citation.';
            container.appendChild(note);
        }
    }

    /**
     * Show citation popup for a tile
     */
    function showCitationPopup(tileData) {
        // Remove any existing popup
        closeCitationPopup();

        const sourceDoi = tileData.source_doi;
        const segDoi = tileData.segmentation ? tileData.segmentation.source_doi : null;

        // Create overlay
        const overlay = document.createElement('div');
        overlay.className = 'citation-overlay';
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                closeCitationPopup();
            }
        });

        // Create popup
        const popup = document.createElement('div');
        popup.className = 'citation-popup';

        // Header
        const header = document.createElement('div');
        header.className = 'citation-header';
        header.innerHTML = `
            <h3>Citations</h3>
            <button class="close-btn" aria-label="Close">&times;</button>
        `;
        header.querySelector('.close-btn').addEventListener('click', closeCitationPopup);
        popup.appendChild(header);

        // Content
        const content = document.createElement('div');
        content.className = 'citation-content';

        // Collection info
        const info = document.createElement('p');
        info.className = 'citation-info';
        info.textContent = `${tileData.modality} | ${tileData.collection}`;
        content.appendChild(info);

        // Use labels when both source and segmentation DOIs are present
        const hasBoth = sourceDoi && segDoi && sourceDoi !== segDoi;

        if (sourceDoi) {
            renderCitationBlock(content, sourceDoi, hasBoth ? 'Source Image Collection' : null);
        }

        if (segDoi && segDoi !== sourceDoi) {
            renderCitationBlock(content, segDoi, hasBoth ? 'Segmentation Analysis Collection' : null);
        }

        popup.appendChild(content);

        // Add copy button handlers
        popup.querySelectorAll('.copy-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const text = btn.getAttribute('data-text');
                navigator.clipboard.writeText(text).then(() => {
                    btn.textContent = 'Copied!';
                    setTimeout(() => {
                        btn.textContent = 'Copy';
                    }, 2000);
                });
            });
        });

        overlay.appendChild(popup);
        document.body.appendChild(overlay);

        // Close on escape key
        document.addEventListener('keydown', handleEscapeKey);
    }

    /**
     * Close the citation popup
     */
    function closeCitationPopup() {
        const overlay = document.querySelector('.citation-overlay');
        if (overlay) {
            overlay.remove();
        }
        document.removeEventListener('keydown', handleEscapeKey);
    }

    /**
     * Handle escape key to close popup
     */
    function handleEscapeKey(e) {
        if (e.key === 'Escape') {
            closeCitationPopup();
        }
    }

    /**
     * Escape HTML for safe insertion
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
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
    function updateStats(showing, total, hasSegmentations, view) {
        if (statsElement) {
            let text = `Showing ${showing} of ${total} tiles`;
            if (view === 'diverse') {
                text += ' (diverse modalities)';
            } else if (hasSegmentations) {
                text += ' (with TotalSegmentator overlays)';
            } else {
                text += ' (CT only)';
            }
            if (manifestData && manifestData.generated) {
                const date = new Date(manifestData.generated);
                text += ` | Generated ${date.toLocaleDateString()}`;
            }
            statsElement.textContent = text;
        }
    }

    /**
     * Update URL with current tile count and view mode
     */
    function updateURL() {
        const url = new URL(window.location);
        url.searchParams.set('tiles', currentTileCount);
        url.searchParams.set('view', currentView);
        window.history.replaceState({}, '', url);
    }

    /**
     * Show loading indicator
     */
    function showLoading() {
        if (loadingElement) {
            loadingElement.classList.remove('hidden');
        }
        if (errorElement) {
            errorElement.style.display = 'none';
        }
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
