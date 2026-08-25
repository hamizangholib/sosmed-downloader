(function() {
    if (window.__saveflow_scanner_injected) return;
    window.__saveflow_scanner_injected = true;

    window.__saveflow_candidates = [];

    function addCandidate(url, type, source, meta) {
        if (!url) return;
        // Make absolute URL
        try {
            url = new URL(url, document.baseURI).href;
        } catch (e) {
            return;
        }
        
        // Exclude data and blob for direct extraction, unless it's a blob we can't handle, but we just pass it to Android.
        // The prompt says: "Untuk blob URL: Jangan mencoba mengirim blob: ke DownloadManager. Cari request media dasar... Jika tidak ditemukan, tampilkan penjelasan"
        
        window.__saveflow_candidates.push({
            url: url,
            type: type,
            source: source,
            referer: window.location.href,
            meta: meta || {}
        });
    }

    function scanElement(el) {
        if (!el || !el.tagName) return;
        const tag = el.tagName.toLowerCase();

        if (tag === 'video') {
            if (el.src) addCandidate(el.src, 'VIDEO', 'DOM_VIDEO', { poster: el.poster });
            if (el.currentSrc) addCandidate(el.currentSrc, 'VIDEO', 'DOM_VIDEO', { poster: el.poster });
        } else if (tag === 'source') {
            if (el.src) {
                let type = 'UNKNOWN';
                if (el.type && el.type.includes('video')) type = 'VIDEO';
                else if (el.type && el.type.includes('image')) type = 'PHOTO';
                else if (el.src.match(/\.(mp4|webm|m4v|mov)$/i)) type = 'VIDEO';
                
                let parent = el.parentElement;
                let poster = parent && parent.tagName.toLowerCase() === 'video' ? parent.poster : null;
                addCandidate(el.src, type, 'DOM_SOURCE', { poster: poster, mimeType: el.type });
            }
        } else if (tag === 'img') {
            if (el.src) addCandidate(el.src, 'PHOTO', 'DOM_IMAGE', { mimeType: 'image/*' });
            if (el.currentSrc) addCandidate(el.currentSrc, 'PHOTO', 'DOM_IMAGE', { mimeType: 'image/*' });
            if (el.srcset) {
                // very basic srcset parsing, just take the first url
                let parts = el.srcset.split(',');
                if (parts.length > 0) {
                    let firstUrl = parts[0].trim().split(' ')[0];
                    if (firstUrl) addCandidate(firstUrl, 'PHOTO', 'DOM_IMAGE', { mimeType: 'image/*' });
                }
            }
        } else if (tag === 'a') {
            if (el.href && el.href.match(/\.(mp4|webm|m4v|mov|jpg|jpeg|png|webp|gif|avif|m3u8)(\?.*)?(#.*)?$/i)) {
                let type = el.href.includes('.m3u8') ? 'HLS' : (el.href.match(/\.(mp4|webm|m4v|mov)$/i) ? 'VIDEO' : 'PHOTO');
                addCandidate(el.href, type, 'DOM_LINK', {});
            }
        }

        // Check background image
        try {
            let bg = window.getComputedStyle(el).backgroundImage;
            if (bg && bg !== 'none' && bg.startsWith('url(')) {
                let url = bg.slice(4, -1).replace(/["']/g, "");
                if (url && !url.startsWith('data:')) {
                    addCandidate(url, 'PHOTO', 'DOM_CSS_BG', {});
                }
            }
        } catch (e) {}
    }

    function scanMeta() {
        let metas = document.querySelectorAll('meta');
        metas.forEach(m => {
            let prop = m.getAttribute('property') || m.getAttribute('name');
            let content = m.getAttribute('content');
            if (prop && content) {
                if (prop === 'og:video' || prop === 'twitter:player') {
                    addCandidate(content, 'VIDEO', 'META', {});
                } else if (prop === 'og:image' || prop === 'twitter:image') {
                    addCandidate(content, 'PHOTO', 'META', {});
                }
            }
        });
    }

    function scanPerformance() {
        if (window.performance && window.performance.getEntriesByType) {
            let entries = window.performance.getEntriesByType('resource');
            entries.forEach(e => {
                if (e.name.match(/\.(mp4|webm|m4v|mov)(\?.*)?(#.*)?$/i)) {
                    addCandidate(e.name, 'VIDEO', 'PERFORMANCE', {});
                } else if (e.name.match(/\.(jpg|jpeg|png|webp|gif|avif)(\?.*)?(#.*)?$/i)) {
                    addCandidate(e.name, 'PHOTO', 'PERFORMANCE', {});
                } else if (e.name.match(/\.m3u8(\?.*)?(#.*)?$/i)) {
                    addCandidate(e.name, 'HLS', 'PERFORMANCE', {});
                }
            });
        }
    }

    // Initial scan
    scanMeta();
    scanPerformance();
    document.querySelectorAll('*').forEach(scanElement);

    // Mutation observer for dynamic content
    const observer = new MutationObserver(mutations => {
        mutations.forEach(mutation => {
            mutation.addedNodes.forEach(node => {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    scanElement(node);
                    node.querySelectorAll('*').forEach(scanElement);
                }
            });
            if (mutation.type === 'attributes') {
                scanElement(mutation.target);
            }
        });
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['src', 'href', 'srcset', 'style', 'class']
    });

})();
