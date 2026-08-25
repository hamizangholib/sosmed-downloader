package com.xsaintz.saveflowhelper

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.webkit.CookieManager
import android.widget.Toast

class MediaDownloadManager(private val context: Context) {

    fun downloadMedia(candidate: MediaCandidate) {
        if (candidate.type == MediaType.HLS) {
            Toast.makeText(context, "HLS stream requires external tool to merge.", Toast.LENGTH_LONG).show()
            return
        }
        
        if (candidate.url.startsWith("blob:")) {
            Toast.makeText(context, "Blob URL cannot be downloaded directly. We couldn't find the underlying media request.", Toast.LENGTH_LONG).show()
            return
        }

        try {
            val request = DownloadManager.Request(Uri.parse(candidate.url))
            request.setTitle(candidate.filename ?: "Media Download")
            request.setDescription("Downloading from Saveflow Helper")
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            
            // Set Destination
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, candidate.filename ?: "downloaded_media")
            
            // Add Referer and User Agent
            if (candidate.referer.isNotBlank()) {
                request.addRequestHeader("Referer", candidate.referer)
            }
            // A common user agent to prevent basic blocks
            request.addRequestHeader("User-Agent", "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")
            
            // Add Cookies from CookieManager
            val cookies = CookieManager.getInstance().getCookie(candidate.url)
            if (!cookies.isNullOrBlank()) {
                request.addRequestHeader("Cookie", cookies)
            }
            
            val downloadManager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            downloadManager.enqueue(request)
            
            Toast.makeText(context, "Download started: ${candidate.filename}", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(context, "Error starting download: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }
}
