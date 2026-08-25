package com.xsaintz.saveflowhelper

import android.annotation.SuppressLint
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.tabs.TabLayout
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var webContainer: FrameLayout
    private lateinit var etUrl: EditText
    private lateinit var btnGo: Button
    private lateinit var btnPaste: Button
    private lateinit var btnBack: Button
    private lateinit var btnClear: Button
    private lateinit var tvStatus: TextView
    private lateinit var tvEmpty: TextView
    private lateinit var progressBar: ProgressBar
    private lateinit var tabLayout: TabLayout
    private lateinit var recyclerView: RecyclerView

    private lateinit var mediaDetector: MediaDetector
    private lateinit var mediaAdapter: MediaAdapter
    private lateinit var downloadManager: MediaDownloadManager
    private val saveflowApiClient = SaveflowApiClient()

    private var pollingJob: Job? = null
    private var extractionJob: Job? = null
    private var isScannerInjected = false
    private var scannerJsCode = ""

    private var currentTab = 0 // 0 = Video, 1 = Photo

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        initViews()
        setupMediaSystem()
        setupWebView()
        handleIntent(intent)
        
        loadScannerJs()
    }

    private fun initViews() {
        webView = findViewById(R.id.webView)
        webContainer = findViewById(R.id.webContainer)
        etUrl = findViewById(R.id.etUrl)
        btnGo = findViewById(R.id.btnGo)
        btnPaste = findViewById(R.id.btnPaste)
        btnBack = findViewById(R.id.btnBack)
        btnClear = findViewById(R.id.btnClear)
        tvStatus = findViewById(R.id.tvStatus)
        tvEmpty = findViewById(R.id.tvEmpty)
        progressBar = findViewById(R.id.progressBar)
        tabLayout = findViewById(R.id.tabLayout)
        recyclerView = findViewById(R.id.recyclerView)

        tabLayout.addTab(tabLayout.newTab().setText("Video (0)"))
        tabLayout.addTab(tabLayout.newTab().setText("Photo (0)"))
        
        tabLayout.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab?) {
                currentTab = tab?.position ?: 0
                updateListUI()
            }
            override fun onTabUnselected(tab: TabLayout.Tab?) {}
            override fun onTabReselected(tab: TabLayout.Tab?) {}
        })

        btnGo.setOnClickListener {
            val url = etUrl.text.toString().trim()
            if (UrlValidator.isValidUrl(url)) {
                detectMedia(url)
            } else {
                Toast.makeText(this, "Invalid URL. Only http/https allowed.", Toast.LENGTH_SHORT).show()
            }
        }

        btnPaste.setOnClickListener {
            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val text = clipboard.primaryClip?.getItemAt(0)?.text?.toString()
            if (!text.isNullOrBlank()) {
                etUrl.setText(text)
            }
        }

        btnBack.setOnClickListener {
            if (webView.canGoBack()) {
                webView.goBack()
            }
        }

        btnClear.setOnClickListener {
            mediaDetector.clear()
            tvEmpty.text = "Tempel link lalu tekan OPEN & DETECT."
            showInteractiveBrowser(false)
            webView.loadUrl("about:blank")
            CookieManager.getInstance().removeAllCookies(null)
            CookieManager.getInstance().flush()
            Toast.makeText(this, "Cookies & Results Cleared", Toast.LENGTH_SHORT).show()
        }
    }

    private fun detectMedia(url: String) {
        extractionJob?.cancel()
        showInteractiveBrowser(false)
        webView.stopLoading()
        webView.loadUrl("about:blank")
        isScannerInjected = false
        mediaDetector.clear()
        btnGo.isEnabled = false
        btnGo.text = "CHECKING..."
        tvEmpty.text = "Sedang memeriksa media..."
        progressBar.visibility = View.VISIBLE
        tvStatus.text = "Checking Saveflow..."

        extractionJob = CoroutineScope(Dispatchers.Main).launch {
            val candidates = runCatching {
                withContext(Dispatchers.IO) { saveflowApiClient.extract(url) }
            }.onFailure {
                Log.w("MainActivity", "API extraction failed; using WebView fallback", it)
            }.getOrDefault(emptyList())

            btnGo.isEnabled = true
            btnGo.text = "OPEN & DETECT"
            progressBar.visibility = View.GONE
            if (candidates.isNotEmpty()) {
                candidates.forEach(mediaDetector::addCandidate)
                tvStatus.text = "Detected by Saveflow: ${candidates.size}"
            } else {
                tvStatus.text = "Interactive scan"
                tvEmpty.text = "Belum ada media terdeteksi. Tekan play atau berinteraksi dengan halaman di atas."
                showInteractiveBrowser(true)
                Toast.makeText(
                    this@MainActivity,
                    "Server found no media. Opening interactive detector.",
                    Toast.LENGTH_SHORT
                ).show()
                webView.loadUrl(url)
            }
        }
    }

    private fun setupMediaSystem() {
        mediaDetector = MediaDetector { candidates ->
            updateListUI()
            if (webContainer.visibility == View.VISIBLE && candidates.any {
                    it.type == MediaType.VIDEO || it.type == MediaType.HLS
                }) {
                showInteractiveBrowser(false)
            }
        }
        downloadManager = MediaDownloadManager(this)
        mediaAdapter = MediaAdapter(emptyList(), downloadManager)
        recyclerView.layoutManager = LinearLayoutManager(this)
        recyclerView.adapter = mediaAdapter
    }

    private fun showInteractiveBrowser(show: Boolean) {
        webContainer.visibility = if (show) View.VISIBLE else View.GONE
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        // Must disable file access and content access
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            // Let the device keep track of cookies safely via CookieManager
        }

        WebView.setWebContentsDebuggingEnabled(false) // Never enable in release

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                if (newProgress < 100) {
                    progressBar.visibility = View.VISIBLE
                    progressBar.progress = newProgress
                } else {
                    progressBar.visibility = View.GONE
                }
            }
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                btnBack.isEnabled = webView.canGoBack()
                if (url == "about:blank") return
                tvStatus.text = "Loaded: ${android.net.Uri.parse(url ?: "").host}"
                
                // Inject JS payload
                if (scannerJsCode.isNotBlank()) {
                    webView.evaluateJavascript(scannerJsCode, null)
                    isScannerInjected = true
                }
            }

            override fun shouldInterceptRequest(view: WebView?, request: WebResourceRequest?): WebResourceResponse? {
                val url = request?.url?.toString()
                if (url != null) {
                    // Quick network layer detection based on extension
                    if (url.matches(Regex(".*\\.(mp4|webm|m4v|mov)(\\?.*)?(#.*)?$", RegexOption.IGNORE_CASE))) {
                        mediaDetector.addCandidate(MediaCandidate(url, MediaType.VIDEO, "NETWORK", view?.url ?: ""))
                    } else if (url.matches(Regex(".*\\.(jpg|jpeg|png|webp|gif|avif)(\\?.*)?(#.*)?$", RegexOption.IGNORE_CASE))) {
                        mediaDetector.addCandidate(MediaCandidate(url, MediaType.PHOTO, "NETWORK", view?.url ?: ""))
                    } else if (url.matches(Regex(".*\\.m3u8(\\?.*)?(#.*)?$", RegexOption.IGNORE_CASE))) {
                        mediaDetector.addCandidate(MediaCandidate(url, MediaType.HLS, "NETWORK", view?.url ?: ""))
                    }
                }
                return super.shouldInterceptRequest(view, request)
            }
        }
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            val text = intent.getStringExtra(Intent.EXTRA_TEXT)
            if (text != null && UrlValidator.isValidUrl(text)) {
                etUrl.setText(text)
                // Do not auto start, wait for user confirmation
                Toast.makeText(this, "URL received from share", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun loadScannerJs() {
        try {
            val inputStream = assets.open("media_scanner.js")
            scannerJsCode = inputStream.bufferedReader().use { it.readText() }
        } catch (e: Exception) {
            Log.e("MainActivity", "Error loading JS", e)
        }
    }

    private fun startPolling() {
        pollingJob?.cancel()
        pollingJob = CoroutineScope(Dispatchers.Main).launch {
            while (isActive) {
                if (isScannerInjected) {
                    // Extract payload and empty the array in JS
                    val js = "var res = window.__saveflow_candidates; window.__saveflow_candidates = []; JSON.stringify(res);"
                    webView.evaluateJavascript(js) { result ->
                        if (result != null && result != "null") {
                            // Result from evaluateJavascript is double-quoted JSON string
                            val unescaped = result.removeSurrounding("\"").replace("\\\\\"", "\"").replace("\\\\\\\\", "\\\\")
                            mediaDetector.processJsPayload(unescaped)
                        }
                    }
                }
                delay(2000) // Poll every 2 seconds
            }
        }
    }

    private fun stopPolling() {
        pollingJob?.cancel()
        pollingJob = null
    }

    private fun updateListUI() {
        val videos = mediaDetector.getVideos()
        val photos = mediaDetector.getPhotos()

        tabLayout.getTabAt(0)?.text = "Video (${videos.size})"
        tabLayout.getTabAt(1)?.text = "Photo (${photos.size})"

        val currentList = if (currentTab == 0) videos else photos
        mediaAdapter.submitList(currentList)

        if (currentList.isEmpty()) {
            tvEmpty.visibility = View.VISIBLE
            recyclerView.visibility = View.GONE
        } else {
            tvEmpty.visibility = View.GONE
            recyclerView.visibility = View.VISIBLE
        }
    }

    override fun onResume() {
        super.onResume()
        startPolling()
    }

    override fun onPause() {
        super.onPause()
        stopPolling()
    }

    override fun onDestroy() {
        extractionJob?.cancel()
        super.onDestroy()
    }
}
