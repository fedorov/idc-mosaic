/**
 * IDC Mosaic - Interactive tile grid with DICOMweb image loading
 */

(function () {
    'use strict';

    // Configuration
    const MANIFEST_URL = 'data/manifest.json';

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
        updateStats(tilesToShow.length, manifestData.total_tiles);

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

        // Load image
        loadTileImage(tile, tileData.tile_url);

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
     * Load tile image from DICOMweb
     */
    function loadTileImage(tile, url) {
        const img = new Image();

        img.onload = () => {
            tile.style.backgroundImage = `url("${url}")`;
            tile.classList.remove('loading');
        };

        img.onerror = () => {
            tile.classList.remove('loading');
            tile.classList.add('error');
        };

        img.src = url;
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

        return parts.join(' | ');
    }

    /**
     * Update the stats display
     */
    function updateStats(showing, total) {
        if (statsElement) {
            statsElement.textContent = `Showing ${showing} of ${total} tiles`;
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
